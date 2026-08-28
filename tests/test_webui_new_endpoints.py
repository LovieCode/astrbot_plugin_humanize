from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import (
    ContextSection,
    MessageContext,
    UnknownTerm,
)
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.web.routes import WebApi


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


def _context(request_id: str, scope_id: str = "group-a") -> MessageContext:
    """Build one minimal message context.

    Args:
        request_id: Request identifier used by storage joins.
        scope_id: Scope identifier for the conversation.

    Returns:
        A ready-to-persist message context.
    """
    return MessageContext(
        request_id=request_id,
        scope_type="group",
        scope_id=scope_id,
        message_id=f"msg-{request_id}",
        sender_id="user-1",
        sender_name="小明",
        user_text="测试",
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


def _section() -> ContextSection:
    """Return one minimal context section.

    Returns:
        A context section suitable for recording a context run.
    """
    return ContextSection(
        key="current_message",
        ordinal=0,
        priority=100,
        source_type="message",
        source_refs=("message:msg-1",),
        targets=("prompt",),
        required=True,
        included=True,
        budget_tokens=None,
        estimated_tokens=8,
        applied_tokens=8,
        item_count=1,
        reason="current_user_message",
        content="测试",
    )


@pytest.fixture()
def astrbot_web(monkeypatch: Any):
    """Provide the astrbot.api.web module for request patching.

    Args:
        monkeypatch: pytest monkeypatch fixture.

    Yields:
        The astrbot.api.web module.
    """
    import astrbot.api.web as astrbot_web

    yield astrbot_web


def test_web_api_settings_save_validates_and_persists(
    tmp_path: Path, astrbot_web: Any, monkeypatch: Any
) -> None:
    """Settings save enforces the public whitelist and writes without reload."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        api = WebApi(repository, PluginConfig())

        # Unknown keys are rejected with 400.
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("POST", body={"values": {"bogus_key": 1}}),
        )
        response = await api.dispatch("settings")
        assert response.status_code == 400
        assert "未知配置项" in _response_payload(response)["message"]

        # Wrong value types are rejected with 400.
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("POST", body={"values": {"max_message_chars": "ten"}}),
        )
        response = await api.dispatch("settings")
        assert response.status_code == 400
        assert "类型错误" in _response_payload(response)["message"]

        # Missing values object is rejected.
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("POST", body={}),
        )
        response = await api.dispatch("settings")
        assert response.status_code == 400

        # Valid values persist to the plugin config file without reloading.
        import astrbot.core.utils.astrbot_path as astrbot_path

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        monkeypatch.setattr(
            astrbot_path,
            "get_astrbot_config_path",
            lambda: str(config_dir),
        )
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "values": {
                        "max_message_chars": 12,
                        "admin_name": "新管理员",
                        "memory_enabled": False,
                        "reply_examples_limit": 2,
                    }
                },
            ),
        )
        response = await api.dispatch("settings")
        payload = _response_payload(response)["data"]
        assert response.status_code == 200
        assert set(payload["updated"]) == {
            "max_message_chars",
            "admin_name",
            "memory_enabled",
            "reply_examples_limit",
        }
        assert payload["restart_required"] is True

        # The file is written and reads back through the same AstrBotConfig shape.
        config_file = config_dir / "astrbot_plugin_humanize_config.json"
        assert config_file.is_file()
        stored = json.loads(config_file.read_text(encoding="utf-8-sig"))
        assert stored["general"]["max_message_chars"] == 12
        assert stored["general"]["admin_name"] == "新管理员"
        assert stored["memory"]["memory_enabled"] is False
        assert stored["memory"]["reply_examples"]["reply_examples_limit"] == 2

        # GET settings reflects the saved values immediately (in-memory sync).
        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        settings = _response_payload(await api.dispatch("settings"))["data"]
        assert settings["max_message_chars"] == 12
        assert settings["memory_enabled"] is False

    asyncio.run(scenario())


def test_web_api_prompt_template_audit_lists_paginated_entries(
    tmp_path: Path, astrbot_web: Any, monkeypatch: Any
) -> None:
    """Template updates produce queryable, decoded audit entries."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        api = WebApi(repository, PluginConfig())

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "action": "update",
                    "key": "rule",
                    "content": "第一版规则",
                    "reason": "audit-test",
                },
            ),
        )
        assert (await api.dispatch("prompt-templates")).status_code == 200
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "action": "update",
                    "key": "rule",
                    "content": "第二版规则",
                    "reason": "audit-test-2",
                },
            ),
        )
        assert (await api.dispatch("prompt-templates")).status_code == 200

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "1", "page_size": "1"}),
        )
        data = _response_payload(await api.dispatch("prompt-template-audit"))["data"]
        assert data["total"] == 2
        assert data["page"] == 1
        assert data["page_size"] == 1
        assert len(data["items"]) == 1
        first = data["items"][0]
        assert first["action"] == "update"
        assert first["actor"] == "web_admin"
        assert first["reason"] == "audit-test-2"
        assert isinstance(first["before"], dict)
        assert first["after"]["rule"] == "第二版规则"
        assert "before_json" not in first and "after_json" not in first

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "2", "page_size": "1"}),
        )
        data = _response_payload(await api.dispatch("prompt-template-audit"))["data"]
        assert len(data["items"]) == 1
        assert data["items"][0]["reason"] == "audit-test"

    asyncio.run(scenario())


