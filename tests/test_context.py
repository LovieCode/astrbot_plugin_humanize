from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.context.composer import ContextComposer
from astrbot_plugin_humanize.humanize.domain.models import (
    ContextSection,
    JargonStatus,
    KnownTerm,
    MessageContext,
)
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.memory import RecallResult
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import (
    _CONTEXT_SCHEMA,
    _SCHEMA,
    _SCHEMA_VERSION,
    SQLiteRepository,
)
from astrbot_plugin_humanize.humanize.services.humanize import HumanizeService
from astrbot_plugin_humanize.humanize.web.routes import WebApi


def _context(request_id: str = "req-1") -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type="group",
        scope_id="group-1",
        message_id="msg-1",
        sender_id="user-1",
        sender_name="小明",
        user_text="yyds 而且 nb",
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


def _known(entry_id: int, term: str, meaning: str) -> KnownTerm:
    return KnownTerm(
        entry_id=entry_id,
        term=term,
        normalized_term=term,
        meaning=meaning,
        confidence=0.9,
        status=JargonStatus.VERIFIED,
        scope_type="group",
        scope_id="group-1",
    )


class _CharacterTokenCounter:
    """Provide deterministic character counts for composer budget tests."""

    def count_tokens(self, messages, trusted_token_usage: int = 0) -> int:
        """Count string content characters.

        Args:
            messages: AstrBot message objects.
            trusted_token_usage: Unused trusted provider token count.

        Returns:
            Total string content length.
        """
        del trusted_token_usage
        return sum(
            len(message.content) if isinstance(message.content, str) else 0
            for message in messages
        )


class _TermRepository:
    """Expose only the term lookup consumed by ContextComposer."""

    def __init__(self, terms: list[KnownTerm]) -> None:
        self.terms = terms

    async def list_injectable_terms(self, *args, **kwargs) -> list[KnownTerm]:
        """Return configured injectable terms.

        Returns:
            Configured terms.
        """
        del args, kwargs
        return self.terms


def test_context_composer_orders_sections_and_enforces_token_budget() -> None:
    async def scenario() -> None:
        terms = [
            _known(1, "yyds", "永远的神"),
            _known(2, "nb", "很厉害"),
        ]
        base_config = PluginConfig.from_mapping({"protocol_injection_mode": "both"})
        envelope = EnvelopeBuilder(base_config)
        one_term_budget = len(envelope.build_known_terms_xml((terms[0],)))
        config = PluginConfig.from_mapping(
            {
                "protocol_injection_mode": "both",
                "max_injection_tokens": one_term_budget,
            }
        )
        composer = ContextComposer(
            config=config,
            repository=_TermRepository(terms),  # type: ignore[arg-type]
            envelope=EnvelopeBuilder(config),
            matcher=JargonMatcher(),
            token_counter=_CharacterTokenCounter(),
        )

        prepared = await composer.compose(_context())

        assert [section.key for section in prepared.sections] == [
            "current_message",
            "known_terms",
            "memory_context",
            "reply_examples",
            "response_protocol",
        ]
        assert [section.ordinal for section in prepared.sections] == [0, 1, 2, 3, 4]
        assert [section.priority for section in prepared.sections] == [
            100,
            60,
            70,
            65,
            90,
        ]
        assert [term.entry_id for term in prepared.matched_terms] == [1]
        jargon = prepared.sections[1]
        assert jargon.budget_tokens == one_term_budget
        assert jargon.estimated_tokens <= one_term_budget
        assert jargon.reason == "matched_current_message_budgeted"
        assert prepared.sections[2].included is False
        assert prepared.sections[2].reason == "not_initialized"
        assert prepared.sections[3].included is False
        assert prepared.sections[3].reason == "not_initialized"
        protocol = prepared.sections[4]
        assert protocol.targets == ("temp_user", "system")
        assert protocol.applied_tokens == protocol.estimated_tokens * 2
        assert prepared.message_xml == prepared.sections[0].content

    asyncio.run(scenario())


