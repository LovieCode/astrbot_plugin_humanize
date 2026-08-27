from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import Action, MessageContext
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.services.humanize import HumanizeService


def _context(
    scope_id: str,
    *,
    request_id: str,
    message_id: str,
    user_text: str,
) -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type="chat",
        scope_id=scope_id,
        message_id=message_id,
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text,
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


async def _service(db_path: Path) -> tuple[HumanizeService, SQLiteRepository]:
    config = PluginConfig()
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    service = HumanizeService(
        config=config,
        repository=repository,
        envelope=EnvelopeBuilder(config),
        parser=ProtocolParser(config),
        matcher=JargonMatcher(),
    )
    return service, repository


def test_first_learning_is_injected_only_on_next_matching_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, _ = await _service(tmp_path / "humanize.db")
        first_context = _context(
            "group-a",
            request_id="req-1",
            message_id="msg-1",
            user_text="这操作真是 yyds",
        )
        first_prepared = await service.prepare_request(first_context)
        assert first_prepared.matched_terms == ()

        raw = (
            "<Action>Reply</Action>\n"
            '<UnknownTerms>[{"word":"yyds","guess":"永远的神，用于强烈称赞",'
            '"confidence":0.92,"reason":"用户用它评价一次很强的操作"}]</UnknownTerms>\n'
            "确实很强"
        )
        outcome = await service.process_final_response(
            first_context, raw, model="test-model", duration_ms=20
        )
        assert outcome.valid
        assert outcome.action is Action.REPLY
        assert [term.word for term in outcome.unknown_terms] == ["yyds"]

        second = await service.prepare_request(
            _context(
                "group-a",
                request_id="req-2",
                message_id="msg-2",
                user_text="这次也是 yyds",
            )
        )
        other_scope = await service.prepare_request(
            _context(
                "group-b",
                request_id="req-3",
                message_id="msg-3",
                user_text="这次也是 yyds",
            )
        )

        assert [term.term for term in second.matched_terms] == ["yyds"]
        assert "永远的神，用于强烈称赞" in second.known_terms_xml
        assert other_scope.matched_terms == ()
        assert "<Term>" not in other_scope.known_terms_xml

    asyncio.run(scenario())


def test_service_removes_one_provider_blank_line_before_dispatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service, _ = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-leading-blank",
            message_id="msg-leading-blank",
            user_text="普通消息",
        )
        raw = (
            "<Action>Reply</Action>\n"
            "<UnknownTerms>[]</UnknownTerms>\n\n"
            "<Messages><Message>正文第一行\n\n正文第三行</Message></Messages>"
        )

        outcome = await service.process_final_response(
            context, raw, model="test-model", duration_ms=10
        )

        assert outcome.valid
        assert outcome.messages == ("正文第一行\n\n正文第三行",)

    asyncio.run(scenario())


def test_service_removes_reply_markup_after_provider_framing_blank_line(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service, _ = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-reply-framing-blank",
            message_id="msg-reply-framing-blank",
            user_text="发张贴贴图",
        )
        raw = (
            "<Action>Reply</Action>\n"
            "<UnknownTerms>[]</UnknownTerms>\n\n"
            "<Reply><Message>贴贴真好</Message></Reply>"
        )

        outcome = await service.process_final_response(
            context, raw, model="test-model", duration_ms=10
        )

        assert outcome.valid
        assert outcome.messages == ("贴贴真好",)
        assert all("<Reply>" not in message for message in outcome.messages)
        assert all("<Message>" not in message for message in outcome.messages)

    asyncio.run(scenario())


def test_service_filters_noise_before_persistence(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-1",
            message_id="msg-1",
            user_text="yyds 12345 https://example.com",
        )
        raw = (
            "<Action>No Reply</Action>\n"
            "<UnknownTerms>["
            '{"word":"yyds","guess":"永远的神","confidence":0.9,"reason":"称赞"},'
            '{"word":"12345","guess":"数字","confidence":0.9,"reason":"出现过"},'
            '{"word":"https://example.com","guess":"链接","confidence":0.9,"reason":"出现过"},'
            '{"word":"不存在","guess":"幻觉","confidence":0.9,"reason":"没有依据"}'
            "]</UnknownTerms>"
        )

        outcome = await service.process_final_response(
            context, raw, model="test-model", duration_ms=10
        )
        stored = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )

        assert outcome.valid
        assert outcome.action is Action.NO_REPLY
        assert [term.word for term in outcome.unknown_terms] == ["yyds"]
        assert stored["total"] == 1
        assert stored["items"][0]["term"] == "yyds"

    asyncio.run(scenario())


