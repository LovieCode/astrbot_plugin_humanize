from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from ..domain.control import (
    BehaviorPolicy,
    DynamicState,
    ExpressionConfig,
    PersonaConfig,
)
from ..domain.models import JargonStatus, KnownTerm, MessageContext, UnknownTerm
from ..jargon.normalizer import normalize_term

T = TypeVar("T")


_SCHEMA_VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS jargon_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    term TEXT NOT NULL,
    normalized_term TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_id, normalized_term)
);

CREATE TABLE IF NOT EXISTS jargon_senses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL UNIQUE REFERENCES jargon_entries(id) ON DELETE CASCADE,
    meaning TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jargon_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    source_text TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 1,
    UNIQUE(entry_id, message_id, content_hash)
);

CREATE TABLE IF NOT EXISTS jargon_inference_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    proposed_meaning TEXT NOT NULL,
    confidence REAL NOT NULL,
    reason TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jargon_injection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    entry_id INTEGER REFERENCES jargon_entries(id) ON DELETE SET NULL,
    selected INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS protocol_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    success INTEGER NOT NULL,
    action TEXT NOT NULL,
    failure_code TEXT NOT NULL,
    failure_detail TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    model TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jargon_entries_scope
    ON jargon_entries(scope_type, scope_id, status, confidence);