def test_service_records_every_context_run_in_shared_database(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = PluginConfig()
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        envelope = EnvelopeBuilder(config)
        matcher = JargonMatcher()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=envelope,
            parser=ProtocolParser(config),
            matcher=matcher,
            composer=ContextComposer(
                config=config,
                repository=repository,
                envelope=envelope,
                matcher=matcher,
                token_counter=_CharacterTokenCounter(),
            ),
        )

        prepared = await service.prepare_request(_context())
        assert await repository.get_context_run("req-1") is None
        await service.record_context_trace(_context(), prepared.sections)
        detail = await repository.get_context_run("req-1")
        stats = await repository.get_context_stats(days=7)
        overview = await repository.get_overview()

        assert len(prepared.sections) == 5
        assert detail is not None
        assert detail["run"]["request_id"] == "req-1"
        assert [item["section_key"] for item in detail["sections"]] == [
            "current_message",
            "known_terms",
            "memory_context",
            "reply_examples",
            "response_protocol",
        ]
        assert detail["sections"][1]["reason"] == "no_matching_trusted_term"
        assert stats["runs"] == 1
        assert overview["context_stats"] == {
            "total_runs": 1,
            "average_tokens": detail["run"]["estimated_tokens"],
            "omitted_runs": int(detail["run"]["omitted_sections"] > 0),
        }
        assert list(tmp_path.glob("*.db")) == [tmp_path / "humanize.db"]

    asyncio.run(scenario())


def test_context_composer_fails_open_when_memory_sources_raise() -> None:
    class FailingMemory:
        async def recall_memories(self, context: MessageContext):
            del context
            raise RuntimeError("memory unavailable")

        async def recall_examples(self, context: MessageContext, *, agent_id: str):
            del context, agent_id
            raise RuntimeError("examples unavailable")

    async def scenario() -> None:
        config = PluginConfig()
        composer = ContextComposer(
            config=config,
            repository=_TermRepository([]),  # type: ignore[arg-type]
            envelope=EnvelopeBuilder(config),
            matcher=JargonMatcher(),
            token_counter=_CharacterTokenCounter(),
            memory=FailingMemory(),  # type: ignore[arg-type]
        )

        prepared = await composer.compose(_context())

        assert [section.key for section in prepared.sections] == [
            "current_message",
            "known_terms",
            "memory_context",
            "reply_examples",
            "response_protocol",
        ]
        assert prepared.sections[2].included is False
        assert prepared.sections[2].reason == "source_error"
        assert prepared.sections[2].content == ""
        assert prepared.sections[3].included is False
        assert prepared.sections[3].reason == "source_error"
        assert prepared.sections[3].content == ""
        assert prepared.sections[4].required is True
        assert prepared.sections[4].targets == ("temp_user",)

    asyncio.run(scenario())


def test_context_composer_recalls_memory_sources_concurrently_and_independently() -> (
    None
):
    class ConcurrentMemory:
        def __init__(self) -> None:
            self.started = 0
            self.both_started = asyncio.Event()

        async def wait_for_peer(self) -> None:
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await self.both_started.wait()

        async def recall_memories(self, context: MessageContext):
            del context
            await self.wait_for_peer()
            raise RuntimeError("memory unavailable")

        async def recall_examples(self, context: MessageContext, *, agent_id: str):
            del context, agent_id
            await self.wait_for_peer()
            return RecallResult(
                True,
                "<ReplyExamples>并发成功</ReplyExamples>",
                ("example:1",),
                1,
                "matched",
                1,
            )

    async def scenario() -> None:
        config = PluginConfig()
        memory = ConcurrentMemory()
        composer = ContextComposer(
            config=config,
            repository=_TermRepository([]),  # type: ignore[arg-type]
            envelope=EnvelopeBuilder(config),
            matcher=JargonMatcher(),
            token_counter=_CharacterTokenCounter(),
            memory=memory,  # type: ignore[arg-type]
        )

        prepared = await asyncio.wait_for(composer.compose(_context()), timeout=1)

        assert memory.started == 2
        assert prepared.sections[2].included is False
        assert prepared.sections[2].reason == "source_error"
        assert prepared.sections[3].included is True
        assert prepared.sections[3].reason == "matched"
        assert prepared.sections[3].source_refs == ("example:1",)
        assert prepared.sections[3].content == (
            "<ReplyExamples>并发成功</ReplyExamples>"
        )

    asyncio.run(scenario())


