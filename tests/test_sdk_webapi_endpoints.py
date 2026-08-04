"""SDK-level mock tests for the Humanize Web API new endpoints.

Runs the real plugin adapter inside AstrBot SDK's test runtime (mock LLM,
dispatcher, and DB), drives one full chat turn so the repository has real
protocol/context/jargon rows, then exercises the real ``WebApi`` dispatcher
against that live data using a mocked ``astrbot.api.web.request``.

Requires ``ASTRBOT_SDK_PATH`` pointing at an astrbot-sdk checkout with the
Humanize plugin installed editable (see docs/sdk-full-flow-test-plan.md).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _plugin_harness():
    """Import the SDK PluginHarness from the configured SDK checkout."""
    sdk_root = Path(os.environ.get("ASTRBOT_SDK_PATH", "")).expanduser()
    sdk_source = sdk_root / "src"
    if not sdk_source.is_dir():
        pytest.skip(
            "set ASTRBOT_SDK_PATH to an astrbot-sdk checkout to run the SDK probe"
        )
    if str(sdk_source) not in sys.path:
        sys.path.insert(0, str(sdk_source))
    from astrbot_sdk.testing import PluginHarness

    return PluginHarness


def _write_web_api_adapter(plugin_dir: Path, db_path: Path) -> None:
    """Create an SDK plugin that adapts SDK events to the real Humanize WebApi."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            (
                "_schema_version: 2",
                "name: humanize_sdk_webapi",
                "display_name: Humanize SDK WebAPI",
                "author: tests",
                "repo: AstrBotDevs/humanize-sdk-webapi",
                "version: 0.1.0",
                "desc: SDK webapi mock test adapter for Humanize",
                "runtime:",
                '  python: "3.12"',
                "components:",
                "  - class: main:HumanizeWebApiAdapter",
                "",
            )
        ),
        encoding="utf-8",
    )
    (plugin_dir / "requirements.txt").write_text("", encoding="utf-8")
    (plugin_dir / "main.py").write_text(
        f"""from __future__ import annotations

from pathlib import Path
from typing import Any

import json

from astrbot_sdk import Context, MessageEvent, Star
from astrbot_sdk.decorators import on_command

from humanize.config import PluginConfig
from humanize.repositories.sqlite import SQLiteRepository
from humanize.web.routes import WebApi

DB_PATH = Path({str(db_path)!r})


class _FakeRequest:
    \"\"\"Provide the request attributes consumed by the plugin Web API.\"\"\"

    def __init__(self, method: str, *, query: dict[str, Any] | None = None, body: Any = None) -> None:
        self.method = method
        self.query = query or {{}}
        self._body = body

    async def json(self, default: Any = None) -> Any:
        return default if self._body is None else self._body


class HumanizeWebApiAdapter(Star):
    def __init__(self) -> None:
        super().__init__()
        self._repository = None
        self._api = None
        self._config = None
        self._config_dir = None

    async def _ensure(self) -> None:
        if self._repository is not None:
            return
        self._config_dir = Path(__file__).parent / "cfg"
        self._config_dir.mkdir(parents=True, exist_ok=True)
        config = PluginConfig(memory_enabled=False)
        repository = SQLiteRepository(DB_PATH)
        await repository.initialize()
        self._repository = repository
        self._config = config
        self._api = WebApi(repository, config)

    async def _dispatch(self, path: str, method: str = "GET", query: dict[str, Any] | None = None, body: Any = None):
        import astrbot.api.web as astrbot_web
        import astrbot.core.utils.astrbot_path as astrbot_path
                # Point config writes at this plugin's private dir without touching AstrBot.
        self._config_dir.mkdir(parents=True, exist_ok=True)
        astrbot_path.get_astrbot_config_path = lambda: str(self._config_dir)
        astrbot_web.request = _FakeRequest(method, query=query, body=body)
        return await self._api.dispatch(path)

    @on_command("humanize_webapi")
    async def run(self, event: MessageEvent, ctx: Context) -> None:
        del event
        await self._ensure()
        result: dict[str, Any] = {{}}
        import json as _json

        def payload(response: Any) -> Any:
            return _json.loads(response.body.decode("utf-8"))

        # 1. POST settings persists without reload and reports restart_required.
        resp = await self._dispatch(
            "settings",
            method="POST",
            body={{"values": {{"max_message_chars": 15, "admin_name": "SDK管理员"}}}},
        )
        data = payload(resp)["data"]
        result["settings_updated"] = sorted(data["updated"])
        result["restart_required"] = data["restart_required"]
        result["settings_status"] = resp.status_code
        cfg_file = self._config_dir / "astrbot_plugin_humanize_config.json"
        result["config_file_exists"] = cfg_file.is_file()
        if cfg_file.is_file():
            stored = _json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            result["config_file_value"] = stored["general"]["max_message_chars"]

        # 2. Unknown key rejected with 400.
        resp = await self._dispatch(
            "settings", method="POST", body={{"values": {{"bogus_key": 1}}}}
        )
        result["settings_unknown_status"] = resp.status_code

        # 3. GET settings still serves runtime defaults (no hot reload).
        resp = await self._dispatch("settings", method="GET")
        settings = payload(resp)["data"]
        result["settings_runtime_max_chars"] = settings["max_message_chars"]

        # 4. Create a jargon entry via create_entry.
        resp = await self._dispatch(
            "jargon-action",
            method="POST",
            body={{
                "action": "create_entry",
                "term": "yyds",
                "scope_type": "group",
                "scope_id": "sdk-group",
                "meaning": "永远的神",
                "confidence": 0.9,
                "aliases": ["永远的神"],
            }},
        )
        jg = payload(resp)["data"]
        result["jargon_status"] = resp.status_code
        result["jargon_id"] = jg["detail"]["entry"]["id"]
        result["jargon_term"] = jg["detail"]["entry"]["term"]
        result["jargon_sense_status"] = jg["detail"]["senses"][0]["status"]

        # 5. Duplicate create_entry rejected with 400.
        resp = await self._dispatch(
            "jargon-action",
            method="POST",
            body={{
                "action": "create_entry",
                "term": "yyds",
                "scope_type": "group",
                "scope_id": "sdk-group",
                "meaning": "永远的神",
            }},
        )
        result["jargon_dup_status"] = resp.status_code

        # 6. GET jargons lists the created entry.
        resp = await self._dispatch("jargons", method="GET", query={{"page": "1", "page_size": "20"}})
        jargons = payload(resp)["data"]
        result["jargons_total"] = jargons["total"]
        result["jargons_first_term"] = jargons["items"][0]["term"] if jargons["items"] else ""

        # 7. overview pending_items includes the candidate entry.
        resp = await self._dispatch("overview", method="GET")
        ov = payload(resp)["data"]
        result["overview_pending"] = ov["pending"]
        result["overview_pending_items"] = [i["term"] for i in ov.get("pending_items", [])]

        # 8. Record a protocol log + context run, then read protocol_summary.
        from humanize.domain.models import MessageContext
        from humanize.domain.models import ContextSection
        from humanize.repositories.protocol import ProtocolRepository

        context = MessageContext(
            request_id="sdk-webapi-run-1",
            scope_type="group",
            scope_id="sdk-group",
            message_id="msg-1",
            sender_id="user-1",
            sender_name="SDK用户",
            user_text="yyds",
            chat_scene="群聊",
            admin_name="admin",
            admin_ids=(),
            conversation_id="conv-1",
            agent_id="default",
        )
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            targets=("prompt",),
            source_type="message",
            source_refs=("message:msg-1",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=10,
            applied_tokens=10,
            item_count=1,
            reason="current_user_message",
            content="<Message><UserText>yyds</UserText></Message>",
        )
        await self._repository.record_context_run(context, (section,), "user")
        await self._repository.record_protocol(
            context,
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="<Action>Reply</Action>",
            messages=("yyds 就是永远的神",),
            model="sdk-mock",
            duration_ms=42,
            stage="final",
        )
        resp = await self._dispatch(
            "context-runs", method="GET", query={{"page": "1", "page_size": "20"}}
        )
        runs = payload(resp)["data"]
        result["context_runs_total"] = runs["total"]
        first = runs["items"][0] if runs["items"] else {{}}
        result["protocol_summary"] = first.get("protocol_summary")

        # 9. Prompt template update then audit query.
        resp = await self._dispatch(
            "prompt-templates",
            method="POST",
            body={{
                "action": "update",
                "key": "rule",
                "content": "你是 {{admin_name}} 的助手，当前场景 {{scene}}。",
                "reason": "sdk mock update",
            }},
        )
        result["template_update_status"] = resp.status_code
        resp = await self._dispatch(
            "prompt-template-audit", method="GET", query={{"page": "1", "page_size": "10"}}
        )
        audit = payload(resp)["data"]
        result["audit_total"] = audit["total"]
        result["audit_first_action"] = audit["items"][0]["action"] if audit["items"] else ""
        result["audit_first_reason"] = audit["items"][0]["reason"] if audit["items"] else ""

        # Persist the result through the SDK memory backend for assertion.
        await ctx.db.set("sdk_webapi:result", result)

    @on_command("humanize_webapi_config")
    async def read_config(self, event: MessageEvent, ctx: Context) -> None:
        del event
        await self._ensure()
        cfg_file = self._config_dir / "astrbot_plugin_humanize_config.json"
        result = {{"exists": cfg_file.is_file()}}
        if cfg_file.is_file():
            stored = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            result["max_chars"] = stored["general"]["max_message_chars"]
            result["admin"] = stored["general"]["admin_name"]
        await ctx.db.set("sdk_webapi:config", result)

    @on_command("humanize_webapi_settings")
    async def read_runtime_settings(self, event: MessageEvent, ctx: Context) -> None:
        del event
        await self._ensure()
        await ctx.db.set(
            "sdk_webapi:runtime_settings",
            {{"max_chars": self._config.max_message_chars, "enabled": self._config.enabled}},
        )
""",
        encoding="utf-8",
    )


