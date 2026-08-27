from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import (
    JargonStatus,
    KnownSense,
    KnownTerm,
)
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.jargon.normalizer import term_matches
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.repositories.sqlite import (
    _CONTEXT_SCHEMA,
    _SCHEMA,
    _SCHEMA_VERSION,
    SQLiteRepository,
)
from test_jargon_v2 import _context, _term


async def _repository(db_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return repository


def test_multiple_verified_senses_share_one_envelope_term(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("上岸表示成功", request_id="req-1", message_id="msg-1"),
            [_term("上岸", "考试或求职成功")],
            0.75,
            20,
        )
        await repository.ingest_unknown_terms(
            _context("上岸也指离开困境", request_id="req-2", message_id="msg-2"),
            [_term("上岸", "摆脱困难处境")],
            0.75,
            20,
        )
        detail = await repository.get_jargon_detail(entry_id)
        assert detail is not None
        for index, sense in enumerate(detail["senses"]):
            assert await repository.apply_jargon_action(
                entry_id,
                "confirm_sense",
                payload={"sense_id": sense["id"], "preferred": index == 0},
            )

        injectable = await repository.list_injectable_terms("group", "group-a", 0.75)
        confirmed_detail = await repository.get_jargon_detail(entry_id)
        envelope = EnvelopeBuilder(PluginConfig()).build_known_terms_xml(
            tuple(injectable)
        )

        assert len(injectable) == 1
        assert len(injectable[0].senses) == 2
        assert confirmed_detail is not None
        assert confirmed_detail["entry"]["has_conflict"] is False
        assert envelope.count("<Term>") == 2
        assert "上岸：考试或求职成功" in envelope
        assert "上岸：摆脱困难处境" in envelope

    asyncio.run(scenario())


def test_merge_sense_moves_evidence_and_resolves_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("开香槟表示庆祝", request_id="req-1", message_id="msg-1"),
            [_term("开香槟", "提前庆祝事情成功")],
            0.75,
            20,
        )
        await repository.ingest_unknown_terms(
            _context("开香槟也可表示嘲讽", request_id="req-2", message_id="msg-2"),
            [_term("开香槟", "嘲讽对方过早乐观")],
            0.75,
            20,
        )
        detail = await repository.get_jargon_detail(entry_id)
        assert detail is not None
        source, target = detail["senses"]

        assert await repository.apply_jargon_action(
            entry_id,
            "merge_sense",
            payload={
                "source_sense_id": source["id"],
                "target_sense_id": target["id"],
            },
        )
        merged = await repository.get_jargon_detail(entry_id)

        assert merged is not None
        assert [sense["id"] for sense in merged["senses"]] == [target["id"]]
        assert {evidence["sense_id"] for evidence in merged["evidence"]} == {
            target["id"]
        }
        assert merged["entry"]["has_conflict"] is False

    asyncio.run(scenario())


def test_match_modes_aliases_and_case_are_deterministic() -> None:
    sense = KnownSense(
        sense_id=1,
        meaning="强烈称赞",
        confidence=1.0,
        status=JargonStatus.VERIFIED,
    )
    term = KnownTerm(
        entry_id=1,
        term="永远滴神",
        normalized_term="永远滴神",
        meaning=sense.meaning,
        confidence=1.0,
        status=JargonStatus.VERIFIED,
        scope_type="group",
        scope_id="group-a",
        senses=(sense,),
        aliases=("YYDS",),
        match_mode="smart",
        case_sensitive=True,
    )

    matcher = JargonMatcher()
    assert matcher.select([term], "YYDS!", max_count=5, char_budget=1_000)
    assert not matcher.select([term], "yyds!", max_count=5, char_budget=1_000)
    assert term_matches("abc", "xabcx", match_mode="contains")
    assert not term_matches("abc", "xabcx", match_mode="smart")
    assert term_matches("abc", "abc", match_mode="exact")
    assert not term_matches("abc", "abc!", match_mode="exact")