def test_context_trace_is_idempotent_and_preview_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        content = "敏" * 1_500
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=900,
            applied_tokens=900,
            item_count=1,
            reason="current_user_message",
            content=content,
        )

        await repository.record_context_run(_context(), (section,), "user")
        await repository.record_context_run(_context(), (section,), "user")
        listing = await repository.list_context_runs(
            scope_type="group",
            scope_id="group-1",
            section_key="current_message",
            page=1,
            page_size=20,
        )
        detail = await repository.get_context_run("req-1")

        assert listing["total"] == 1
        assert "request_snapshot" not in listing["items"][0]
        assert "request_snapshot_json" not in listing["items"][0]
        assert detail is not None
        assert len(detail["sections"]) == 1
        assert len(detail["sections"][0]["content_preview"]) == 1_000
        assert len(detail["sections"][0]["content_hash"]) == 64
        assert detail["sections"][0]["content_chars"] == 1_500
        assert detail["sections"][0]["preview_truncated"] is True
        assert detail["sections"][0]["content"] == content
        assert detail["sections"][0]["snapshot_complete"] is True
        assert detail["sections"][0]["targets"] == ["prompt"]
        assert detail["sections"][0]["source_refs"] == ["message:msg-1"]
        assert detail["snapshot"]["snapshot_kind"] == "context_injection"
        assert detail["snapshot"]["snapshot_complete"] is True
        assert detail["snapshot"]["sections"][0]["content"] == content
        assert detail["response"] is None

    asyncio.run(scenario())


def test_context_detail_links_full_final_response_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = PluginConfig()
        repository = SQLiteRepository(
            tmp_path / "humanize.db",
            raw_log_chars=256,
        )
        await repository.initialize()
        envelope = EnvelopeBuilder(config)
        matcher = JargonMatcher()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=envelope,
            parser=ProtocolParser(config),
            matcher=matcher,
            composer=ContextComposer(
                config=config,
                repository=repository,
                envelope=envelope,
                matcher=matcher,
                token_counter=_CharacterTokenCounter(),
            ),
        )
        context = _context("req-response")
        prepared = await service.prepare_request(context)
        request_snapshot = {
            "capture_stage": "on_llm_request_finalizer",
            "type": "ProviderRequest",
            "fields": {
                "prompt": "外部前缀\n<Msg>真实用户消息</Msg>",
                "system_prompt": "系统与其他插件完整注入",
                "contexts": [{"role": "assistant", "content": "完整历史"}],
            },
        }
        await service.record_context_trace(
            context,
            prepared.sections,
            request_snapshot=request_snapshot,
            request_snapshot_complete=True,
        )
        body = "完整响应" * 1_200
        raw_output = (
            f"<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            f"<Messages><Message>{body}</Message></Messages>"
        )
        llm_snapshot = {
            "capture_stage": "on_llm_response_firewall",
            "responses": [
                {
                    "phase": "final",
                    "snapshot_complete": True,
                    "response": {
                        "type": "LLMResponse",
                        "fields": {
                            "role": "assistant",
                            "completion_text": raw_output,
                            "reasoning_content": "完整推理元数据",
                            "usage": {"input": 12, "output": 34},
                        },
                    },
                }
            ],
        }

        outcome = await service.process_final_response(
            context,
            raw_output,
            model="snapshot-model",
            duration_ms=23,
            response_snapshot=llm_snapshot,
            response_snapshot_complete=True,
        )
        detail = await repository.get_context_run("req-response")

        assert outcome.valid
        assert len(raw_output) > 4_000
        assert detail is not None
        assert detail["request_snapshot"] == {
            "snapshot_kind": "provider_request",
            "snapshot_complete": True,
            "provider_request": request_snapshot,
        }
        assert detail["request_snapshot_final"] == {
            "snapshot_kind": "provider_request_final",
            "snapshot_complete": False,
            "provider_request": None,
        }
        assert detail["response_snapshot"]["snapshot_kind"] == "llm_response"
        assert detail["response_snapshot"]["snapshot_complete"] is True
        assert detail["response_snapshot"]["llm_response"] == llm_snapshot
        assert detail["response_snapshot"]["protocol"]["raw_output"] == raw_output
        assert detail["response"] == {
            "success": True,
            "action": "Reply",
            "failure_code": "",
            "failure_detail": "",
            "model": "snapshot-model",
            "duration_ms": 23,
            "stage": "final",
            "created_at": detail["response"]["created_at"],
            "snapshot_complete": True,
            "raw_output": raw_output,
            "messages": [body],
        }

    asyncio.run(scenario())


