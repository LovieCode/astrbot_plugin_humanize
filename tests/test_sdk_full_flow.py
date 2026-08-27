"""Run the real Humanize request/response path in AstrBot SDK's test runtime.

The production plugin is still an AstrBot Core hook plugin. This test creates a
small SDK-native adapter at runtime, but the adapter uses the actual Humanize
repository, context composer, protocol parser, and service implementation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository


def _plugin_harness():
    sdk_root = Path(os.environ.get("ASTRBOT_SDK_PATH", "")).expanduser()
    sdk_source = sdk_root / "src"
    if not sdk_source.is_dir():
        pytest.skip(
            "set ASTRBOT_SDK_PATH to an astrbot-sdk checkout to run the SDK full flow"
        )
    try:
        from astrbot.core.agent.context.token_counter import EstimateTokenCounter
    except ModuleNotFoundError:
        pytest.skip(
            "run the SDK full flow with astrbot-sdk/.venv-humanize, not the SDK-only environment"
        )
    del EstimateTokenCounter
    if str(sdk_source) not in sys.path:
        sys.path.insert(0, str(sdk_source))
    from astrbot_sdk.testing import PluginHarness

    return PluginHarness


def _write_full_flow_adapter(plugin_dir: Path, db_path: Path) -> None:
    """Create an SDK plugin that adapts SDK events to the real Humanize service."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            (
                "_schema_version: 2",
                "name: humanize_sdk_full_flow",
                "display_name: Humanize SDK Full Flow",
                "author: tests",
                "repo: AstrBotDevs/humanize-sdk-full-flow",
                "version: 0.1.0",
                "desc: SDK full-flow test adapter for Humanize",
                "runtime:",
                '  python: "3.12"',
                "components:",
                "  - class: main:HumanizeFullFlowAdapter",
                "",
            )
        ),
        encoding="utf-8",
    )
    (plugin_dir / "requirements.txt").write_text("", encoding="utf-8")
    (plugin_dir / "main.py").write_text(
        f"""from __future__ import annotations

from pathlib import Path
from time import perf_counter

from astrbot_sdk import Context, MessageEvent, Star
from astrbot_sdk.decorators import on_command

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.context.composer import ContextComposer
from astrbot_plugin_humanize.humanize.domain.models import Action, MessageContext
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.services.humanize import HumanizeService


DB_PATH = Path({str(db_path)!r})


class HumanizeFullFlowAdapter(Star):
    def __init__(self) -> None:
        super().__init__()
        self._repository = None
        self._service = None

    async def _ensure_service(self) -> None:
        if self._service is not None:
            return
        config = PluginConfig(
            memory_enabled=False,
            memory_auto_extract_enabled=False,
            reply_examples_enabled=False,
            protocol_injection_mode="both",
        )
        repository = SQLiteRepository(DB_PATH)
        await repository.initialize()
        envelope = EnvelopeBuilder(config)
        matcher = JargonMatcher()
        composer = ContextComposer(
            config=config,
            repository=repository,
            envelope=envelope,
            matcher=matcher,
        )
        self._repository = repository
        self._service = HumanizeService(
            config=config,
            repository=repository,
            envelope=envelope,
            parser=ProtocolParser(config),
            matcher=matcher,
            composer=composer,
        )

    @staticmethod
    def _message_context(event: MessageEvent) -> MessageContext:
        raw_metadata = event.raw.get("raw", {{}})
        trace_id = str(raw_metadata.get("trace_id") or event.session_id)
        scope_type = "group" if event.group_id else "private"
        scope_value = event.group_id or event.user_id or event.session_id
        scope_id = f"{{event.platform}}:{{scope_value}}"
        return MessageContext(
            request_id=trace_id,
            scope_type=scope_type,
            scope_id=scope_id,
            message_id=f"{{event.platform}}:{{event.session_id}}:{{trace_id}}",
            sender_id=event.user_id,
            sender_name=event.sender_name or event.user_id,
            user_text=event.text,
            chat_scene=scope_type,
            admin_name="admin",
            admin_ids=(),
            conversation_id=event.session_id,
        )

    @on_command("humanize")
    async def run(self, event: MessageEvent, ctx: Context) -> None:
        await self._ensure_service()
        assert self._service is not None

        started_at = perf_counter()
        context = self._message_context(event)
        prepared = await self._service.prepare_request(
            context,
            include_session_fallback=False,
        )
        temp_user_sections = [
            section
            for section in prepared.sections
            if section.included and "temp_user" in section.targets
        ]
        system_sections = [
            section
            for section in prepared.sections
            if section.included and "system" in section.targets
        ]
        llm_contexts = [
            {{"role": "user", "content": section.content}}
            for section in temp_user_sections
        ]
        system_prompt = "\\n\\n".join(section.content for section in system_sections)
        request_snapshot = {{
            "adapter": "sdk_full_flow",
            "prompt": prepared.message_xml,
            "system": system_prompt,
            "contexts": llm_contexts,
        }}
        await self._service.record_context_trace(
            context,
            prepared.sections,
            request_snapshot=request_snapshot,
            request_snapshot_complete=True,
        )
        await ctx.db.set(
            f"sdk_full_flow:{{context.request_id}}",
            {{
                "request_id": context.request_id,
                "scope_id": context.scope_id,
                "section_keys": [section.key for section in prepared.sections],
                "temp_user_keys": [section.key for section in temp_user_sections],
                "system_keys": [section.key for section in system_sections],
                "matched_terms": [term.term for term in prepared.matched_terms],
            }},
        )

        raw_output = await ctx.llm.chat(
            prepared.message_xml,
            system=system_prompt or None,
            contexts=llm_contexts,
            model="sdk-mock",
        )
        duration_ms = int((perf_counter() - started_at) * 1_000)
        outcome = await self._service.process_final_response(
            context,
            raw_output,
            model="sdk-mock",
            provider_id="sdk-mock",
            duration_ms=duration_ms,
            record_success=False,
            response_snapshot={{"text": raw_output}},
            response_snapshot_complete=True,
        )
        if not outcome.valid:
            return

        delivered = []
        if outcome.action is Action.REPLY:
            for message in outcome.messages:
                await event.reply(message)
                delivered.append(message)
        await self._service.record_protocol_success(
            context,
            action=outcome.action.value,
            raw_output=raw_output,
            messages=delivered,
            response_snapshot={{"text": raw_output}},
            response_snapshot_complete=True,
            model="sdk-mock",
            provider_id="sdk-mock",
            duration_ms=duration_ms,
        )
""",
        encoding="utf-8",
    )


