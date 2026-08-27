from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot_plugin_humanize.humanize.domain.models import MessageContext, UnknownTerm
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository


def _context(
    scope_id: str,
    *,
    scope_type: str = "chat",
    request_id: str = "req-1",
    message_id: str = "msg-1",
    user_text: str = "yyds 真厉害",
) -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type=scope_type,
        scope_id=scope_id,
        message_id=message_id,
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text,
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


def _term(
    word: str = "yyds",
    guess: str = "永远的神",
    confidence: float = 0.9,
) -> UnknownTerm:
    return UnknownTerm(
        word=word,
        guess=guess,
        confidence=confidence,
        reason="当前上下文用于称赞",
    )


async def _repository(db_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


def test_ingest_is_idempotent_for_same_message(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        context = _context("group-a")

        first = await repository.ingest_unknown_terms(context, [_term()], 0.75, 20)
        duplicate = await repository.ingest_unknown_terms(context, [_term()], 0.75, 20)
        result = await repository.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        detail = await repository.get_jargon_detail(first[0])

        assert len(first) == 1
        assert duplicate == []
        assert result["total"] == 1
        assert result["items"][0]["occurrence_count"] == 1
        assert detail is not None
        assert len(detail["evidence"]) == 1
        assert len(detail["inferences"]) == 1

    asyncio.run(scenario())


def test_same_term_is_isolated_between_scopes(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        await repository.ingest_unknown_terms(
            _context("group-a"), [_term(guess="永远的神")], 0.75, 20
        )
        await repository.ingest_unknown_terms(
            _context("group-b", request_id="req-2", message_id="msg-2"),
            [_term(guess="本群对优秀玩家的称呼")],
            0.75,
            20,
        )

        group_a = await repository.list_injectable_terms("chat", "group-a", 0.75)
        group_b = await repository.list_injectable_terms("chat", "group-b", 0.75)
        missing = await repository.list_injectable_terms("chat", "group-c", 0.75)

        assert len(group_a) == len(group_b) == 1
        assert group_a[0].entry_id != group_b[0].entry_id
        assert group_a[0].meaning == "永远的神"
        assert group_b[0].meaning == "本群对优秀玩家的称呼"
        assert missing == []

    asyncio.run(scenario())


def test_dashboard_filter_uses_composite_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        await repository.ingest_unknown_terms(
            _context("shared-id", scope_type="group"), [_term()], 0.75, 20
        )
        await repository.ingest_unknown_terms(
            _context(
                "shared-id",
                scope_type="private",
                request_id="req-2",
                message_id="msg-2",
            ),
            [_term(guess="私聊中的含义")],
            0.75,
            20,
        )

        group = await repository.list_jargons(
            search="",
            status="",
            scope_id="shared-id",
            scope_type="group",
            page=1,
            page_size=20,
        )
        private = await repository.list_jargons(
            search="",
            status="",
            scope_id="shared-id",
            scope_type="private",
            page=1,
            page_size=20,
        )

        assert group["total"] == 1
        assert group["items"][0]["scope_type"] == "group"
        assert private["total"] == 1
        assert private["items"][0]["scope_type"] == "private"

    asyncio.run(scenario())


def test_admin_can_confirm_and_reject_jargon(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        changed = await repository.ingest_unknown_terms(
            _context("group-a"), [_term(confidence=0.4)], 0.75, 20
        )
        entry_id = changed[0]

        assert await repository.list_injectable_terms("chat", "group-a", 0.75) == []
        pending = await repository.list_jargons(
            search="", status="candidate", scope_id="group-a", page=1, page_size=20
        )
        assert pending["total"] == 1
        assert await repository.apply_jargon_action(entry_id, "confirm")
        confirmed = await repository.list_injectable_terms("chat", "group-a", 0.75)
        confirmed_rows = await repository.list_jargons(
            search="", status="confirmed", scope_id="group-a", page=1, page_size=20
        )
        assert len(confirmed) == 1
        assert confirmed[0].status.value == "verified"
        assert confirmed_rows["total"] == 1

        assert await repository.apply_jargon_action(entry_id, "reject")
        assert await repository.list_injectable_terms("chat", "group-a", 0.75) == []
        detail = await repository.get_jargon_detail(entry_id)
        assert detail is not None
        assert detail["entry"]["status"] == "rejected"

    asyncio.run(scenario())


def test_protocol_logs_preserve_success_and_failure_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        success_context = _context("group-a")
        failure_context = _context("group-a", request_id="req-2", message_id="msg-2")
        await repository.record_protocol(
            success_context,
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="<AgentResponse />",
            model="test-model",
            duration_ms=12,
        )
        await repository.record_protocol(
            failure_context,
            success=False,
            action="",
            failure_code="malformed_xml",
            failure_detail="missing close tag",
            raw_output="<AgentResponse>",
            model="test-model",
            duration_ms=-1,
        )

        logs = await repository.list_protocol_logs(page=1, page_size=20)
        overview = await repository.get_overview()

        assert logs["total"] == 2
        by_request = {item["request_id"]: item for item in logs["items"]}
        assert by_request["req-1"]["success"] == 1
        assert by_request["req-1"]["action"] == "Reply"
        assert by_request["req-2"]["success"] == 0
        assert by_request["req-2"]["failure_code"] == "malformed_xml"
        assert by_request["req-2"]["failure_detail"] == "missing close tag"
        assert by_request["req-2"]["duration_ms"] == 0
        assert overview["protocol_samples"] == 2
        assert len(overview["protocol_trend"]) == 7
        assert sum(item["total"] for item in overview["protocol_trend"]) == 2

    asyncio.run(scenario())