def test_context_run_final_snapshot_update_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="测试",
        )
        context = _context("req-final")
        request_snapshot = {
            "capture_stage": "on_llm_request_finalizer",
            "type": "ProviderRequest",
            "fields": {"prompt": "<Msg>测试</Msg>"},
        }
        final_snapshot = {
            "capture_stage": "on_agent_done_final",
            "type": "provider_request_final",
            "fields": {
                "contexts": [
                    {"role": "system", "content": "persona 注入"},
                    {"role": "user", "content": "<Msg>测试</Msg>"},
                ],
                "image_urls": [],
                "audio_urls": [],
                "extra_user_content_parts": [],
                "func_tool": None,
                "system_prompt": "",
                "prompt": "",
                "model": "model-1",
            },
            "response": {
                "type": "LLMResponse",
                "fields": {
                    "completion_text": "你好",
                    "reasoning_content": "思考过程",
                },
            },
        }

        await repository.record_context_run(
            context,
            (section,),
            "user",
            request_snapshot=request_snapshot,
            request_snapshot_complete=True,
            request_snapshot_final=final_snapshot,
            request_snapshot_final_complete=True,
        )
        # 幂等：相同数据重复写入不抛错
        await repository.record_context_run(
            context,
            (section,),
            "user",
            request_snapshot=request_snapshot,
            request_snapshot_complete=True,
            request_snapshot_final=final_snapshot,
            request_snapshot_final_complete=True,
        )
        detail = await repository.get_context_run("req-final")

        assert detail is not None
        assert detail["request_snapshot"]["provider_request"] == request_snapshot
        assert detail["request_snapshot_final"]["provider_request"] == final_snapshot
        assert detail["request_snapshot_final"]["snapshot_complete"] is True

        # 独立更新最终快照（模拟 on_agent_done 覆盖）
        second_final = {
            "capture_stage": "on_agent_done_final",
            "type": "provider_request_final",
            "fields": {
                "contexts": [
                    {"role": "system", "content": "persona 注入 v2"},
                    {"role": "user", "content": "<Msg>测试</Msg>"},
                ],
                "image_urls": ["http://img/1"],
                "audio_urls": [],
                "extra_user_content_parts": [],
                "func_tool": None,
                "system_prompt": "",
                "prompt": "",
                "model": "model-1",
            },
            "response": {
                "type": "LLMResponse",
                "fields": {
                    "completion_text": "你好",
                    "reasoning_content": "思考过程 v2",
                },
            },
        }
        updated = await repository.update_context_run_final_snapshot(
            context,
            request_snapshot_final=second_final,
            request_snapshot_final_complete=True,
        )
        detail2 = await repository.get_context_run("req-final")

        assert updated is True
        assert detail2 is not None
        assert detail2["request_snapshot"]["provider_request"] == request_snapshot
        assert detail2["request_snapshot_final"]["provider_request"] == second_final
        assert (
            detail2["request_snapshot_final"]["provider_request"]["fields"]
            == second_final["fields"]
        )
        assert detail2["request_snapshot_final"]["snapshot_complete"] is True

        # 不存在的 request_id 返回 False
        missing = await repository.update_context_run_final_snapshot(
            _context("req-missing"),
            request_snapshot_final=final_snapshot,
            request_snapshot_final_complete=True,
        )
        assert missing is False

    asyncio.run(scenario())


def test_context_detail_links_latest_final_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="测试",
        )
        context = _context("req-failure")
        await repository.record_context_run(context, (section,), "user")
        await repository.record_protocol(
            context,
            success=False,
            action="",
            failure_code="invalid_control_header",
            failure_detail="missing Action",
            raw_output="原始错误响应",
            model="failure-model",
            duration_ms=7,
        )

        detail = await repository.get_context_run("req-failure")

        assert detail is not None
        assert detail["response"]["success"] is False
        assert detail["response"]["failure_code"] == "invalid_control_header"
        assert detail["response"]["failure_detail"] == "missing Action"
        assert detail["response"]["messages"] == []
        assert detail["response"]["raw_output"] == "原始错误响应"

    asyncio.run(scenario())