def test_invalid_final_response_is_logged_without_learning(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-bad",
            message_id="msg-bad",
            user_text="这个 yyds 是什么意思",
        )

        outcome = await service.process_final_response(
            context,
            "没有控制头的自然语言回复",
            model="test-model",
            duration_ms=8,
        )
        stored = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        logs = await repository.list_protocol_logs(page=1, page_size=20)

        assert not outcome.valid
        assert outcome.error_code == "missing_action"
        assert stored["total"] == 0
        assert logs["total"] == 1
        assert logs["items"][0]["failure_code"] == "missing_action"

    asyncio.run(scenario())


def test_service_records_external_protocol_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-provider-failure",
            message_id="msg-provider-failure",
            user_text="测试",
        )

        await service.record_protocol_failure(
            context,
            error_code="repair_provider_failed",
            error_detail="provider unavailable",
            raw_output="broken header",
            model="test-model",
            duration_ms=17,
        )
        logs = await repository.list_protocol_logs(page=1, page_size=20)

        assert logs["total"] == 1
        assert logs["items"][0]["success"] == 0
        assert logs["items"][0]["failure_code"] == "repair_provider_failed"
        assert logs["items"][0]["failure_detail"] == "provider unavailable"
        assert logs["items"][0]["stage"] == "final"
        assert logs["items"][0]["is_final"] is True

    asyncio.run(scenario())


def test_service_can_defer_valid_success_until_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-deferred-success",
            message_id="msg-deferred-success",
            user_text="测试",
        )
        raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n收到"

        outcome = await service.process_final_response(
            context,
            raw,
            model="test-model",
            duration_ms=3,
            record_success=False,
        )
        assert outcome.valid
        assert (await repository.list_protocol_logs(page=1, page_size=20))["total"] == 0

        await service.record_protocol_success(
            context,
            action=outcome.action.value,
            raw_output=raw,
            messages=outcome.messages,
            model="test-model",
            duration_ms=4,
        )
        logs = await repository.list_protocol_logs(page=1, page_size=20)
        assert logs["total"] == 1
        assert logs["items"][0]["action"] == "Reply"
        assert logs["items"][0]["is_final"] is True

    asyncio.run(scenario())


def test_failed_attempt_does_not_learn_before_one_valid_repair(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-repair",
            message_id="msg-repair",
            user_text="这个 yyds 真厉害",
        )
        unknown_terms = (
            '[{"word":"yyds","guess":"永远的神","confidence":0.9,'
            '"reason":"用于强烈称赞"}]'
        )

        failed = await service.process_final_response(
            context,
            f"Action: Reply\nUnknownTerms: {unknown_terms}\n原正文",
            model="test-model",
            duration_ms=5,
        )
        before_repair = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        repaired = await service.process_final_response(
            context,
            (
                "<Action>Reply</Action>\n"
                f"<UnknownTerms>{unknown_terms}</UnknownTerms>\n"
                "原正文"
            ),
            model="test-model",
            duration_ms=8,
        )
        after_repair = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )

        assert not failed.valid
        assert before_repair["total"] == 0
        assert repaired.valid
        assert after_repair["total"] == 1
        assert after_repair["items"][0]["term"] == "yyds"
        assert after_repair["items"][0]["occurrence_count"] == 1

    asyncio.run(scenario())