CREATE INDEX IF NOT EXISTS idx_jargon_evidence_entry_time
    ON jargon_evidence(entry_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_injection_request
    ON jargon_injection_logs(request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_protocol_created
    ON protocol_logs(created_at DESC);
"""

_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_persona (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    identity TEXT NOT NULL,
    traits_json TEXT NOT NULL,
    values_json TEXT NOT NULL,
    boundaries_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mood REAL NOT NULL,
    energy REAL NOT NULL,
    interest REAL NOT NULL,
    stress REAL NOT NULL,
    focus TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_behavior_policy (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL,
    allow_no_reply INTEGER NOT NULL,
    allow_follow_up INTEGER NOT NULL,
    allow_proactive INTEGER NOT NULL,
    allow_end_topic INTEGER NOT NULL,
    reply_threshold REAL NOT NULL,
    follow_up_threshold REAL NOT NULL,
    proactive_threshold REAL NOT NULL,
    end_topic_threshold REAL NOT NULL,
    cooldown_minutes INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_expression (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL,
    provider TEXT NOT NULL,
    mode TEXT NOT NULL,
    profile TEXT NOT NULL,
    integration_status TEXT NOT NULL,
    last_checked_at TEXT,
    last_error TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_control_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_control_audit_created
    ON humanize_control_audit(created_at DESC, id DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SQLiteRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        raw_log_chars: int = 4_000,
        log_retention_days: int = 7,
    ) -> None:
        self._db_path = db_path
        self._raw_log_chars = raw_log_chars
        self._log_retention_days = log_retention_days
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(self._migrate)

    async def get_control_section(self, section: str) -> dict[str, Any]:
        """Read one non-relationship humanization control section."""
        if section not in {"persona", "state", "behavior", "expression"}:
            raise ValueError("unsupported control section")

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                {
                    "persona": "SELECT * FROM humanize_persona WHERE id = 1",
                    "state": "SELECT * FROM humanize_state WHERE id = 1",
                    "behavior": "SELECT * FROM humanize_behavior_policy WHERE id = 1",
                    "expression": "SELECT * FROM humanize_expression WHERE id = 1",
                }[section]
            ).fetchone()
            if row is None:
                raise RuntimeError(f"missing control defaults for {section}")
            if section == "persona":
                result = PersonaConfig.from_row(row).as_dict()
            elif section == "state":
                result = DynamicState.from_row(row).as_dict()
            elif section == "behavior":
                result = BehaviorPolicy.from_row(row).as_dict()
            else:
                result = ExpressionConfig.from_row(row).as_dict()
            result["updated_at"] = str(row["updated_at"])
            return result

        return await self._run(operation)

    async def get_control_overview(self) -> dict[str, Any]:
        """Read all non-relationship controls and recent audit metadata."""

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            rows = {
                "persona": conn.execute(
                    "SELECT * FROM humanize_persona WHERE id = 1"
                ).fetchone(),
                "state": conn.execute(
                    "SELECT * FROM humanize_state WHERE id = 1"
                ).fetchone(),
                "behavior": conn.execute(
                    "SELECT * FROM humanize_behavior_policy WHERE id = 1"
                ).fetchone(),
                "expression": conn.execute(
                    "SELECT * FROM humanize_expression WHERE id = 1"
                ).fetchone(),
            }
            if any(row is None for row in rows.values()):
                raise RuntimeError("missing humanization control defaults")
            audit = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       MAX(created_at) AS last_changed_at,
                       MAX(CASE WHEN action = 'reset' THEN created_at END) AS last_reset_at
                FROM humanize_control_audit
                """
            ).fetchone()
            persona = PersonaConfig.from_row(rows["persona"]).as_dict()
            persona["updated_at"] = str(rows["persona"]["updated_at"])
            state = DynamicState.from_row(rows["state"]).as_dict()
            state["updated_at"] = str(rows["state"]["updated_at"])
            behavior = BehaviorPolicy.from_row(rows["behavior"]).as_dict()
            behavior["updated_at"] = str(rows["behavior"]["updated_at"])
            expression = ExpressionConfig.from_row(rows["expression"]).as_dict()
            expression["updated_at"] = str(rows["expression"]["updated_at"])
            audit_rows = conn.execute(
                """
                SELECT id, section, action, actor, reason,
                       before_json, after_json, created_at
                FROM humanize_control_audit
                ORDER BY created_at DESC, id DESC LIMIT 20
                """
            ).fetchall()
            audit_items = []
            for row in audit_rows:
                item = dict(row)
                item["before"] = json.loads(item.pop("before_json"))
                item["after"] = json.loads(item.pop("after_json"))
                audit_items.append(item)
            return {
                "persona": persona,
                "state": state,
                "behavior": behavior,
                "expression": expression,
                "audit": audit_items,
                "audit_meta": {
                    "total": int(audit["total"] or 0),
                    "last_changed_at": audit["last_changed_at"],
                    "last_reset_at": audit["last_reset_at"],
                },
            }

        return await self._run(operation)

    async def update_control_section(
        self,
        section: str,
        value: dict[str, Any],
        *,
        actor: str = "web_admin",
        reason: str = "web update",
    ) -> dict[str, Any]:
        """Validate and persist one control section with an audit record."""
        if section not in {"persona", "state", "behavior", "expression"}:
            raise ValueError("unsupported control section")
        clean_reason = str(reason or "web update").strip()[:500]
        clean_actor = str(actor or "web_admin").strip()[:120] or "web_admin"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            now = _now()
            row = conn.execute(
                {
                    "persona": "SELECT * FROM humanize_persona WHERE id = 1",
                    "state": "SELECT * FROM humanize_state WHERE id = 1",
                    "behavior": "SELECT * FROM humanize_behavior_policy WHERE id = 1",
                    "expression": "SELECT * FROM humanize_expression WHERE id = 1",
                }[section]
            ).fetchone()
            if row is None:
                raise RuntimeError(f"missing control defaults for {section}")
            if section == "persona":
                parsed = PersonaConfig.from_mapping(value)
                before = PersonaConfig.from_row(row).as_dict()
                conn.execute(
                    """
                    UPDATE humanize_persona
                    SET name = ?, identity = ?, traits_json = ?, values_json = ?,
                        boundaries_json = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        parsed.name,
                        parsed.identity,
                        json.dumps(parsed.traits, ensure_ascii=False),
                        json.dumps(parsed.values, ensure_ascii=False),
                        json.dumps(parsed.boundaries, ensure_ascii=False),
                        now,
                    ),
                )
                after = parsed.as_dict()
            elif section == "state":
                parsed = DynamicState.from_mapping(value)
                before = DynamicState.from_row(row).as_dict()
                conn.execute(
                    """
                    UPDATE humanize_state
                    SET mood = ?, energy = ?, interest = ?, stress = ?, focus = ?,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        parsed.mood,
                        parsed.energy,
                        parsed.interest,
                        parsed.stress,
                        parsed.focus,
                        now,
                    ),
                )
                after = parsed.as_dict()
            elif section == "behavior":
                parsed = BehaviorPolicy.from_mapping(value)
                before = BehaviorPolicy.from_row(row).as_dict()
                conn.execute(
                    """
                    UPDATE humanize_behavior_policy
                    SET enabled = ?, allow_no_reply = ?, allow_follow_up = ?,
                        allow_proactive = ?, allow_end_topic = ?,
                        reply_threshold = ?, follow_up_threshold = ?,
                        proactive_threshold = ?, end_topic_threshold = ?,
                        cooldown_minutes = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        int(parsed.enabled),
                        int(parsed.allow_no_reply),
                        int(parsed.allow_follow_up),
                        int(parsed.allow_proactive),
                        int(parsed.allow_end_topic),
                        parsed.reply_threshold,
                        parsed.follow_up_threshold,
                        parsed.proactive_threshold,
                        parsed.end_topic_threshold,
                        parsed.cooldown_minutes,
                        now,
                    ),
                )
                after = parsed.as_dict()
            else:
                parsed = ExpressionConfig.from_mapping(value)
                before = ExpressionConfig.from_row(row).as_dict()
                conn.execute(
                    """
                    UPDATE humanize_expression
                    SET enabled = ?, provider = ?, mode = ?, profile = ?,
                        integration_status = ?, last_checked_at = ?, last_error = ?,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        int(parsed.enabled),
                        parsed.provider,
                        parsed.mode,
                        parsed.profile,
                        parsed.integration_status,
                        parsed.last_checked_at,
                        parsed.last_error,
                        now,
                    ),
                )
                after = parsed.as_dict()
            after["updated_at"] = now
            conn.execute(
                """
                INSERT INTO humanize_control_audit (
                    section, action, actor, reason, before_json, after_json, created_at
                ) VALUES (?, 'update', ?, ?, ?, ?, ?)
                """,
                (
                    section,
                    clean_actor,
                    clean_reason,
                    json.dumps(before, ensure_ascii=False, sort_keys=True),
                    json.dumps(after, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            conn.commit()
            return after

        return await self._run(operation)

    async def reset_control(
        self, section: str, *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Reset one or all controls and record before/after values."""
        sections = (
            ("persona", "state", "behavior", "expression")
            if section == "all"
            else (section,)
        )
        if any(
            item not in {"persona", "state", "behavior", "expression"}
            for item in sections
        ):
            raise ValueError("unsupported control section")
        clean_reason = str(reason or "manual reset").strip()[:500]
        clean_actor = str(actor or "web_admin").strip()[:120] or "web_admin"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            now = _now()
            result: dict[str, Any] = {}
            for item in sections:
                row = conn.execute(
                    {
                        "persona": "SELECT * FROM humanize_persona WHERE id = 1",
                        "state": "SELECT * FROM humanize_state WHERE id = 1",
                        "behavior": "SELECT * FROM humanize_behavior_policy WHERE id = 1",
                        "expression": "SELECT * FROM humanize_expression WHERE id = 1",
                    }[item]
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"missing control defaults for {item}")
                if item == "persona":
                    before = PersonaConfig.from_row(row).as_dict()
                    defaults = PersonaConfig()
                    conn.execute(
                        "UPDATE humanize_persona SET name = ?, identity = ?, traits_json = ?, values_json = ?, boundaries_json = ?, updated_at = ? WHERE id = 1",
                        (
                            defaults.name,
                            defaults.identity,
                            json.dumps(defaults.traits, ensure_ascii=False),
                            json.dumps(defaults.values, ensure_ascii=False),
                            json.dumps(defaults.boundaries, ensure_ascii=False),
                            now,
                        ),
                    )
                    after = defaults.as_dict()
                elif item == "state":
                    before = DynamicState.from_row(row).as_dict()
                    defaults = DynamicState()
                    conn.execute(
                        "UPDATE humanize_state SET mood = ?, energy = ?, interest = ?, stress = ?, focus = ?, updated_at = ? WHERE id = 1",
                        (
                            defaults.mood,
                            defaults.energy,
                            defaults.interest,
                            defaults.stress,
                            defaults.focus,
                            now,
                        ),
                    )
                    after = defaults.as_dict()
                elif item == "behavior":
                    before = BehaviorPolicy.from_row(row).as_dict()
                    defaults = BehaviorPolicy()
                    conn.execute(
                        "UPDATE humanize_behavior_policy SET enabled = ?, allow_no_reply = ?, allow_follow_up = ?, allow_proactive = ?, allow_end_topic = ?, reply_threshold = ?, follow_up_threshold = ?, proactive_threshold = ?, end_topic_threshold = ?, cooldown_minutes = ?, updated_at = ? WHERE id = 1",
                        (
                            int(defaults.enabled),
                            int(defaults.allow_no_reply),
                            int(defaults.allow_follow_up),
                            int(defaults.allow_proactive),
                            int(defaults.allow_end_topic),
                            defaults.reply_threshold,
                            defaults.follow_up_threshold,
                            defaults.proactive_threshold,
                            defaults.end_topic_threshold,
                            defaults.cooldown_minutes,
                            now,
                        ),
                    )
                    after = defaults.as_dict()
                else:
                    before = ExpressionConfig.from_row(row).as_dict()
                    defaults = ExpressionConfig()
                    conn.execute(
                        "UPDATE humanize_expression SET enabled = ?, provider = ?, mode = ?, profile = ?, integration_status = ?, last_checked_at = ?, last_error = ?, updated_at = ? WHERE id = 1",
                        (
                            int(defaults.enabled),
                            defaults.provider,
                            defaults.mode,
                            defaults.profile,
                            defaults.integration_status,
                            defaults.last_checked_at,
                            defaults.last_error,
                            now,
                        ),
                    )
                    after = defaults.as_dict()
                after["updated_at"] = now
                conn.execute(
                    """
                    INSERT INTO humanize_control_audit (
                        section, action, actor, reason, before_json, after_json, created_at
                    ) VALUES (?, 'reset', ?, ?, ?, ?, ?)
                    """,
                    (
                        item,
                        clean_actor,
                        clean_reason,
                        json.dumps(before, ensure_ascii=False, sort_keys=True),
                        json.dumps(after, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
                result[item] = after
            conn.commit()
            return result

        return await self._run(operation)

    async def list_control_audit(self, *, page: int, page_size: int) -> dict[str, Any]:
        """List control changes newest first for the management UI."""

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM humanize_control_audit"
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, section, action, actor, reason, before_json, after_json, created_at
                FROM humanize_control_audit
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["before"] = json.loads(item.pop("before_json"))
                item["after"] = json.loads(item.pop("after_json"))
                items.append(item)
            return {"items": items, "total": total}

        return await self._run(operation)

    async def list_injectable_terms(
        self,
        scope_type: str,
        scope_id: str,
        min_confidence: float,
        limit: int = 500,
    ) -> list[KnownTerm]:
        def operation(conn: sqlite3.Connection) -> list[KnownTerm]:
            rows = conn.execute(
                """
                SELECT e.id, e.term, e.normalized_term, e.status, e.confidence,
                       e.scope_type, e.scope_id, s.meaning, s.confidence AS sense_confidence
                FROM jargon_entries e
                JOIN jargon_senses s ON s.entry_id = e.id
                WHERE e.scope_type = ? AND e.scope_id = ?
                  AND e.status IN ('provisional', 'verified')
                  AND e.confidence >= ? AND s.meaning <> ''
                ORDER BY CASE e.status WHEN 'verified' THEN 0 ELSE 1 END,
                         e.confidence DESC, LENGTH(e.normalized_term) DESC
                LIMIT ?
                """,
                (scope_type, scope_id, min_confidence, max(1, limit)),
            ).fetchall()
            return [
                KnownTerm(
                    entry_id=int(row["id"]),
                    term=str(row["term"]),
                    normalized_term=str(row["normalized_term"]),
                    meaning=str(row["meaning"]),
                    confidence=float(row["sense_confidence"]),
                    status=JargonStatus(str(row["status"])),
                    scope_type=str(row["scope_type"]),
                    scope_id=str(row["scope_id"]),
                )
                for row in rows
            ]

        return await self._run(operation)

    async def ingest_unknown_terms(
        self,
        context: MessageContext,
        terms: Sequence[UnknownTerm],
        provisional_threshold: float,
        max_evidence: int,
    ) -> list[int]:
        if not terms:
            return []

        def operation(conn: sqlite3.Connection) -> list[int]:
            now = _now()
            content_hash = hashlib.sha256(
                context.user_text.encode("utf-8", errors="replace")
            ).hexdigest()
            changed: list[int] = []
            conn.execute("BEGIN IMMEDIATE")
            try:
                for term in terms:
                    normalized = normalize_term(term.word)
                    desired_status = (
                        JargonStatus.PROVISIONAL.value
                        if term.confidence >= provisional_threshold
                        else JargonStatus.CANDIDATE.value
                    )
                    conn.execute(
                        """
                        INSERT INTO jargon_entries (
                            scope_type, scope_id, term, normalized_term, status,
                            occurrence_count, confidence, first_seen_at, last_seen_at,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                        ON CONFLICT(scope_type, scope_id, normalized_term)
                        DO UPDATE SET term = excluded.term, last_seen_at = excluded.last_seen_at,
                                      updated_at = excluded.updated_at
                        """,
                        (
                            context.scope_type,
                            context.scope_id,
                            term.word.strip(),
                            normalized,
                            desired_status,
                            now,
                            now,
                            now,
                            now,
                        ),
                    )
                    entry = conn.execute(
                        """
                        SELECT id, status, confidence FROM jargon_entries
                        WHERE scope_type = ? AND scope_id = ? AND normalized_term = ?
                        """,
                        (context.scope_type, context.scope_id, normalized),
                    ).fetchone()
                    if entry is None or entry["status"] == JargonStatus.REJECTED.value:
                        continue
                    entry_id = int(entry["id"])
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO jargon_evidence (
                            entry_id, message_id, content_hash, sender_id,
                            source_text, observed_at, valid
                        ) VALUES (?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            entry_id,
                            context.message_id,
                            content_hash,
                            context.sender_id,
                            context.user_text[:2_000],
                            now,
                        ),
                    )
                    if cursor.rowcount == 0:
                        continue

                    current_status = str(entry["status"])
                    next_status = current_status
                    if current_status not in {
                        JargonStatus.VERIFIED.value,
                        JargonStatus.AMBIGUOUS.value,
                    }:
                        next_status = desired_status
                    next_confidence = max(float(entry["confidence"]), term.confidence)
                    conn.execute(
                        """
                        UPDATE jargon_entries
                        SET occurrence_count = occurrence_count + 1,
                            confidence = ?, status = ?, last_seen_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (next_confidence, next_status, now, now, entry_id),
                    )

                    sense = conn.execute(
                        "SELECT * FROM jargon_senses WHERE entry_id = ?", (entry_id,)
                    ).fetchone()
                    accepted = 0
                    if sense is None:
                        conn.execute(
                            """
                            INSERT INTO jargon_senses (
                                entry_id, meaning, confidence, status, version,
                                created_by, reason, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 1, 'llm_protocol', ?, ?, ?)
                            """,
                            (
                                entry_id,
                                term.guess[:1_000],
                                term.confidence,
                                next_status,
                                term.reason[:1_000],
                                now,
                                now,
                            ),
                        )
                        accepted = 1
                    elif current_status != JargonStatus.VERIFIED.value:
                        same_meaning = normalize_term(
                            str(sense["meaning"])
                        ) == normalize_term(term.guess)
                        if same_meaning or term.confidence >= float(
                            sense["confidence"]
                        ):
                            conn.execute(
                                """
                                UPDATE jargon_senses
                                SET meaning = ?, confidence = ?, status = ?,
                                    version = version + 1, created_by = 'llm_protocol',
                                    reason = ?, updated_at = ?
                                WHERE entry_id = ?
                                """,
                                (
                                    term.guess[:1_000],
                                    max(float(sense["confidence"]), term.confidence),
                                    next_status,
                                    term.reason[:1_000],
                                    now,
                                    entry_id,
                                ),
                            )
                            accepted = 1

                    conn.execute(
                        """
                        INSERT INTO jargon_inference_logs (
                            entry_id, message_id, proposed_meaning, confidence,
                            reason, accepted, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            context.message_id,
                            term.guess[:1_000],
                            term.confidence,
                            term.reason[:1_000],
                            accepted,
                            now,
                        ),
                    )
                    self._prune_evidence(conn, entry_id, max_evidence)
                    changed.append(entry_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return changed

        return await self._run(operation)

    async def record_injections(
        self,
        context: MessageContext,
        selected: Sequence[KnownTerm],
        reason: str,
    ) -> None:
        if not selected:
            return

        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            conn.executemany(
                """
                INSERT INTO jargon_injection_logs (
                    request_id, scope_type, scope_id, message_id,
                    entry_id, selected, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                [
                    (
                        context.request_id,
                        context.scope_type,
                        context.scope_id,
                        context.message_id,
                        term.entry_id,
                        reason,
                        now,
                    )
                    for term in selected
                ],
            )
            cutoff = (
                datetime.now(UTC) - timedelta(days=self._log_retention_days)
            ).isoformat(timespec="seconds")
            conn.execute(
                "DELETE FROM jargon_injection_logs WHERE created_at < ?", (cutoff,)
            )
            conn.commit()

        await self._run(operation)

    async def record_protocol(
        self,
        context: MessageContext,
        *,
        success: bool,
        action: str,
        failure_code: str,
        failure_detail: str,
        raw_output: str,
        model: str,
        duration_ms: int,
    ) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            conn.execute(
                """
                INSERT INTO protocol_logs (
                    request_id, scope_type, scope_id, message_id, sender_id,
                    success, action, failure_code, failure_detail, raw_output,
                    model, duration_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context.request_id,
                    context.scope_type,
                    context.scope_id,
                    context.message_id,
                    context.sender_id,
                    int(success),
                    action,
                    failure_code,
                    failure_detail[:1_000],
                    raw_output[: self._raw_log_chars],
                    model,
                    max(0, duration_ms),
                    now,
                ),
            )
            cutoff = (
                datetime.now(UTC) - timedelta(days=self._log_retention_days)
            ).isoformat(timespec="seconds")
            conn.execute("DELETE FROM protocol_logs WHERE created_at < ?", (cutoff,))
            conn.commit()

        await self._run(operation)

    async def get_overview(self) -> dict[str, Any]:
        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status IN ('candidate', 'provisional') THEN 1 ELSE 0 END) AS pending
                FROM jargon_entries
                WHERE status <> 'rejected'
                """
            ).fetchone()
            start_date = datetime.now(UTC).date() - timedelta(days=6)
            since = f"{start_date.isoformat()}T00:00:00+00:00"
            protocol = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS blocked
                FROM protocol_logs WHERE created_at >= ?
                """,
                (since,),
            ).fetchone()
            total_protocol = int(protocol["total"] or 0)
            success_protocol = int(protocol["success"] or 0)
            success_rate = (
                round(success_protocol * 100 / total_protocol, 1)
                if total_protocol
                else None
            )
            daily_rows = conn.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success
                FROM protocol_logs
                WHERE created_at >= ?
                GROUP BY substr(created_at, 1, 10)
                """,
                (since,),
            ).fetchall()
            daily_by_date = {str(row["day"]): row for row in daily_rows}
            protocol_trend = []
            for offset in range(7):
                day = start_date + timedelta(days=offset)
                row = daily_by_date.get(day.isoformat())
                total = int(row["total"] or 0) if row else 0
                success = int(row["success"] or 0) if row else 0
                protocol_trend.append(
                    {
                        "date": day.isoformat(),
                        "label": day.strftime("%m-%d"),
                        "value": round(success * 100 / total, 1) if total else None,
                        "total": total,
                    }
                )
            scopes = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT scope_id, scope_type, COUNT(*) AS count
                    FROM jargon_entries GROUP BY scope_type, scope_id
                    ORDER BY MAX(updated_at) DESC LIMIT 50
                    """
                ).fetchall()
            ]
            return {
                "learned": int(counts["total"] or 0),
                "pending": int(counts["pending"] or 0),
                "protocol_success_rate": success_rate,
                "protocol_samples": total_protocol,
                "blocked_week": int(protocol["blocked"] or 0),
                "protocol_trend": protocol_trend,
                "scopes": scopes,
            }

        return await self._run(operation)

    async def list_jargons(
        self,
        *,
        search: str,
        status: str,
        scope_id: str,
        page: int,
        page_size: int,
        scope_type: str = "",
    ) -> dict[str, Any]:
        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            conditions = ["1 = 1"]
            params: list[Any] = []
            if search:
                conditions.append("(e.term LIKE ? OR s.meaning LIKE ?)")
                query = f"%{search}%"
                params.extend([query, query])
            if status:
                status_values = {
                    "candidate": ("candidate", "provisional"),
                    "pending": ("candidate", "provisional"),
                    "confirmed": ("verified",),
                }.get(status, (status,))
                placeholders = ", ".join("?" for _ in status_values)
                conditions.append(f"e.status IN ({placeholders})")
                params.extend(status_values)
            if scope_id:
                conditions.append("e.scope_id = ?")
                params.append(scope_id)
            if scope_type:
                conditions.append("e.scope_type = ?")
                params.append(scope_type)
            where = " AND ".join(conditions)
            total = conn.execute(
                f"""
                SELECT COUNT(*) AS count FROM jargon_entries e
                LEFT JOIN jargon_senses s ON s.entry_id = e.id
                WHERE {where}
                """,
                params,
            ).fetchone()["count"]
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT e.id, e.term, e.normalized_term, e.scope_type, e.scope_id,
                       e.status, e.occurrence_count, e.confidence, e.last_seen_at,
                       COALESCE(s.meaning, '') AS meaning,
                       COALESCE(s.version, 0) AS sense_version
                FROM jargon_entries e
                LEFT JOIN jargon_senses s ON s.entry_id = e.id
                WHERE {where}
                ORDER BY e.updated_at DESC, e.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
            return {"items": [dict(row) for row in rows], "total": int(total)}

        return await self._run(operation)

    async def get_jargon_detail(self, entry_id: int) -> dict[str, Any] | None:
        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            entry = conn.execute(
                """
                SELECT e.*, COALESCE(s.meaning, '') AS meaning,
                       COALESCE(s.reason, '') AS sense_reason,
                       COALESCE(s.version, 0) AS sense_version,
                       COALESCE(s.created_by, '') AS sense_created_by
                FROM jargon_entries e
                LEFT JOIN jargon_senses s ON s.entry_id = e.id
                WHERE e.id = ?
                """,
                (entry_id,),
            ).fetchone()
            if entry is None:
                return None
            evidence = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, message_id, sender_id, source_text, observed_at, valid
                    FROM jargon_evidence WHERE entry_id = ?
                    ORDER BY observed_at DESC, id DESC LIMIT 20
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            inferences = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT proposed_meaning, confidence, reason, accepted, created_at
                    FROM jargon_inference_logs WHERE entry_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT 20
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            injections = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT request_id, scope_id, selected, reason, created_at
                    FROM jargon_injection_logs WHERE entry_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT 20
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            return {
                "entry": dict(entry),
                "evidence": evidence,
                "inferences": inferences,
                "injections": injections,
            }

        return await self._run(operation)

    async def apply_jargon_action(
        self, entry_id: int, action: str, meaning: str = ""
    ) -> bool:
        def operation(conn: sqlite3.Connection) -> bool:
            now = _now()
            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT id FROM jargon_entries WHERE id = ?", (entry_id,)
                ).fetchone()
                if exists is None:
                    conn.rollback()
                    return False
                if action == "confirm":
                    conn.execute(
                        "UPDATE jargon_entries SET status = 'verified', confidence = MAX(confidence, 1.0), updated_at = ? WHERE id = ?",
                        (now, entry_id),
                    )
                    conn.execute(
                        "UPDATE jargon_senses SET status = 'verified', confidence = MAX(confidence, 1.0), created_by = 'admin', updated_at = ? WHERE entry_id = ?",
                        (now, entry_id),
                    )
                elif action == "reject":
                    conn.execute(
                        "UPDATE jargon_entries SET status = 'rejected', updated_at = ? WHERE id = ?",
                        (now, entry_id),
                    )
                    conn.execute(
                        "UPDATE jargon_senses SET status = 'rejected', created_by = 'admin', updated_at = ? WHERE entry_id = ?",
                        (now, entry_id),
                    )
                elif action == "update":
                    clean = meaning.strip()
                    if not clean or len(clean) > 1_000:
                        raise ValueError("meaning must contain 1 to 1000 characters")
                    conn.execute(
                        "UPDATE jargon_entries SET status = 'verified', confidence = 1.0, updated_at = ? WHERE id = ?",
                        (now, entry_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO jargon_senses (
                            entry_id, meaning, confidence, status, version,
                            created_by, reason, created_at, updated_at
                        ) VALUES (?, ?, 1.0, 'verified', 1, 'admin', 'manual update', ?, ?)
                        ON CONFLICT(entry_id) DO UPDATE SET
                            meaning = excluded.meaning, confidence = 1.0,
                            status = 'verified', version = jargon_senses.version + 1,
                            created_by = 'admin', reason = 'manual update',
                            updated_at = excluded.updated_at
                        """,
                        (entry_id, clean, now, now),
                    )
                elif action == "delete":
                    conn.execute("DELETE FROM jargon_entries WHERE id = ?", (entry_id,))
                else:
                    raise ValueError(f"unsupported action: {action}")
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def list_protocol_logs(self, *, page: int, page_size: int) -> dict[str, Any]:
        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                conn.execute("SELECT COUNT(*) AS count FROM protocol_logs").fetchone()[
                    "count"
                ]
            )
            rows = conn.execute(
                """
                SELECT id, request_id, scope_id, message_id, success, action,
                       failure_code, failure_detail, model, duration_ms, created_at
                FROM protocol_logs
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
            return {"items": [dict(row) for row in rows], "total": total}

        return await self._run(operation)

    async def _run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(self._run_sync, operation)

    def _run_sync(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            return operation(conn)
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {version} is newer than supported {_SCHEMA_VERSION}"
            )
        if version == 0:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            conn.executescript(_CONTROL_SCHEMA)
            now = _now()
            persona = PersonaConfig()
            state = DynamicState()
            behavior = BehaviorPolicy()
            expression = ExpressionConfig()
            conn.execute(
                """
                INSERT OR IGNORE INTO humanize_persona (
                    id, name, identity, traits_json, values_json,
                    boundaries_json, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    persona.name,
                    persona.identity,
                    json.dumps(persona.traits, ensure_ascii=False),
                    json.dumps(persona.values, ensure_ascii=False),
                    json.dumps(persona.boundaries, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO humanize_state (
                    id, mood, energy, interest, stress, focus, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.mood,
                    state.energy,
                    state.interest,
                    state.stress,
                    state.focus,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO humanize_behavior_policy (
                    id, enabled, allow_no_reply, allow_follow_up, allow_proactive,
                    allow_end_topic, reply_threshold, follow_up_threshold,
                    proactive_threshold, end_topic_threshold, cooldown_minutes,
                    updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(behavior.enabled),
                    int(behavior.allow_no_reply),
                    int(behavior.allow_follow_up),
                    int(behavior.allow_proactive),
                    int(behavior.allow_end_topic),
                    behavior.reply_threshold,
                    behavior.follow_up_threshold,
                    behavior.proactive_threshold,
                    behavior.end_topic_threshold,
                    behavior.cooldown_minutes,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO humanize_expression (
                    id, enabled, provider, mode, profile, integration_status,
                    last_checked_at, last_error, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(expression.enabled),
                    expression.provider,
                    expression.mode,
                    expression.profile,
                    expression.integration_status,
                    expression.last_checked_at,
                    expression.last_error,
                    now,
                ),
            )
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            conn.commit()

    @staticmethod
    def _prune_evidence(
        conn: sqlite3.Connection, entry_id: int, max_evidence: int
    ) -> None:
        conn.execute(
            """
            DELETE FROM jargon_evidence
            WHERE entry_id = ? AND id NOT IN (
                SELECT id FROM jargon_evidence
                WHERE entry_id = ? ORDER BY observed_at DESC, id DESC LIMIT ?
            )
            """,
            (entry_id, entry_id, max(1, max_evidence)),
        )