def test_context_detail_response_prefers_final_stage_over_first_row(
    tmp_path: Path,
) -> None:
    """多轮调用（tool + final）时主响应必须取 final 阶段，而非插入顺序首行。

    回归守卫：`get_context_run` 曾用 `row_index == 0` 取最早插入的一行，
    对多轮请求会拿到 tool 轮，导致一次成功回复被 API 报告为失败。
    """

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="测试",
        )
        context = _context("req-multistage")
        await repository.record_context_run(context, (section,), "user")
        # 中间轮先落库：工具调用阶段，未通过协议校验
        await repository.record_protocol(
            context,
            success=False,
            action="Reply",
            failure_code="missing_action_tag",
            failure_detail="中间轮未产出 Action",
            raw_output="中间轮原始输出",
            messages=[],
            model="stage-model",
            duration_ms=120,
            stage="tool",
        )
        # 最终轮后落库：这才是真正的结果
        await repository.record_protocol(
            context,
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="<Action>Reply</Action>你好",
            messages=["你好", "还有什么事吗"],
            model="stage-model",
            duration_ms=340,
            stage="final",
        )

        detail = await repository.get_context_run("req-multistage")

        assert detail is not None
        assert detail["response"]["stage"] == "final"
        assert detail["response"]["success"] is True
        assert detail["response"]["failure_code"] == ""
        assert detail["response"]["messages"] == ["你好", "还有什么事吗"]
        assert detail["response"]["raw_output"] == "<Action>Reply</Action>你好"
        # 两轮都保留在序列里，顺序仍按插入序
        assert [item["stage"] for item in detail["response_sequence"]] == [
            "tool",
            "final",
        ]
        # 详情快照同样绑定到 final 轮
        assert detail["response_snapshot"]["protocol"]["stage"] == "final"

    asyncio.run(scenario())


def test_context_detail_response_falls_back_to_latest_without_final(
    tmp_path: Path,
) -> None:
    """只有 tool 轮时兜底取最后一行，避免调用方拿到 None。"""

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="测试",
        )
        context = _context("req-tool-only")
        await repository.record_context_run(context, (section,), "user")
        await repository.record_protocol(
            context,
            success=False,
            action="Reply",
            failure_code="aborted",
            failure_detail="工具阶段中断",
            raw_output="未产出",
            model="stage-model",
            duration_ms=90,
            stage="tool",
        )

        detail = await repository.get_context_run("req-tool-only")

        assert detail is not None
        assert detail["response"] is not None
        assert detail["response"]["stage"] == "tool"
        assert detail["response"]["failure_code"] == "aborted"

    asyncio.run(scenario())


def test_context_trace_rejects_conflicting_duplicate_without_mutation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        original = ContextSection(
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
            content="原始内容",
        )
        changed = ContextSection(
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
            content="不同内容",
        )

        await repository.record_context_run(_context(), (original,), "user")
        before = await repository.get_context_run("req-1")
        with pytest.raises(RuntimeError, match="different trace data"):
            await repository.record_context_run(_context(), (changed,), "user")
        after = await repository.get_context_run("req-1")

        assert after == before
        assert after is not None
        assert after["sections"][0]["content_preview"] == "原始内容"

    asyncio.run(scenario())