def test_conflict_disabled_filters_and_export(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        [entry_id] = await repository.ingest_unknown_terms(
            _context("红温表示生气", request_id="req-1", message_id="msg-1"),
            [_term("红温", "情绪生气")],
            0.75,
            20,
        )
        await repository.ingest_unknown_terms(
            _context("红温也可能是设备过热", request_id="req-2", message_id="msg-2"),
            [_term("红温", "设备温度过高")],
            0.75,
            20,
        )

        conflict = await repository.list_jargons(
            search="",
            status="conflict",
            scope_id="group-a",
            scope_type="group",
            page=1,
            page_size=20,
        )
        overview = await repository.get_overview()
        assert conflict["total"] == 1
        assert overview["pending"] == 1
        assert await repository.apply_jargon_action(
            entry_id, "update_entry", payload={"enabled": False}
        )
        disabled = await repository.list_jargons(
            search="",
            status="disabled",
            scope_id="group-a",
            scope_type="group",
            page=1,
            page_size=20,
        )
        exported = await repository.export_jargons(
            search="红温",
            scope_type="group",
            scope_id="group-a",
            status="disabled",
        )

        assert disabled["total"] == 1
        disabled_overview = await repository.get_overview()
        assert disabled_overview["learned"] == 0
        assert disabled_overview["pending"] == 0
        assert exported["schema_version"] == 2
        assert exported["total"] == 1
        assert len(exported["items"][0]["senses"]) == 2

    asyncio.run(scenario())


def test_v3_migration_preserves_entry_and_sense_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "humanize.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(
            _CONTEXT_SCHEMA.replace(
                "    content_chars INTEGER NOT NULL,\n"
                "    preview_truncated INTEGER NOT NULL,\n"
                "    content_snapshot TEXT NOT NULL,\n"
                "    snapshot_complete INTEGER NOT NULL,\n",
                "",
            )
        )
        conn.execute(
            """
            INSERT INTO jargon_entries (
                id, scope_type, scope_id, term, normalized_term, status,
                occurrence_count, confidence, first_seen_at, last_seen_at,
                created_at, updated_at
            ) VALUES (41, 'group', 'group-a', '旧词', '旧词', 'provisional',
                      1, 0.8, '2026-01-01', '2026-01-01', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute(
            """
            INSERT INTO jargon_senses (
                id, entry_id, meaning, confidence, status, version,
                created_by, reason, created_at, updated_at
            ) VALUES (73, 41, '旧含义', 0.8, 'provisional', 1,
                      'legacy', '', '2026-01-01', '2026-01-01')
            """
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

    async def scenario() -> None:
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        detail = await repository.get_jargon_detail(41)

        assert detail is not None
        assert detail["entry"]["id"] == 41
        assert detail["entry"]["preferred_sense_id"] == 73
        assert detail["senses"][0]["id"] == 73
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
            context_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(humanize_context_sections)"
                ).fetchall()
            }
            assert {"content_chars", "preview_truncated"} <= context_columns

    asyncio.run(scenario())


def test_v4_migration_resumes_after_partial_column_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "humanize.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.executescript(_CONTEXT_SCHEMA)
        conn.execute(
            "ALTER TABLE jargon_entries ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
        conn.execute(
            "ALTER TABLE jargon_entries ADD COLUMN match_mode TEXT NOT NULL DEFAULT 'smart'"
        )
        conn.execute(
            "ALTER TABLE jargon_entries ADD COLUMN case_sensitive INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()

    async def scenario() -> None:
        repository = SQLiteRepository(db_path)
        await repository.initialize()
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(jargon_entries)").fetchall()
            }
            assert "preferred_sense_id" in columns

    asyncio.run(scenario())


def test_protocol_overview_counts_latest_attempt_per_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = await _repository(tmp_path / "humanize.db")
        context = _context("测试", request_id="req-repair", message_id="msg-1")
        await repository.record_protocol(
            context,
            success=False,
            action="",
            failure_code="invalid_control_header",
            failure_detail="broken",
            raw_output="broken",
            model="test",
            duration_ms=1,
        )
        await repository.record_protocol(
            context,
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="tool",
            model="test",
            duration_ms=2,
            stage="tool",
        )
        await repository.record_protocol(
            context,
            success=True,
            action="Reply",
            failure_code="",
            failure_detail="",
            raw_output="fixed",
            model="test",
            duration_ms=3,
        )

        overview = await repository.get_overview()
        logs = await repository.list_protocol_logs(page=1, page_size=20)

        assert overview["protocol_samples"] == 1
        assert overview["protocol_success_rate"] == 100.0
        assert overview["action_distribution"] == {"Reply": 1, "No Reply": 0}
        assert [item["stage"] for item in logs["items"]] == [
            "final",
            "tool",
            "final",
        ]
        assert [item["is_final"] for item in logs["items"]] == [True, False, False]

    asyncio.run(scenario())