def test_overview_returns_pending_items_for_candidate_entries(
    tmp_path: Path, astrbot_web: Any, monkeypatch: Any
) -> None:
    """Overview lists the newest entries that still carry pending senses."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        await repository.ingest_unknown_terms(
            _context("req-1"),
            [UnknownTerm(word="yyds", guess="永远滴神", confidence=0.9, reason="语境")],
            0.75,
            20,
        )
        await repository.ingest_unknown_terms(
            _context("req-2"),
            [UnknownTerm(word="nb", guess="厉害", confidence=0.8, reason="语境")],
            0.75,
            20,
        )
        await repository.apply_jargon_action(1, "confirm")
        api = WebApi(repository, PluginConfig())

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        data = _response_payload(await api.dispatch("overview"))["data"]
        assert data["learned"] == 2
        assert data["pending"] == 1
        assert len(data["pending_items"]) == 1
        item = data["pending_items"][0]
        assert item["term"] == "nb"
        assert item["status"] == "provisional"
        assert item["pending_sense_count"] == 1
        assert {"id", "scope_type", "scope_id", "confidence", "updated_at"} <= set(item)

    asyncio.run(scenario())


def test_web_api_jargon_create_entry_validates_and_persists(
    tmp_path: Path, astrbot_web: Any, monkeypatch: Any
) -> None:
    """Create entry accepts a new term and rejects invalid payloads."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        api = WebApi(repository, PluginConfig())

        def post(body: dict[str, Any]):
            """Dispatch one jargon-action POST body."""
            monkeypatch.setattr(astrbot_web, "request", _FakeRequest("POST", body=body))
            return api.dispatch("jargon-action")

        response = await post(
            {
                "action": "create_entry",
                "term": "yyds",
                "scope_type": "group",
                "scope_id": "g1",
                "meaning": "永远滴神",
                "confidence": 0.9,
                "aliases": ["永远的神"],
            }
        )
        assert response.status_code == 200
        data = _response_payload(response)["data"]
        assert data["updated"] is True
        assert data["deleted"] is False
        entry = data["detail"]["entry"]
        assert entry["term"] == "yyds"
        assert entry["status"] == "candidate"
        assert data["detail"]["senses"][0]["meaning"] == "永远滴神"
        assert len(data["detail"]["aliases"]) == 1

        # Duplicate term in the same scope is rejected with 400.
        response = await post(
            {
                "action": "create_entry",
                "term": "yyds",
                "scope_type": "group",
                "scope_id": "g1",
                "meaning": "另一个释义",
            }
        )
        assert response.status_code == 400
        assert "already exists" in _response_payload(response)["message"]

        # Missing meaning is rejected with 400.
        response = await post(
            {
                "action": "create_entry",
                "term": "nb",
                "scope_type": "group",
                "scope_id": "g1",
            }
        )
        assert response.status_code == 400

        # Missing term is rejected with 400.
        response = await post(
            {
                "action": "create_entry",
                "scope_type": "group",
                "scope_id": "g1",
                "meaning": "厉害",
            }
        )
        assert response.status_code == 400

        # Non-create actions still require an entry id.
        response = await post({"action": "confirm"})
        assert response.status_code == 400

    asyncio.run(scenario())


def test_context_runs_link_latest_final_protocol_summary(
    tmp_path: Path, astrbot_web: Any, monkeypatch: Any
) -> None:
    """Context run listings expose a bounded protocol result summary."""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        for request_id in ("req-a", "req-b", "req-c"):
            await repository.record_context_run(
                _context(request_id), (_section(),), "user"
            )
        await repository.record_protocol(
            _context("req-a"),
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="正文",
            model="gpt-test",
            duration_ms=123,
            stage="final",
        )
        await repository.record_protocol(
            _context("req-a"),
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="正文2",
            model="gpt-test",
            duration_ms=456,
            stage="final",
        )
        await repository.record_protocol(
            _context("req-b"),
            success=False,
            action="",
            failure_code="invalid_control_header",
            failure_detail="missing Action",
            raw_output="",
            model="gpt-test",
            duration_ms=50,
            stage="final",
        )
        api = WebApi(repository, PluginConfig())

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "1", "page_size": "20"}),
        )
        data = _response_payload(await api.dispatch("context-runs"))["data"]
        assert data["total"] == 3
        by_request = {item["request_id"]: item for item in data["items"]}
        assert by_request["req-a"]["protocol_summary"] == {
            "success": True,
            "action": "Reply",
            "failure_code": "",
            "duration_ms": 456,
            "model": "gpt-test",
            "no_reply_reason": "",
        }
        summary_b = by_request["req-b"]["protocol_summary"]
        assert summary_b["success"] is False
        assert summary_b["failure_code"] == "invalid_control_header"
        assert by_request["req-c"]["protocol_summary"] is None
        assert "raw_output" not in by_request["req-a"]
        assert "request_snapshot" not in by_request["req-a"]
        assert "protocol_success" not in by_request["req-a"]

    asyncio.run(scenario())