def test_v5_migration_preserves_bounded_legacy_snapshots_and_is_repeatable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "humanize.db"
    legacy_schema = _SCHEMA.replace(
        "    raw_output_snapshot TEXT NOT NULL DEFAULT '',\n"
        "    raw_snapshot_complete INTEGER NOT NULL DEFAULT 0,\n"
        "    messages_json TEXT NOT NULL DEFAULT '[]',\n",
        "",
    )
    legacy_context_schema = _CONTEXT_SCHEMA.replace(
        "    content_snapshot TEXT NOT NULL,\n"
        "    snapshot_complete INTEGER NOT NULL,\n",
        "",
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(legacy_schema)
        conn.executescript(legacy_context_schema)
        SQLiteRepository._migrate_jargon_v2(conn)
        conn.execute(
            """
            INSERT INTO humanize_context_runs (
                id, request_id, scope_type, scope_id, message_id, sender_id,
                protocol_mode, estimated_tokens, included_sections,
                omitted_sections, created_at
            ) VALUES (1, 'legacy-request', 'group', 'group-1', 'msg-1', 'user-1',
                      'user', 20, 1, 0, '2026-01-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO humanize_context_sections (
                run_id, section_key, ordinal, priority, targets_json,
                source_type, source_refs_json, required, included,
                budget_tokens, estimated_tokens, applied_tokens, item_count,
                reason, content_preview, content_hash, content_chars,
                preview_truncated, created_at
            ) VALUES (1, 'current_message', 0, 100, '["prompt"]', 'message',
                      '["message:msg-1"]', 1, 1, NULL, 20, 20, 1,
                      'current_user_message', '旧版有界预览', ?, 1200, 1,
                      '2026-01-01T00:00:00+00:00')
            """,
            ("a" * 64,),
        )
        conn.execute(
            """
            INSERT INTO protocol_logs (
                request_id, scope_type, scope_id, message_id, sender_id,
                success, action, failure_code, failure_detail, raw_output,
                model, duration_ms, stage, created_at
            ) VALUES ('legacy-request', 'group', 'group-1', 'msg-1', 'user-1',
                      1, 'Reply', '', '', '旧版响应预览', 'legacy-model', 9,
                      'final', '2026-01-01T00:00:01+00:00')
            """
        )
        conn.execute(
            "ALTER TABLE humanize_context_sections "
            "ADD COLUMN content_snapshot TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            "ALTER TABLE protocol_logs "
            "ADD COLUMN messages_json TEXT NOT NULL DEFAULT '[]'"
        )
        conn.execute("PRAGMA user_version = 4")
        conn.commit()

    async def scenario() -> None:
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        await repository.initialize()
        detail = await repository.get_context_run("legacy-request")

        assert detail is not None
        assert detail["sections"][0]["content"] == "旧版有界预览"
        assert detail["sections"][0]["snapshot_complete"] is False
        assert detail["snapshot"]["snapshot_complete"] is False
        assert detail["response"]["raw_output"] == "旧版响应预览"
        assert detail["response"]["snapshot_complete"] is False
        assert detail["response"]["messages"] == []
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION

    asyncio.run(scenario())


def test_v6_migration_marks_legacy_request_and_response_snapshots_incomplete(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "humanize.db"
    v5_schema = _SCHEMA.replace(
        "    response_snapshot_json TEXT NOT NULL DEFAULT '{}',\n"
        "    response_snapshot_complete INTEGER NOT NULL DEFAULT 0,\n",
        "",
    )
    v5_context_schema = _CONTEXT_SCHEMA.replace(
        "    request_snapshot_json TEXT NOT NULL DEFAULT '{}',\n"
        "    request_snapshot_complete INTEGER NOT NULL DEFAULT 0,\n",
        "",
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(v5_schema)
        conn.executescript(v5_context_schema)
        SQLiteRepository._migrate_jargon_v2(conn)
        conn.execute(
            """
            INSERT INTO humanize_context_runs (
                id, request_id, scope_type, scope_id, message_id, sender_id,
                protocol_mode, estimated_tokens, included_sections,
                omitted_sections, created_at
            ) VALUES (1, 'v5-request', 'group', 'group-1', 'msg-1', 'user-1',
                      'user', 1, 0, 0, '2026-01-01T00:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO protocol_logs (
                request_id, scope_type, scope_id, message_id, sender_id,
                success, action, failure_code, failure_detail, raw_output,
                raw_output_snapshot, raw_snapshot_complete, messages_json,
                model, duration_ms, stage, created_at
            ) VALUES ('v5-request', 'group', 'group-1', 'msg-1', 'user-1',
                      1, 'Reply', '', '', 'ok', 'ok', 1, '["ok"]',
                      'model', 1, 'final', '2026-01-01T00:00:01+00:00')
            """
        )
        conn.execute("PRAGMA user_version = 5")
        conn.commit()

    async def scenario() -> None:
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        await repository.initialize()
        detail = await repository.get_context_run("v5-request")

        assert detail is not None
        assert detail["request_snapshot"] == {
            "snapshot_kind": "provider_request",
            "snapshot_complete": False,
            "provider_request": None,
        }
        assert detail["response_snapshot"]["snapshot_complete"] is False
        assert detail["response_snapshot"]["llm_response"] is None
        assert detail["response_snapshot"]["protocol"]["raw_output"] == "ok"
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION

    asyncio.run(scenario())


class _FakeRequest:
    """Provide request attributes consumed by WebApi."""

    def __init__(self, query: dict[str, Any] | None = None) -> None:
        self.method = "GET"
        self.query = query or {}

    async def json(self, default: Any = None) -> Any:
        """Return the supplied default body.

        Args:
            default: Default request body.

        Returns:
            The supplied default value.
        """
        return default


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.body.decode("utf-8"))


def test_context_web_api_contract(tmp_path: Path, monkeypatch: Any) -> None:
    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        section = ContextSection(
            key="known_terms",
            ordinal=1,
            priority=60,
            source_type="repository",
            source_refs=("jargon:1",),
            targets=("temp_user",),
            required=False,
            included=True,
            budget_tokens=256,
            estimated_tokens=20,
            applied_tokens=20,
            item_count=1,
            reason="matched_current_message",
            content="<KnownTerms />",
        )
        await repository.record_context_run(_context(), (section,), "user")
        api = WebApi(repository, PluginConfig())

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                {"page": "1", "page_size": "10", "section_key": "known_terms"}
            ),
        )
        runs = _payload(await api.dispatch("context-runs"))["data"]
        assert runs["total"] == 1
        assert runs["items"][0]["request_id"] == "req-1"

        monkeypatch.setattr(
            astrbot_web, "request", _FakeRequest({"request_id": "req-1"})
        )
        detail = _payload(await api.dispatch("context-run"))["data"]
        assert detail["sections"][0]["section_key"] == "known_terms"
        assert detail["sections"][0]["source_refs"] == ["jargon:1"]
        assert detail["sections"][0]["content"] == "<KnownTerms />"
        assert detail["snapshot"]["snapshot_kind"] == "context_injection"
        assert detail["snapshot"]["sections"][0]["targets"] == ["temp_user"]
        assert detail["response"] is None

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest({"days": "7"}))
        stats = _payload(await api.dispatch("context-stats"))["data"]
        assert stats["runs"] == 1
        assert stats["sections"][0]["section_key"] == "known_terms"

    asyncio.run(scenario())


