"""Group policy storage tests (WebUI 群聊策略页的后端存储)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.web.routes import WebApi


async def _repository(db_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


def test_group_policy_upsert_list_and_clear(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")

        assert await repository.list_group_policies() == []

        await repository.set_group_policy_mode(scope_id="global", mode="full")
        await repository.set_group_policy_mode(scope_id="100", mode="silent")
        rows = {
            row["scope_id"]: row["mode"]
            for row in await repository.list_group_policies()
        }
        assert rows == {"global": "full", "100": "silent"}

        # 同一行重复设置只改 mode，不产生重复条目。
        await repository.set_group_policy_mode(scope_id="100", mode="mention")
        rows = {
            row["scope_id"]: row["mode"]
            for row in await repository.list_group_policies()
        }
        assert rows == {"global": "full", "100": "mention"}

        # 非法模式与空会话标识直接拒绝。
        with pytest.raises(ValueError):
            await repository.set_group_policy_mode(scope_id="100", mode="yolo")
        with pytest.raises(ValueError):
            await repository.set_group_policy_mode(scope_id="  ", mode="full")

        await repository.clear_group_policy(scope_id="100")
        rows = {
            row["scope_id"]: row["mode"]
            for row in await repository.list_group_policies()
        }
        assert rows == {"global": "full"}
        with pytest.raises(ValueError):
            await repository.clear_group_policy(scope_id="global")

    asyncio.run(scenario())


def test_session_meta_remembers_group_names(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")

        assert await repository.list_known_sessions() == []

        await repository.remember_session(
            scope_id="aiocqhttp:GroupMessage:100", display_name="摸鱼基地"
        )
        await repository.remember_session(
            scope_id="aiocqhttp:GroupMessage:200", display_name="紫苑"
        )
        await repository.remember_session(
            scope_id="aiocqhttp:GroupMessage:100", display_name="摸鱼基地（新）"
        )

        sessions = {
            row["scope_id"]: row["display_name"]
            for row in await repository.list_known_sessions()
        }
        assert sessions == {
            "aiocqhttp:GroupMessage:100": "摸鱼基地（新）",
            "aiocqhttp:GroupMessage:200": "紫苑",
        }

        # 空标识不落行。
        await repository.remember_session(scope_id="", display_name="x")
        assert len(await repository.list_known_sessions()) == 2

    asyncio.run(scenario())


class _FakeRequest:
    """astrbot.api.web.request 的最小替身：GET 带 query，POST 带 json 体。"""

    def __init__(self, *, method: str = "GET", query: Any = None, body: Any = None):
        self.method = method
        self.query = query if query is not None else {}
        self._body = body

    async def json(self, default=None):
        return self._body if self._body is not None else (default or {})


def test_webapi_policy_endpoints_roundtrip(tmp_path: Path) -> None:
    async def scenario() -> None:
        import astrbot.api.web as web

        repository = await _repository(tmp_path / "humanize.db")
        api = WebApi(repository, PluginConfig())

        def dispatch(fake: _FakeRequest, subpath: str = ""):
            async def run():
                original = web.request
                web.request = fake
                try:
                    return await api.dispatch(subpath)
                finally:
                    web.request = original

            return run()

        def payload(result):
            import json

            return json.loads(result.body)

        # 初始：无行 → 代码默认 mention，无覆盖、无已知会话。
        response = await dispatch(_FakeRequest(method="GET"), "policy")
        assert payload(response)["data"]["global_mode"] == "mention"
        assert payload(response)["data"]["groups"] == []
        assert payload(response)["data"]["known_sessions"] == []

        # 设置全局默认 + 按群覆盖。
        await dispatch(
            _FakeRequest(
                method="POST",
                body={"scope_id": "global", "mode": "full"},
            ),
            "policy-set",
        )
        await dispatch(
            _FakeRequest(
                method="POST",
                body={"scope_id": "123456", "mode": "silent"},
            ),
            "policy-set",
        )
        await repository.remember_session(
            scope_id="aiocqhttp:GroupMessage:123456", display_name="摸鱼基地"
        )

        response = await dispatch(_FakeRequest(method="GET"), "policy")
        data = payload(response)["data"]
        assert data["global_mode"] == "full"
        assert data["groups"] == [
            {
                "scope_id": "123456",
                "mode": "silent",
                "display_name": "摸鱼基地",
                "updated_at": data["groups"][0]["updated_at"],
            }
        ]
        assert data["known_sessions"] == [
            {"scope_id": "aiocqhttp:GroupMessage:123456", "display_name": "摸鱼基地"}
        ]

        # 非法模式报 400；清除覆盖后回退全局默认。
        bad = await dispatch(
            _FakeRequest(
                method="POST",
                body={"scope_id": "123456", "mode": "yolo"},
            ),
            "policy-set",
        )
        assert bad.status_code == 400

        await dispatch(
            _FakeRequest(method="POST", body={"scope_id": "123456"}),
            "policy-clear",
        )
        response = await dispatch(_FakeRequest(method="GET"), "policy")
        assert payload(response)["data"]["groups"] == []

        # 清除 global 被拒绝。
        bad = await dispatch(
            _FakeRequest(method="POST", body={"scope_id": "global"}),
            "policy-clear",
        )
        assert bad.status_code == 400

    asyncio.run(scenario())