def test_protocol_persistence_retries_and_returns_boolean_result() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def record_protocol(self, context, **kwargs):
            del context, kwargs
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("database temporarily busy")

    async def scenario() -> None:
        config = PluginConfig()
        repository = Repository()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=SimpleNamespace(),
        )

        persisted = await service.record_protocol_failure(
            _context(
                "group-a",
                request_id="req-retry",
                message_id="msg-retry",
                user_text="测试",
            ),
            error_code="dispatch_failed",
            error_detail="temporary failure",
            raw_output="raw",
            model="test-model",
            duration_ms=1,
        )

        assert persisted is True
        assert repository.calls == 3

    asyncio.run(scenario())


def test_context_trace_persistence_uses_the_same_bounded_retry_budget() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def record_context_run(
            self,
            context,
            sections,
            injection_mode,
            request_snapshot,
            request_snapshot_complete,
            request_snapshot_final=None,
            request_snapshot_final_complete=False,
        ):
            del (
                context,
                sections,
                injection_mode,
                request_snapshot,
                request_snapshot_complete,
                request_snapshot_final,
                request_snapshot_final_complete,
            )
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("database temporarily busy")

    async def scenario() -> None:
        config = PluginConfig()
        repository = Repository()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=SimpleNamespace(),
        )

        persisted = await service.record_context_trace(
            _context(
                "group-a",
                request_id="req-context-retry",
                message_id="msg-context-retry",
                user_text="测试",
            ),
            (),
            request_snapshot={"prompt": "完整快照"},
            request_snapshot_complete=True,
        )

        assert persisted is True
        assert repository.calls == 3

    asyncio.run(scenario())


def test_atomic_memory_failure_falls_back_to_protocol_log() -> None:
    class Repository:
        def __init__(self) -> None:
            self.atomic_calls = 0
            self.protocol_calls: list[dict] = []

        async def record_protocol_and_enqueue_memory(
            self, context, *, memory_job, **kwargs
        ):
            del context, memory_job, kwargs
            self.atomic_calls += 1
            raise RuntimeError("atomic write unavailable")

        async def record_protocol(self, context, **kwargs):
            del context
            self.protocol_calls.append(kwargs)

    class Memory:
        def __init__(self) -> None:
            self.provider_ids: list[str] = []

        async def build_turn_job(self, context, *, action, messages, provider_id=""):
            del context, action, messages
            self.provider_ids.append(provider_id)
            return {
                "job_type": "extract_turn",
                "idempotency_key": "req-atomic-fallback",
                "scope_type": "group_member",
                "scope_hash": "scope-hash",
            }

    async def scenario() -> None:
        config = PluginConfig()
        repository = Repository()
        memory = Memory()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=SimpleNamespace(),
            memory=memory,
        )

        persisted = await service.record_protocol_success(
            _context(
                "group-a",
                request_id="req-atomic-fallback",
                message_id="msg-atomic-fallback",
                user_text="测试",
            ),
            action="Reply",
            raw_output="raw",
            messages=("真实正文",),
            response_snapshot={"final": "snapshot"},
            response_snapshot_complete=True,
            model="test-model",
            provider_id="provider-a",
            duration_ms=1,
        )

        assert persisted is True
        assert repository.atomic_calls == 3
        assert len(repository.protocol_calls) == 1
        assert repository.protocol_calls[0]["messages"] == ("真实正文",)
        assert repository.protocol_calls[0]["response_snapshot"] == {
            "final": "snapshot"
        }
        assert memory.provider_ids == ["provider-a"]

    asyncio.run(scenario())


def test_protocol_persistence_returns_false_after_bounded_failures() -> None:
    class Repository:
        def __init__(self) -> None:
            self.calls = 0

        async def record_protocol(self, context, **kwargs):
            del context, kwargs
            self.calls += 1
            raise RuntimeError("database unavailable")

    async def scenario() -> None:
        config = PluginConfig()
        repository = Repository()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
            composer=SimpleNamespace(),
        )

        persisted = await service.record_protocol_failure(
            _context(
                "group-a",
                request_id="req-permanent-failure",
                message_id="msg-permanent-failure",
                user_text="测试",
            ),
            error_code="dispatch_failed",
            error_detail="permanent failure",
            raw_output="raw",
            model="test-model",
            duration_ms=1,
        )

        assert persisted is False
        assert repository.calls == 3

    asyncio.run(scenario())
