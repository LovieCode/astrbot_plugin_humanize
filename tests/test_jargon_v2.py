from __future__ import annotations

import asyncio
from pathlib import Path

from astrbot_plugin_humanize.humanize.domain.models import MessageContext, UnknownTerm
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository


def _context(
    user_text: str,
    *,
    request_id: str,
    message_id: str,
    scope_id: str = "group-a",
) -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type="group",
        scope_id=scope_id,
        message_id=message_id,
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text,
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


def _term(word: str, guess: str, confidence: float = 0.9) -> UnknownTerm:
    return UnknownTerm(
        word=word,
        guess=guess,
        confidence=confidence,
        reason="当前消息提供了语境证据",
    )


async def _repository(db_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


def test_different_meanings_create_senses_without_overwriting(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        first = await repository.ingest_unknown_terms(
            _context("这个梗是指很强", request_id="req-1", message_id="msg-1"),
            [_term("这个梗", "用于称赞某件事很强")],
            0.75,
            20,
        )
        await repository.ingest_unknown_terms(
            _context("这个梗也可能是反讽", request_id="req-2", message_id="msg-2"),
            [_term("这个梗", "用于反讽某件事表现很差", 0.88)],
            0.75,
            20,
        )

        detail = await repository.get_jargon_detail(first[0])

        assert detail is not None
        assert {sense["meaning"] for sense in detail["senses"]} == {
            "用于称赞某件事很强",
            "用于反讽某件事表现很差",
        }
        assert detail["entry"]["status"] == "ambiguous"
        assert detail["entry"]["has_conflict"] is True
        assert {item["sense_id"] for item in detail["evidence"]} == {
            sense["id"] for sense in detail["senses"]
        }
        assert await repository.list_injectable_terms("group", "group-a", 0.75) == []

    asyncio.run(scenario())


def test_verified_sense_survives_new_conflicting_guess(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("开香槟表示庆祝", request_id="req-1", message_id="msg-1"),
            [_term("开香槟", "提前庆祝事情成功")],
            0.75,
            20,
        )
        assert await repository.apply_jargon_action(entry_id, "confirm")

        await repository.ingest_unknown_terms(
            _context("开香槟也可能是在嘲讽", request_id="req-2", message_id="msg-2"),
            [_term("开香槟", "用于嘲讽对方过早乐观", 0.99)],
            0.75,
            20,
        )
        detail = await repository.get_jargon_detail(entry_id)
        injectable = await repository.list_injectable_terms("group", "group-a", 0.75)

        assert detail is not None
        verified = [
            sense for sense in detail["senses"] if sense["status"] == "verified"
        ]
        assert [sense["meaning"] for sense in verified] == ["提前庆祝事情成功"]
        assert detail["entry"]["status"] == "verified"
        assert detail["entry"]["has_conflict"] is True
        assert len(injectable) == 1
        assert [sense.meaning for sense in injectable[0].senses] == ["提前庆祝事情成功"]

    asyncio.run(scenario())


def test_alias_resolves_to_existing_entry_and_match_rules_are_applied(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("永远滴神", request_id="req-1", message_id="msg-1"),
            [_term("永远滴神", "用于表示强烈称赞")],
            0.75,
            20,
        )
        assert await repository.apply_jargon_action(
            entry_id,
            "replace_aliases",
            payload={"aliases": ["yyds"]},
        )
        assert await repository.apply_jargon_action(
            entry_id,
            "update_entry",
            payload={
                "enabled": True,
                "match_mode": "smart",
                "case_sensitive": False,
            },
        )

        changed = await repository.ingest_unknown_terms(
            _context("YYDS", request_id="req-2", message_id="msg-2"),
            [_term("YYDS", "用于表示强烈称赞")],
            0.75,
            20,
        )
        rows = await repository.list_jargons(
            search="yyds",
            status="",
            scope_id="group-a",
            scope_type="group",
            page=1,
            page_size=20,
        )
        injectable = await repository.list_injectable_terms("group", "group-a", 0.75)

        assert changed == [entry_id]
        assert rows["total"] == 1
        assert rows["items"][0]["alias_count"] == 1
        assert injectable[0].aliases == ("yyds",)

    asyncio.run(scenario())


def test_admin_can_manage_individual_senses(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("上岸指成功", request_id="req-1", message_id="msg-1"),
            [_term("上岸", "考试或求职成功")],
            0.75,
            20,
        )
        assert await repository.apply_jargon_action(
            entry_id,
            "create_sense",
            payload={"meaning": "摆脱困难处境"},
        )
        detail = await repository.get_jargon_detail(entry_id)
        assert detail is not None
        created = next(
            sense for sense in detail["senses"] if sense["meaning"] == "摆脱困难处境"
        )

        assert await repository.apply_jargon_action(
            entry_id,
            "confirm_sense",
            payload={"sense_id": created["id"], "preferred": True},
        )
        updated = await repository.get_jargon_detail(entry_id)

        assert updated is not None
        assert updated["entry"]["preferred_sense_id"] == created["id"]
        assert updated["entry"]["meaning"] == "摆脱困难处境"
        assert (
            next(sense for sense in updated["senses"] if sense["id"] == created["id"])[
                "status"
            ]
            == "verified"
        )

    asyncio.run(scenario())