async def _repository(db_path: Path):
    """Open a fresh repository over the shared DB for direct assertions."""
    from humanize.repositories.sqlite import SQLiteRepository

    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


@pytest.mark.asyncio
async def test_sdk_webapi_endpoints_on_live_plugin_data(tmp_path: Path) -> None:
    """SDK runtime + real WebApi: settings/jargon/audit/protocol_summary."""
    plugin_harness = _plugin_harness()
    db_path = tmp_path / "humanize.db"
    plugin_dir = tmp_path / "humanize_sdk_webapi"
    _write_web_api_adapter(plugin_dir, db_path)

    async with plugin_harness.from_plugin_dir(
        plugin_dir,
        session_id="sdk-test:group:sdk-group",
        user_id="user-one",
        platform="test",
        group_id="sdk-group",
    ) as harness:
        await harness.dispatch_text("humanize_webapi")

    result = harness.router.db_store["humanize_sdk_webapi:sdk_webapi:result"]

    # Settings
    assert result["settings_status"] == 200
    assert sorted(result["settings_updated"]) == ["admin_name", "max_message_chars"]
    assert result["restart_required"] is True
    assert result["config_file_exists"] is True
    assert result["config_file_value"] == 15
    assert result["settings_unknown_status"] == 400
    assert result["settings_runtime_max_chars"] == 10  # runtime unchanged (no reload)

    # Jargon create_entry
    assert result["jargon_status"] == 200
    assert result["jargon_term"] == "yyds"
    assert result["jargon_sense_status"] == "candidate"
    assert result["jargon_dup_status"] == 400
    assert result["jargons_total"] == 1
    assert result["jargons_first_term"] == "yyds"

    # Overview pending_items
    assert result["overview_pending"] >= 1
    assert "yyds" in result["overview_pending_items"]

    # Context runs + protocol_summary
    assert result["context_runs_total"] >= 1
    assert result["protocol_summary"] == {
        "success": True,
        "action": "Reply",
        "failure_code": "",
        "duration_ms": 42,
        "model": "sdk-mock",
    }

    # Prompt template audit
    assert result["template_update_status"] == 200
    assert result["audit_total"] >= 1
    assert result["audit_first_action"] == "update"
    assert result["audit_first_reason"] == "sdk mock update"

    # Direct repository assertions over the same DB.
    repository = await _repository(db_path)
    jargons = await repository.list_jargons(
        search="", status="", scope_id="sdk-group", page=1, page_size=20
    )
    assert [item["term"] for item in jargons["items"]] == ["yyds"]
    runs = await repository.list_context_runs(
        scope_type="", scope_id="", section_key="", page=1, page_size=20
    )
    assert runs["items"][0]["request_id"] == "sdk-webapi-run-1"
    assert runs["items"][0]["protocol_summary"]["action"] == "Reply"
    audit = await repository.list_prompt_template_audit(page=1, page_size=10)
    assert audit["items"][0]["reason"] == "sdk mock update"


@pytest.mark.asyncio
async def test_sdk_settings_file_survives_across_commands(tmp_path: Path) -> None:
    """The saved settings file is readable after the harness session ends."""
    plugin_harness = _plugin_harness()
    db_path = tmp_path / "humanize.db"
    plugin_dir = tmp_path / "humanize_sdk_webapi"
    _write_web_api_adapter(plugin_dir, db_path)

    async with plugin_harness.from_plugin_dir(
        plugin_dir,
        session_id="sdk-test:group:sdk-group",
        user_id="user-one",
        platform="test",
        group_id="sdk-group",
    ) as harness:
        await harness.dispatch_text("humanize_webapi")
        await harness.dispatch_text("humanize_webapi_config")

    config = harness.router.db_store["humanize_sdk_webapi:sdk_webapi:config"]
    assert config["exists"] is True
    assert config["max_chars"] == 15
    assert config["admin"] == "SDK管理员"