async def _repository(db_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


@pytest.mark.asyncio
async def test_sdk_harness_runs_humanize_full_flow(tmp_path: Path) -> None:
    """Exercise SDK event, mock LLM, protocol gate, outbound messages, and audit."""
    plugin_harness = _plugin_harness()
    db_path = tmp_path / "humanize.db"
    plugin_dir = tmp_path / "humanize_sdk_full_flow"
    _write_full_flow_adapter(plugin_dir, db_path)

    first_reply = (
        "<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"yyds","guess":"永远的神",'
        '"confidence":0.92,"reason":"用户在当前消息中使用"}]</UnknownTerms>\n'
        "<Reply><Message>懂了</Message><Message>确实 yyds</Message></Reply>"
    )
    no_reply = "<Action>No Reply</Action>\n<UnknownTerms>[]</UnknownTerms>"
    invalid_reply = "not a control header"
    later_reply = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n继续聊"
    other_scope_reply = (
        "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n换个群聊"
    )

    async with plugin_harness.from_plugin_dir(
        plugin_dir,
        session_id="test:group-a",
        user_id="user-one",
        platform="test",
        group_id="group-a",
    ) as harness:
        harness.router.enqueue_llm_response(first_reply)
        first = await harness.dispatch_text(
            "humanize 这操作真是 yyds",
            request_id="sdk-full-1",
        )
        assert [(item.kind, item.session_id, item.text) for item in first] == [
            ("text", "test:group-a", "懂了"),
            ("text", "test:group-a", "确实 yyds"),
        ]
        first_capture = harness.router.db_store[
            "humanize_sdk_full_flow:sdk_full_flow:sdk-full-1"
        ]
        assert first_capture["section_keys"] == [
            "current_message",
            "known_terms",
            "memory_context",
            "reply_examples",
            "response_protocol",
        ]
        assert first_capture["temp_user_keys"] == [
            "known_terms",
            "response_protocol",
        ]
        assert first_capture["system_keys"] == ["response_protocol"]

        harness.router.enqueue_llm_response(no_reply)
        no_reply_result = await harness.dispatch_text(
            "humanize 不需要回应",
            request_id="sdk-full-2",
        )
        assert no_reply_result == []

        harness.router.enqueue_llm_response(invalid_reply)
        invalid_result = await harness.dispatch_text(
            "humanize 这个 yyds 是什么意思",
            request_id="sdk-full-3",
        )
        assert invalid_result == []

        harness.router.enqueue_llm_response(later_reply)
        later = await harness.dispatch_text(
            "humanize 这次也是 yyds",
            request_id="sdk-full-4",
        )
        assert [(item.kind, item.text) for item in later] == [("text", "继续聊")]
        later_capture = harness.router.db_store[
            "humanize_sdk_full_flow:sdk_full_flow:sdk-full-4"
        ]
        assert later_capture["matched_terms"] == ["yyds"]

        harness.router.enqueue_llm_response(other_scope_reply)
        other_scope = await harness.dispatch_text(
            "humanize 这次也是 yyds",
            session_id="test:group-b",
            group_id="group-b",
            request_id="sdk-full-5",
        )
        assert [(item.kind, item.text) for item in other_scope] == [
            ("text", "换个群聊")
        ]
        other_scope_capture = harness.router.db_store[
            "humanize_sdk_full_flow:sdk_full_flow:sdk-full-5"
        ]
        assert other_scope_capture["matched_terms"] == []

    repository = await _repository(db_path)
    context_run = await repository.get_context_run("sdk-full-4")
    assert context_run is not None
    assert context_run["run"]["scope_id"] == "test:group-a"
    assert [section["section_key"] for section in context_run["sections"]] == [
        "current_message",
        "known_terms",
        "memory_context",
        "reply_examples",
        "response_protocol",
    ]
    assert context_run["request_snapshot"]["snapshot_complete"]
    assert (
        "永远的神"
        in context_run["request_snapshot"]["provider_request"]["contexts"][0]["content"]
    )

    logs = await repository.list_protocol_logs(page=1, page_size=20)
    by_request_id = {item["request_id"]: item for item in logs["items"]}
    assert by_request_id["sdk-full-1"]["success"]
    assert by_request_id["sdk-full-1"]["action"] == "Reply"
    assert by_request_id["sdk-full-2"]["success"]
    assert by_request_id["sdk-full-2"]["action"] == "No Reply"
    assert not by_request_id["sdk-full-3"]["success"]
    assert by_request_id["sdk-full-3"]["failure_code"] == "invalid_control_header"

    jargons = await repository.list_jargons(
        search="",
        status="",
        scope_id="test:group-a",
        page=1,
        page_size=20,
    )
    assert [item["term"] for item in jargons["items"]] == ["yyds"]
