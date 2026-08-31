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


def test_group_speak_probability_set_clear_and_validate(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")

        # 未设置时读取为 None（提示里不注入期望语句）。
        await repository.set_group_policy_mode(scope_id="global", mode="full")
        rows = await repository.list_group_policies()
        assert rows[0]["speak_probability"] is None

        # 设置 / 更新 / 清除；mode 始终保持不动。
        await repository.set_group_speak_probability(scope_id="global", probability=35)
        await repository.set_group_speak_probability(scope_id="100", probability=80)
        rows = {row["scope_id"]: row for row in await repository.list_group_policies()}
        assert rows["global"]["speak_probability"] == 35
        assert rows["100"]["speak_probability"] == 80
        assert rows["100"]["mode"] == "mention"

        await repository.set_group_speak_probability(scope_id="global", probability=60)
        await repository.set_group_speak_probability(
            scope_id="global", probability=None
        )
        rows = {row["scope_id"]: row for row in await repository.list_group_policies()}
        assert rows["global"]["speak_probability"] is None

        # 非法值直接拒绝：0、101、小数、字符串、布尔；空会话标识同样拒绝。
        for bad in (0, 101, 12.5, "35", True):
            with pytest.raises(ValueError):
                await repository.set_group_speak_probability(
                    scope_id="global", probability=bad
                )
        with pytest.raises(ValueError):
            await repository.set_group_speak_probability(scope_id="  ", probability=35)

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


def test_webapi_policy_endpoints_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        import astrbot.api.web as web

        # 配置写入重定向到临时目录，不碰真实 AstrBot 配置。
        import astrbot.core.utils.astrbot_path as astrbot_path

        monkeypatch.setattr(
            astrbot_path, "get_astrbot_config_path", lambda: str(tmp_path)
        )

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

        # 初始：无行 → 代码默认 mention，无覆盖、无已知会话，关键词为空。
        response = await dispatch(_FakeRequest(method="GET"), "policy")
        assert payload(response)["data"]["global_mode"] == "mention"
        assert payload(response)["data"]["groups"] == []
        assert payload(response)["data"]["known_sessions"] == []
        assert payload(response)["data"]["proactive_keywords"] == []

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
        assert data["global_speak_probability"] is None
        assert data["groups"] == [
            {
                "scope_id": "123456",
                "mode": "silent",
                "speak_probability": None,
                "display_name": "摸鱼基地",
                "updated_at": data["groups"][0]["updated_at"],
            }
        ]
        assert data["known_sessions"] == [
            {"scope_id": "aiocqhttp:GroupMessage:123456", "display_name": "摸鱼基地"}
        ]

        # 期望发言概率：随 policy-set 设置与清除，非法值报 400。
        response = await dispatch(
            _FakeRequest(
                method="POST",
                body={
                    "scope_id": "global",
                    "mode": "full",
                    "speak_probability": 40,
                },
            ),
            "policy-set",
        )
        response = await dispatch(_FakeRequest(method="GET"), "policy")
        data = payload(response)["data"]
        assert data["global_speak_probability"] == 40
        await dispatch(
            _FakeRequest(
                method="POST",
                body={
                    "scope_id": "123456",
                    "mode": "silent",
                    "speak_probability": None,
                },
            ),
            "policy-set",
        )
        await repository.set_group_speak_probability(scope_id="123456", probability=70)
        response = await dispatch(_FakeRequest(method="GET"), "policy")
        data = payload(response)["data"]
        assert data["groups"][0]["speak_probability"] == 70
        response = await dispatch(
            _FakeRequest(
                method="POST",
                body={
                    "scope_id": "123456",
                    "mode": "silent",
                    "speak_probability": 101,
                },
            ),
            "policy-set",
        )
        assert response.status_code == 400

        # 触发关键词：保存后读回一致，且同步进内存配置；非法类型报 400。
        response = await dispatch(
            _FakeRequest(
                method="POST",
                body={"proactive_keywords": ["洛薇", "小薇"]},
            ),
            "policy-keywords",
        )
        assert payload(response)["data"]["proactive_keywords"] == ["洛薇", "小薇"]
        assert api._config.proactive_keywords == ("洛薇", "小薇")

        response = await dispatch(_FakeRequest(method="GET"), "policy")
        assert payload(response)["data"]["proactive_keywords"] == ["洛薇", "小薇"]

        bad = await dispatch(
            _FakeRequest(
                method="POST",
                body={"proactive_keywords": "洛薇"},
            ),
            "policy-keywords",
        )
        assert bad.status_code == 400

        # 清空关键词也允许（空列表）。
        response = await dispatch(
            _FakeRequest(method="POST", body={"proactive_keywords": []}),
            "policy-keywords",
        )
        assert payload(response)["data"]["proactive_keywords"] == []
        assert api._config.proactive_keywords == ()

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
