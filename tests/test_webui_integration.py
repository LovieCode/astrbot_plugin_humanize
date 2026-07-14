from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from humanize.config import PluginConfig
from humanize.repositories.sqlite import SQLiteRepository
from humanize.services.control import ControlService
from humanize.web.routes import WebApi


class _FakeRequest:
    """Provide the request attributes consumed by the plugin Web API."""

    def __init__(
        self,
        method: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> None:
        self.method = method
        self.query = query or {}
        self._body = body

    async def json(self, default: Any = None) -> Any:
        """Return the configured JSON body.

        Args:
            default: Fallback value used when no body was configured.

        Returns:
            The configured body or the supplied fallback.
        """
        return default if self._body is None else self._body


def _response_payload(response: Any) -> dict[str, Any]:
    """Decode a Starlette JSON response.

    Args:
        response: Response returned by the Web API dispatcher.

    Returns:
        The decoded JSON object.
    """
    return json.loads(response.body.decode("utf-8"))


def test_control_sections_persist_in_one_humanize_database(tmp_path: Path) -> None:
    """All non-relationship controls survive reopening one shared database."""

    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        service = ControlService(repository)

        await service.update_section(
            "persona",
            {
                "name": "眠汐",
                "identity": "负责长期交互一致性的助手",
                "traits": ["冷静", "直接"],
                "values": ["可靠"],
                "boundaries": ["不冒充真人"],
            },
        )
        await service.update_section(
            "state",
            {
                "mood": 0.81,
                "energy": 0.72,
                "interest": 0.93,
                "stress": 0.17,
                "focus": "WebUI integration",
            },
        )
        await service.update_section(
            "behavior",
            {
                "enabled": True,
                "allow_no_reply": False,
                "allow_follow_up": True,
                "allow_proactive": True,
                "allow_end_topic": False,
                "reply_threshold": 0.42,
                "follow_up_threshold": 0.61,
                "proactive_threshold": 0.79,
                "end_topic_threshold": 0.88,
                "cooldown_minutes": 16,
            },
        )
        await service.update_section(
            "expression",
            {
                "enabled": True,
                "provider": "astrbot_plugin_style_learner",
                "mode": "inject",
                "profile": "daily-natural",
            },
        )

        reopened = SQLiteRepository(db_path)
        await reopened.initialize()
        features = await ControlService(reopened).get_features()

        assert features["persona"]["name"] == "眠汐"
        assert features["state"]["focus"] == "WebUI integration"
        assert features["behavior"]["allow_no_reply"] is False
        assert features["behavior"]["allow_proactive"] is True
        assert features["behavior"]["allow_end_topic"] is False
        assert features["behavior"]["cooldown_minutes"] == 16
        assert features["expression"]["enabled"] is True
        assert features["expression"]["mode"] == "inject"
        assert features["expression"]["profile"] == "daily-natural"
        assert features["audit_meta"]["total"] == 4
        assert len(features["audit"]) == 4

        assert list(tmp_path.glob("*.db")) == [db_path]
        with sqlite3.connect(db_path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert {
            "humanize_persona",
            "humanize_state",
            "humanize_behavior_policy",
            "humanize_expression",
            "humanize_control_audit",
        } <= tables
        assert not any("relationship" in table.lower() for table in tables)

    asyncio.run(scenario())


def test_web_api_save_reset_and_audit_share_one_repository(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Web routes save, reset, and audit through the same repository instance."""

    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        api = WebApi(repository, PluginConfig(), ControlService(repository))

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={"name": "临时人格", "reason": "integration update"},
            ),
        )
        saved_response = await api.dispatch("persona")
        saved = _response_payload(saved_response)
        assert saved_response.status_code == 200
        assert saved["success"] is True
        assert saved["data"]["name"] == "临时人格"

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={"section": "persona", "reason": "integration reset"},
            ),
        )
        reset_response = await api.dispatch("control/reset")
        reset = _response_payload(reset_response)
        assert reset_response.status_code == 200
        assert reset["data"]["reset"] == ["persona"]
        assert reset["data"]["sections"]["persona"]["name"] == "小助手"

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "1", "page_size": "10"}),
        )
        audit_response = await api.dispatch("control-audit")
        audit = _response_payload(audit_response)["data"]
        assert audit_response.status_code == 200
        assert audit["total"] == 2
        assert [item["action"] for item in audit["items"]] == ["reset", "update"]
        assert audit["items"][0]["reason"] == "integration reset"
        assert audit["items"][1]["reason"] == "integration update"

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        features_response = await api.dispatch("features")
        features = _response_payload(features_response)["data"]
        assert set(features) == {
            "persona",
            "state",
            "behavior",
            "expression",
            "audit",
            "audit_meta",
        }
        assert features["audit_meta"]["total"] == 2
        assert len(features["audit"]) == 2
        assert list(tmp_path.glob("*.db")) == [db_path]

    asyncio.run(scenario())


def test_web_api_returns_bounded_public_errors(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Invalid and failed requests expose stable status codes without internals."""

    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        api = WebApi(repository, PluginConfig(), ControlService(repository))

        cases = [
            (
                _FakeRequest("POST", body=["not", "an", "object"]),
                "persona",
                400,
                "请求体必须是 JSON 对象",
            ),
            (
                _FakeRequest("POST", body={"mode": "invalid"}),
                "expression",
                400,
                "expression mode",
            ),
            (
                _FakeRequest("POST", body={"section": "relationships"}),
                "control/reset",
                400,
                "unsupported control section",
            ),
            (_FakeRequest("GET"), "missing", 404, "未找到该接口"),
            (_FakeRequest("PATCH"), "persona", 405, "不支持的请求方法"),
        ]
        for request, path, status_code, message in cases:
            monkeypatch.setattr(astrbot_web, "request", request)
            response = await api.dispatch(path)
            payload = _response_payload(response)
            assert response.status_code == status_code
            assert payload["status"] == "error"
            assert message in payload["message"]

        class BrokenControl:
            """Raise an internal error from the feature overview."""

            async def get_features(self) -> dict[str, Any]:
                """Simulate a storage failure.

                Raises:
                    RuntimeError: Always raised to exercise the 500 boundary.
                """
                raise RuntimeError("database password and internal path")

        broken_api = WebApi(
            repository,
            PluginConfig(),
            BrokenControl(),  # type: ignore[arg-type]
        )
        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        response = await broken_api.dispatch("features")
        payload = _response_payload(response)
        assert response.status_code == 500
        assert payload == {
            "status": "error",
            "message": "插件内部错误",
            "data": None,
        }
        assert "password" not in response.body.decode("utf-8")

    asyncio.run(scenario())
