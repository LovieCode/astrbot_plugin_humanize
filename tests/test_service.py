from __future__ import annotations

import asyncio
from pathlib import Path

from humanize.config import PluginConfig
from humanize.domain.models import Action, MessageContext
from humanize.jargon.matcher import JargonMatcher
from humanize.protocol.envelope import EnvelopeBuilder
from humanize.protocol.parser import ProtocolParser
from humanize.repositories.sqlite import SQLiteRepository
from humanize.services.humanize import HumanizeService


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

        raw = """
<AgentResponse version="1">
  <Action>Reply</Action>
  <UnknownTerms>
    <UnknownTerm>
      <Word>yyds</Word>
      <Guess>永远的神，用于强烈称赞</Guess>
      <Confidence>0.92</Confidence>
      <Reason>用户用它评价一次很强的操作</Reason>
    </UnknownTerm>
  </UnknownTerms>
  <Reply><Message>确实很强</Message></Reply>
</AgentResponse>
"""
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


def test_service_filters_noise_before_persistence(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, repository = await _service(tmp_path / "humanize.db")
        context = _context(
            "group-a",
            request_id="req-1",
            message_id="msg-1",
            user_text="yyds 12345 https://example.com",
        )
        raw = """
<AgentResponse version="1">
  <Action>No Reply</Action>
  <UnknownTerms>
    <UnknownTerm><Word>yyds</Word><Guess>永远的神</Guess><Confidence>0.9</Confidence><Reason>称赞</Reason></UnknownTerm>
    <UnknownTerm><Word>12345</Word><Guess>数字</Guess><Confidence>0.9</Confidence><Reason>出现过</Reason></UnknownTerm>
    <UnknownTerm><Word>https://example.com</Word><Guess>链接</Guess><Confidence>0.9</Confidence><Reason>出现过</Reason></UnknownTerm>
    <UnknownTerm><Word>不存在</Word><Guess>幻觉</Guess><Confidence>0.9</Confidence><Reason>没有依据</Reason></UnknownTerm>
  </UnknownTerms>
  <Reply />
</AgentResponse>
"""

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
            "没有 XML 的自然语言回复",
            model="test-model",
            duration_ms=8,
        )
        stored = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        logs = await repository.list_protocol_logs(page=1, page_size=20)

        assert not outcome.valid
        assert outcome.error_code == "malformed_xml"
        assert stored["total"] == 0
        assert logs["total"] == 1
        assert logs["items"][0]["failure_code"] == "malformed_xml"

    asyncio.run(scenario())
