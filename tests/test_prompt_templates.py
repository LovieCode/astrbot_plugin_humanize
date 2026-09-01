from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import MessageContext
from astrbot_plugin_humanize.humanize.domain.prompts import (
    DEFAULT_PROTOCOL_TEMPLATE,
    DEFAULT_RULE_TEMPLATE,
    LEGACY_PROTOCOL_TEMPLATE,
    LEGACY_RULE_TEMPLATE,
    PromptTemplates,
)
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.repositories.sqlite import (
    _SCHEMA_VERSION,
    SQLiteRepository,
)
from astrbot_plugin_humanize.humanize.web.routes import WebApi


class _FakeRequest:
    """Provide the request attributes consumed by the plugin Web API."""

    def __init__(self, method: str, *, body: Any = None) -> None:
        self.method = method
        self.query: dict[str, Any] = {}
        self._body = body

    async def json(self, default: Any = None) -> Any:
        """Return the configured request body.

        Args:
            default: Fallback value when no body was configured.

        Returns:
            Configured JSON body or the supplied fallback.
        """
        return default if self._body is None else self._body


def _context() -> MessageContext:
    return MessageContext(
        request_id="req-1",
        scope_type="group",
        scope_id="group-1",
        message_id="msg-1",
        sender_id="user-1",
        sender_name="小明",
        user_text="你好",
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("10001",),
    )


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def test_prompt_templates_persist_with_dedicated_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        defaults = await repository.get_prompt_templates()
        custom = '协议 自定义\n长度 {{max_chars}}\n{"word":"JSON 保持不变"}'
        updated = await repository.update_prompt_templates(
            {"protocol": custom}, reason="edit protocol template"
        )

        reopened = SQLiteRepository(db_path)
        await reopened.initialize()
        persisted = await reopened.get_prompt_templates()

        assert set(defaults["templates"]) == {
            "rule",
            "protocol",
            "memory_extraction",
            "reply_examples",
        }
        assert updated["templates"]["protocol"] == custom
        assert persisted["templates"]["protocol"] == custom
        assert list(tmp_path.glob("*.db")) == [db_path]
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM humanize_prompt_templates"
                ).fetchone()[0]
                == 1
            )
            audit = conn.execute(
                """
                SELECT action, actor, reason, before_json, after_json
                FROM humanize_prompt_template_audit
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        assert audit is not None
        assert audit[0] == "update"
        assert audit[1] == "web_admin"
        assert audit[2] == "edit protocol template"
        assert json.loads(audit[3])["protocol"] == defaults["templates"]["protocol"]
        assert json.loads(audit[4])["protocol"] == custom
        assert "humanize_control_audit" not in tables

    asyncio.run(scenario())


def test_envelope_renders_only_declared_double_brace_variables() -> None:
    templates = PromptTemplates.from_mapping(
        {
            "rule": "<Rule>{{scene}}|{{admin_name}}|{{admin_ids}}<Rule/>",
            "protocol": (
                "长度 {{max_chars}} "
                "<UnknownTerms>["
                '{"word":"x","guess":"y","confidence":1,"reason":"z"}'
                "]</UnknownTerms>"
            ),
            "memory_extraction": "只输出 JSON 数组",
            "reply_examples": "<Examples>{{examples}}</Examples>",
        }
    )
    builder = EnvelopeBuilder(PluginConfig(max_message_chars=10), templates)

    prompt = builder.build_protocol_prompt(_context())

    assert "<Rule>QQ群聊天|管理员|10001<Rule/>" in prompt
    assert "长度 10" in prompt
    assert '{"word":"x","guess":"y","confidence":1,"reason":"z"}' in prompt
    assert templates.render("memory_extraction", {}) == "只输出 JSON 数组"
    assert templates.render("reply_examples", {"examples": "<Example />"}) == (
        "<Examples><Example /></Examples>"
    )


def test_migration_updates_only_unmodified_legacy_protocol_templates(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates "
                "SET protocol_content = ?, rule_content = ?",
                (LEGACY_PROTOCOL_TEMPLATE, LEGACY_RULE_TEMPLATE),
            )
            connection.execute("PRAGMA user_version = 21")
            connection.commit()

        upgraded = SQLiteRepository(db_path)
        await upgraded.initialize()
        assert (await upgraded.get_prompt_templates())["templates"][
            "protocol"
        ] == DEFAULT_PROTOCOL_TEMPLATE
        assert (await upgraded.get_prompt_templates())["templates"][
            "rule"
        ] == DEFAULT_RULE_TEMPLATE

        custom_protocol = "自定义协议 {{max_chars}}"
        custom_rule = "自定义规则 {{scene}}"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates "
                "SET protocol_content = ?, rule_content = ?",
                (custom_protocol, custom_rule),
            )
            connection.execute("PRAGMA user_version = 21")
            connection.commit()

        preserved = SQLiteRepository(db_path)
        await preserved.initialize()
        assert (await preserved.get_prompt_templates())["templates"][
            "protocol"
        ] == custom_protocol
        assert (await preserved.get_prompt_templates())["templates"]["rule"] == (
            custom_rule
        )

    asyncio.run(scenario())


def test_migration_upgrades_unmodified_rule_template_to_speak_expectation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        # 模拟 v27 库：规则模板是上一版默认（无 {{speak_expectation}} 占位）。
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates SET rule_content = ?",
                (LEGACY_RULE_TEMPLATE,),
            )
            connection.execute("PRAGMA user_version = 27")
            connection.commit()

        upgraded = SQLiteRepository(db_path)
        await upgraded.initialize()
        upgraded_rule = (await upgraded.get_prompt_templates())["templates"]["rule"]
        assert upgraded_rule == DEFAULT_RULE_TEMPLATE
        assert "{{speak_expectation}}" in upgraded_rule

        # 自定义过的规则模板保留原样（占位由使用者自行加入）。
        custom_rule = "自定义规则 {{scene}} {{speak_expectation}}"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates SET rule_content = ?",
                (custom_rule,),
            )
            connection.commit()

        preserved = SQLiteRepository(db_path)
        await preserved.initialize()
        assert (await preserved.get_prompt_templates())["templates"]["rule"] == (
            custom_rule
        )

    asyncio.run(scenario())


def test_migration_replaces_customized_templates_still_using_version_variable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        custom_protocol = "自定义协议 {{version}} 上限 {{max_chars}}"
        custom_rule = "自定义规则 {{scene}}"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates "
                "SET protocol_content = ?, rule_content = ?",
                (custom_protocol, custom_rule),
            )
            connection.execute("PRAGMA user_version = 24")
            connection.commit()

        upgraded = SQLiteRepository(db_path)
        await upgraded.initialize()
        templates = (await upgraded.get_prompt_templates())["templates"]
        assert templates["protocol"] == DEFAULT_PROTOCOL_TEMPLATE
        assert templates["rule"] == custom_rule

    asyncio.run(scenario())


def test_stored_templates_degrade_per_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        repository = SQLiteRepository(db_path)
        await repository.initialize()

        custom_rule = "自定义规则 {{scene}}"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE humanize_prompt_templates "
                "SET protocol_content = ?, rule_content = ?",
                ("残留的 {{version}} 模板", custom_rule),
            )
            connection.commit()

        templates = (await repository.get_prompt_templates())["templates"]
        assert templates["protocol"] == DEFAULT_PROTOCOL_TEMPLATE
        assert templates["rule"] == custom_rule

    asyncio.run(scenario())


def test_prompt_template_validation_rejects_unsafe_placeholders() -> None:
    with pytest.raises(ValueError, match="unsupported variable"):
        PromptTemplates.from_mapping({"protocol": "{{unknown}}"})

    with pytest.raises(ValueError, match="unsupported prompt template"):
        PromptTemplates.from_mapping({"repair": "只输出控制头"})

    with pytest.raises(ValueError, match="requires"):
        PromptTemplates.from_mapping({"reply_examples": "没有样例占位符"})


def test_web_api_supports_get_save_bulk_update_and_reset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        envelope = EnvelopeBuilder(PluginConfig())
        api = WebApi(
            repository,
            PluginConfig(),
            envelope,
        )

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        response = await api.dispatch("prompt-templates")
        data = _payload(response)["data"]
        assert [item["key"] for item in data["items"]] == [
            "rule",
            "protocol",
            "memory_extraction",
            "reply_examples",
        ]
        assert "{{max_chars}}" in data["items"][1]["variables"]
        assert data["items"][2]["variables"] == []
        assert data["items"][3]["required_variables"] == ["{{examples}}"]

        custom_protocol = 'CUSTOM {{max_chars}} 自定义 {"json":true}'
        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "key": "protocol",
                    "content": custom_protocol,
                    "reason": "single save",
                },
            ),
        )
        saved = _payload(await api.dispatch("prompt-templates"))["data"]
        assert saved["item"]["content"] == custom_protocol
        assert saved["updated"] == ["protocol"]
        assert 'CUSTOM 10 自定义 {"json":true}' in envelope.build_protocol_prompt(
            _context()
        )

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "templates": {
                        "rule": "<Rule>bulk {{scene}}<Rule/>",
                        "memory_extraction": "bulk 只输出 JSON 数组",
                    },
                    "reason": "bulk save",
                },
            ),
        )
        bulk = _payload(await api.dispatch("prompt-templates"))["data"]
        assert bulk["updated"] == ["rule", "memory_extraction"]
        assert "<Rule>bulk QQ群聊天<Rule/>" in envelope.build_protocol_prompt(
            _context()
        )

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("POST", body={"key": "protocol", "action": "reset"}),
        )
        reset = _payload(await api.dispatch("prompt-templates"))["data"]
        assert reset["reset"] == ["protocol"]
        assert "<Protocol>" in envelope.build_protocol_prompt(_context())
        assert "</Protocol>" in envelope.build_protocol_prompt(_context())

    asyncio.run(scenario())