def test_provider_capture_flushes_final_snapshot_with_reasoning_and_response(
    tmp_path: Path,
) -> None:
    """Provider call-time capture produces a final snapshot with reasoning."""
    from types import SimpleNamespace as _NS

    from astrbot_plugin_humanize.humanize.domain.models import (
        MessageContext as _MainMessageContext,
    )
    from astrbot_plugin_humanize.main import HumanizePlugin

    from astrbot.core.agent.message import TextPart

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        config = PluginConfig()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=_NS(),
        )
        plugin = HumanizePlugin(_NS(), {})
        plugin._container = _NS(service=service)
        plugin._provider_capture = {}

        # 记录一次 context run，模拟 on_llm_request 已写入中间快照
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="<Msg>hello</Msg>",
        )
        context = _MainMessageContext(
            request_id="req-provider-final",
            scope_type="group",
            scope_id="group-1",
            message_id="message-1",
            sender_id="user-1",
            sender_name="小明",
            user_text="hello",
            chat_scene="QQ群",
            admin_name="管理员",
            admin_ids=("admin-1",),
            conversation_id="conversation-1",
            occurred_at="2026-07-19T00:00:00+00:00",
            agent_id="default",
        )
        await repository.record_context_run(
            context,
            (section,),
            "user",
            request_snapshot={
                "capture_stage": "on_llm_request_finalizer",
                "fields": {"prompt": "<Msg>hello</Msg>"},
            },
            request_snapshot_complete=True,
        )

        # 模拟 provider patch 捕获（含 persona/KB/文件注入后的真实 contexts）
        plugin._capture_provider_payload(
            _NS(),
            {
                "session_id": "group-1",
                "contexts": [
                    {"role": "system", "content": "persona 注入"},
                    {"role": "user", "content": "<Msg>hello</Msg>"},
                ],
                "system_prompt": "完整系统提示",
                "prompt": "<Msg>hello</Msg>",
                "model": "model-1",
                "image_urls": [],
                "audio_urls": [],
                # 真实运行中 extra_user_content_parts 是 TextPart 等 ContentPart
                "extra_user_content_parts": [
                    TextPart(text="<KnownTerms>注入</KnownTerms>"),
                    TextPart(text="<HumanizeProtocol>协议</HumanizeProtocol>"),
                ],
                "func_tool": None,
            },
        )
        assert plugin._provider_capture["group-1"]["contexts"][0]["content"] == (
            "persona 注入"
        )
        # TextPart 必须被序列化为 JSON 安全结构，否则落库会 TypeError
        extra = plugin._provider_capture["group-1"]["extra_user_content_parts"]
        assert isinstance(extra, list) and len(extra) == 2
        assert extra[0]["text"] == "<KnownTerms>注入</KnownTerms>"

        event = _NS(unified_msg_origin="group-1")
        event.set_extra = lambda key, value: setattr(event, "__" + key, value)
        event.get_extra = lambda key, default=None: getattr(event, "__" + key, default)
        event.set_extra("_humanize_context", context)
        response = _NS(
            completion_text="<Action>Reply</Action>\n你好",
            reasoning_content="深度思考过程",
        )

        flushed = plugin._flush_provider_capture(event, response)
        assert flushed is True
        await asyncio.sleep(0.05)  # 等待异步落库任务

        detail = await repository.get_context_run("req-provider-final")
        assert detail is not None
        final = detail["request_snapshot_final"]["provider_request"]
        assert final["capture_stage"] == "on_provider_call"
        assert final["fields"]["contexts"][0]["content"] == "persona 注入"
        assert final["reasoning"] == "深度思考过程"
        assert final["response"]["fields"]["completion_text"] == (
            "<Action>Reply</Action>\n你好"
        )
        assert detail["request_snapshot_final"]["snapshot_complete"] is True

    asyncio.run(scenario())


def test_provider_capture_flushes_image_turn_with_image_cache(
    tmp_path: Path,
) -> None:
    """Image-only turns persist a final snapshot with image cache entries."""
    from types import SimpleNamespace as _NS

    from astrbot_plugin_humanize.humanize.domain.models import (
        ImageCache as _MainImageCache,
    )
    from astrbot_plugin_humanize.humanize.domain.models import (
        MessageContext as _MainMessageContext,
    )
    from astrbot_plugin_humanize.main import HumanizePlugin

    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        config = PluginConfig()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=_NS(),
        )
        plugin = HumanizePlugin(_NS(), {})
        plugin._container = _NS(service=service)
        plugin._provider_capture = {}

        context = _MainMessageContext(
            request_id="req-image-turn",
            scope_type="group",
            scope_id="group-1",
            message_id="message-1",
            sender_id="user-1",
            sender_name="小明",
            user_text="",
            chat_scene="QQ群",
            admin_name="管理员",
            admin_ids=("admin-1",),
            conversation_id="conversation-1",
            occurred_at="2026-07-19T00:00:00+00:00",
            agent_id="default",
        )
        section = ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="<Msg></Msg>",
        )
        await repository.record_context_run(
            context,
            (section,),
            "user",
            request_snapshot={
                "capture_stage": "on_llm_request_finalizer",
                "fields": {"prompt": ""},
            },
            request_snapshot_complete=True,
        )

        # 图片轮：contexts 为空，图片在 image_urls；event 带模型输出的 ImageCache
        plugin._capture_provider_payload(
            _NS(),
            {
                "session_id": "group-1",
                "contexts": [],
                "system_prompt": "persona",
                "prompt": None,
                "model": "model-1",
                "image_urls": ["/tmp/img.jpg"],
                "audio_urls": [],
                "extra_user_content_parts": [],
                "func_tool": None,
            },
        )
        event = _NS(unified_msg_origin="group-1")
        event.set_extra = lambda key, value: setattr(event, "__" + key, value)
        event.get_extra = lambda key, default=None: getattr(event, "__" + key, default)
        event.set_extra("_humanize_context", context)
        event.set_extra(
            "_humanize_image_cache",
            (_MainImageCache(text="一只小猪瞪着无辜的眼睛"),),
        )
        response = _NS(
            completion_text="<Action>Reply</Action>\n<Messages><Message>好可爱</Message></Messages>\n<ImageCache>一只小猪瞪着无辜的眼睛</ImageCache>",
            reasoning_content="看到了图片",
        )

        flushed = plugin._flush_provider_capture(event, response)
        assert flushed is True
        await asyncio.sleep(0.05)

        detail = await repository.get_context_run("req-image-turn")
        assert detail is not None
        final = detail["request_snapshot_final"]["provider_request"]
        assert final["capture_stage"] == "on_provider_call"
        # contexts 为空也写入（图片轮不因无 contexts 被丢弃）
        assert final["fields"]["contexts"] == []
        assert final["fields"]["image_urls"] == ["/tmp/img.jpg"]
        # 模型输出的 ImageCache 保留在快照中
        assert final["image_cache"] == ["一只小猪瞪着无辜的眼睛"]
        assert final["reasoning"] == "看到了图片"
        assert detail["request_snapshot_final"]["snapshot_complete"] is True

    asyncio.run(scenario())
