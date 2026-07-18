from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from ..domain.models import (
    ContextSection,
    JargonStatus,
    KnownSense,
    KnownTerm,
    MessageContext,
    UnknownTerm,
)
from ..domain.prompts import (
    LEGACY_PROTOCOL_TEMPLATE,
    LEGACY_REPAIR_TEMPLATE,
    PromptTemplates,
)
from ..jargon.normalizer import normalize_term

T = TypeVar("T")


_SCHEMA_VERSION = 23
_CONTEXT_PREVIEW_CHARS = 1_000
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
    raw_output_snapshot TEXT NOT NULL DEFAULT '',
    raw_snapshot_complete INTEGER NOT NULL DEFAULT 0,
    messages_json TEXT NOT NULL DEFAULT '[]',
    response_snapshot_json TEXT NOT NULL DEFAULT '{}',
    response_snapshot_complete INTEGER NOT NULL DEFAULT 0,
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


_PROMPT_TEMPLATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_prompt_templates (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    rule_content TEXT NOT NULL,
    protocol_content TEXT NOT NULL,
    repair_content TEXT NOT NULL,
    memory_extraction_content TEXT NOT NULL,
    reply_examples_content TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_prompt_template_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL CHECK (action IN ('update', 'reset')),
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_prompt_template_audit_created
    ON humanize_prompt_template_audit(created_at DESC, id DESC);
"""

_DROP_LEGACY_CONTROL_SCHEMA = """
DROP INDEX IF EXISTS idx_humanize_control_audit_created;
DROP TABLE IF EXISTS humanize_control_audit;
DROP TABLE IF EXISTS humanize_expression;
DROP TABLE IF EXISTS humanize_behavior_policy;
DROP TABLE IF EXISTS humanize_state;
DROP TABLE IF EXISTS humanize_persona;
"""

_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_context_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    protocol_mode TEXT NOT NULL,
    estimated_tokens INTEGER NOT NULL,
    included_sections INTEGER NOT NULL,
    omitted_sections INTEGER NOT NULL,
    request_snapshot_json TEXT NOT NULL DEFAULT '{}',
    request_snapshot_complete INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_context_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES humanize_context_runs(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    targets_json TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    required INTEGER NOT NULL,
    included INTEGER NOT NULL,
    budget_tokens INTEGER,
    estimated_tokens INTEGER NOT NULL,
    applied_tokens INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    reason TEXT NOT NULL,
    content_preview TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content_chars INTEGER NOT NULL,
    preview_truncated INTEGER NOT NULL,
    content_snapshot TEXT NOT NULL,
    snapshot_complete INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_humanize_context_runs_created
    ON humanize_context_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_context_runs_scope
    ON humanize_context_runs(scope_type, scope_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_context_sections_run
    ON humanize_context_sections(run_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_humanize_context_sections_stats
    ON humanize_context_sections(section_key, included, created_at DESC);
"""

_JARGON_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS jargon_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(entry_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_jargon_aliases_normalized
    ON jargon_aliases(normalized_alias, entry_id);
CREATE INDEX IF NOT EXISTS idx_jargon_senses_entry_status
    ON jargon_senses(entry_id, status, confidence DESC, id);
"""

_PROVIDER_OBSERVABILITY_SCHEMA = """CREATE TABLE IF NOT EXISTS humanize_prompt_prefix_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    epoch_id TEXT NOT NULL DEFAULT '',
    request_fingerprint TEXT NOT NULL DEFAULT '',
    prefix_fingerprint TEXT NOT NULL DEFAULT '',
    first_difference TEXT NOT NULL DEFAULT '',
    cache_observability TEXT NOT NULL DEFAULT 'unknown',
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    usage_observed INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_prefix_samples_created
    ON humanize_prompt_prefix_samples(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_prefix_samples_scope
    ON humanize_prompt_prefix_samples(scope_type, scope_id, created_at DESC);

CREATE TABLE IF NOT EXISTS humanize_llm_usage_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    provider_id TEXT NOT NULL DEFAULT '',
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    request_fingerprint TEXT NOT NULL DEFAULT '',
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    usage_observed INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_humanize_usage_samples_created
    ON humanize_llm_usage_samples(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_usage_samples_scope
    ON humanize_llm_usage_samples(scope_type, scope_id, created_at DESC);
"""

_PROVIDER_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_provider_cache_capabilities (
    provider_id TEXT NOT NULL,
    provider_type TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL,
    capability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (capability IN ('implicit', 'explicit', 'unsupported', 'unknown')),
    usage_observability TEXT NOT NULL DEFAULT 'unknown'
        CHECK (usage_observability IN ('observable', 'unsupported', 'unknown')),
    observed_samples INTEGER NOT NULL DEFAULT 0,
    cached_samples INTEGER NOT NULL DEFAULT 0,
    input_cached INTEGER NOT NULL DEFAULT 0,
    input_other INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(provider_id, model)
);

CREATE INDEX IF NOT EXISTS idx_humanize_provider_cache_seen
    ON humanize_provider_cache_capabilities(last_seen_at DESC);
"""


_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS humanize_memory_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL UNIQUE,
    job_type TEXT NOT NULL CHECK (
        job_type IN (
            'extract', 'extract_turn', 'embed_example'
        )
    ),
    request_id TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    scope_type TEXT NOT NULL DEFAULT '',
    scope_hash TEXT NOT NULL DEFAULT '',
    subject_hash TEXT NOT NULL DEFAULT '',
    conversation_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'running', 'retry', 'completed', 'dead')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_run_at TEXT NOT NULL,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_expires_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS humanize_memory_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL CHECK (
        entity_type IN ('example', 'job')
    ),
    entity_id INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_reply_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'global',
    scope_hash TEXT NOT NULL DEFAULT '',
    subject_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    topic TEXT NOT NULL DEFAULT '',
    intent TEXT NOT NULL DEFAULT '',
    style_tags_json TEXT NOT NULL DEFAULT '[]',
    keywords_json TEXT NOT NULL DEFAULT '[]',
    turns_json TEXT NOT NULL,
    ideal_reply TEXT NOT NULL,
    conditions TEXT NOT NULL DEFAULT '',
    exclusions TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'approved', 'rejected', 'tombstoned')
    ),
    enabled INTEGER NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 0.8 CHECK (
        quality_score >= 0 AND quality_score <= 1
    ),
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_context_run_id TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(scope_type, scope_hash, agent_id, content_hash)
);

CREATE TABLE IF NOT EXISTS humanize_reply_example_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL REFERENCES humanize_reply_examples(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(example_id, revision)
);

CREATE TABLE IF NOT EXISTS humanize_reply_example_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT '',
    scope_hash TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'default',
    query_hash TEXT NOT NULL DEFAULT '',
    example_id INTEGER REFERENCES humanize_reply_examples(id) ON DELETE SET NULL,
    score REAL NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS humanize_embeddings (
    entity_type TEXT NOT NULL CHECK (entity_type = 'example'),
    entity_id INTEGER NOT NULL,
    provider_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    generation TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(entity_type, entity_id, provider_id, model, generation)
);

"""

_MEMORY_INDEX_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_humanize_memory_jobs_claim_agent
    ON humanize_memory_jobs(agent_id, status, next_run_at, lease_expires_at, id);
CREATE INDEX IF NOT EXISTS idx_humanize_memory_audit_entity
    ON humanize_memory_audit(entity_type, entity_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_examples_scope_agent
    ON humanize_reply_examples(scope_type, scope_hash, agent_id, status, enabled, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_example_usage_request_agent
    ON humanize_reply_example_usage(agent_id, request_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_humanize_embeddings_generation
    ON humanize_embeddings(provider_id, model, generation, entity_type, entity_id);
"""

_MEMORY_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS humanize_reply_example_fts USING fts5(
    example_id UNINDEXED,
    search_text,
    tokenize='unicode61'
);
"""

_DROP_LEGACY_MEMORY_SCHEMA = """
DROP TRIGGER IF EXISTS humanize_memory_fts_ai;
DROP TRIGGER IF EXISTS humanize_memory_fts_ad;
DROP TRIGGER IF EXISTS humanize_memory_fts_au;
DROP TABLE IF EXISTS humanize_memory_fts;
DROP TABLE IF EXISTS humanize_memory_evidence;
DROP TABLE IF EXISTS humanize_memory_aliases;
DROP TABLE IF EXISTS humanize_memory_revisions;
DROP TABLE IF EXISTS humanize_memory_recall_logs;
DROP TABLE IF EXISTS humanize_memory_items;
DROP TABLE IF EXISTS humanize_vector_index_state;
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _now_precise() -> str:
    """Return a sortable timestamp suitable for expiry comparisons.

    Returns:
        Current UTC time with microsecond precision.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _json_text(value: Any, default: str = "{}") -> str:
    """Serialize stored metadata without failing the surrounding operation.

    Args:
        value: JSON-compatible value.
        default: Fallback text for malformed values.

    Returns:
        Compact JSON text or the supplied fallback.
    """
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return default


def _json_value(value: str, default: Any) -> Any:
    """Decode stored JSON while tolerating legacy or corrupted rows.

    Args:
        value: Stored JSON text.
        default: Value returned when decoding fails.

    Returns:
        Decoded value or the supplied fallback.
    """
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


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
        self._fts_available = False

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(self._migrate)

    async def get_prompt_templates(self) -> dict[str, Any]:
        """Read the editable prompt templates from the shared database.

        Returns:
            Raw template content and the shared update timestamp.

        Raises:
            RuntimeError: If prompt template defaults are missing.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT rule_content, protocol_content, repair_content, "
                "memory_extraction_content, reply_examples_content, updated_at "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            templates = PromptTemplates.from_mapping(
                {
                    "rule": row["rule_content"],
                    "protocol": row["protocol_content"],
                    "repair": row["repair_content"],
                    "memory_extraction": row["memory_extraction_content"],
                    "reply_examples": row["reply_examples_content"],
                }
            )
            return {
                "templates": templates.as_dict(),
                "updated_at": str(row["updated_at"]),
            }

        return await self._run(operation)

    async def update_prompt_templates(
        self,
        value: dict[str, Any],
        *,
        actor: str = "web_admin",
        reason: str = "web update",
        action: str = "update",
    ) -> dict[str, Any]:
        """Merge prompt templates and record a dedicated audit entry.

        Args:
            value: Partial template content keyed by rule, protocol, or repair.
            actor: Audit actor label.
            reason: Audit reason.
            action: Audit action, either update or reset.

        Returns:
            Complete persisted templates and their update timestamp.

        Raises:
            ValueError: If the template payload or action is invalid.
            RuntimeError: If prompt template defaults are missing.
        """
        if action not in {"update", "reset"}:
            raise ValueError("unsupported prompt template action")
        clean_reason = str(reason or "web update").strip()[:500]
        clean_actor = str(actor or "web_admin").strip()[:120] or "web_admin"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT rule_content, protocol_content, repair_content, "
                "memory_extraction_content, reply_examples_content "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            current = PromptTemplates.from_mapping(
                {
                    "rule": row["rule_content"],
                    "protocol": row["protocol_content"],
                    "repair": row["repair_content"],
                    "memory_extraction": row["memory_extraction_content"],
                    "reply_examples": row["reply_examples_content"],
                }
            )
            updated = PromptTemplates.from_mapping(value, base=current)
            now = _now()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE humanize_prompt_templates
                    SET rule_content = ?, protocol_content = ?, repair_content = ?,
                        memory_extraction_content = ?, reply_examples_content = ?,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        updated.rule,
                        updated.protocol,
                        updated.repair,
                        updated.memory_extraction,
                        updated.reply_examples,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO humanize_prompt_template_audit (
                        action, actor, reason, before_json, after_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action,
                        clean_actor,
                        clean_reason,
                        json.dumps(
                            current.as_dict(), ensure_ascii=False, sort_keys=True
                        ),
                        json.dumps(
                            updated.as_dict(), ensure_ascii=False, sort_keys=True
                        ),
                        now,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {"templates": updated.as_dict(), "updated_at": now}

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
                       e.scope_type, e.scope_id, e.enabled, e.match_mode,
                       e.case_sensitive, e.preferred_sense_id
                FROM jargon_entries e
                WHERE e.scope_type = ? AND e.scope_id = ?
                  AND e.enabled = 1 AND e.status <> 'rejected'
                  AND e.confidence >= ?
                ORDER BY CASE e.status WHEN 'verified' THEN 0 ELSE 1 END,
                         e.confidence DESC, LENGTH(e.normalized_term) DESC
                LIMIT ?
                """,
                (scope_type, scope_id, min_confidence, max(1, limit)),
            ).fetchall()
            result: list[KnownTerm] = []
            for row in rows:
                entry_id = int(row["id"])
                sense_rows = conn.execute(
                    """
                    SELECT id, meaning, confidence, status, reason
                    FROM jargon_senses
                    WHERE entry_id = ? AND status <> 'rejected' AND meaning <> ''
                    ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END,
                             CASE status WHEN 'verified' THEN 0 ELSE 1 END,
                             confidence DESC, id
                    """,
                    (entry_id, row["preferred_sense_id"]),
                ).fetchall()
                verified = [
                    sense for sense in sense_rows if sense["status"] == "verified"
                ]
                injectable_rows = verified
                if not injectable_rows:
                    if len(sense_rows) != 1:
                        continue
                    only = sense_rows[0]
                    if (
                        only["status"] != "provisional"
                        or float(only["confidence"]) < min_confidence
                    ):
                        continue
                    injectable_rows = [only]
                senses = tuple(
                    KnownSense(
                        sense_id=int(sense["id"]),
                        meaning=str(sense["meaning"]),
                        confidence=float(sense["confidence"]),
                        status=JargonStatus(str(sense["status"])),
                        reason=str(sense["reason"]),
                    )
                    for sense in injectable_rows
                )
                aliases = tuple(
                    str(alias["alias"])
                    for alias in conn.execute(
                        """
                        SELECT alias FROM jargon_aliases
                        WHERE entry_id = ? ORDER BY id
                        """,
                        (entry_id,),
                    ).fetchall()
                )
                result.append(
                    KnownTerm(
                        entry_id=entry_id,
                        term=str(row["term"]),
                        normalized_term=str(row["normalized_term"]),
                        meaning=senses[0].meaning,
                        confidence=max(sense.confidence for sense in senses),
                        status=JargonStatus(str(row["status"])),
                        scope_type=str(row["scope_type"]),
                        scope_id=str(row["scope_id"]),
                        senses=senses,
                        aliases=aliases,
                        match_mode=str(row["match_mode"]),
                        case_sensitive=bool(row["case_sensitive"]),
                        enabled=bool(row["enabled"]),
                    )
                )
            return result

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
                    entry = conn.execute(
                        """
                        SELECT DISTINCT e.* FROM jargon_entries e
                        LEFT JOIN jargon_aliases a ON a.entry_id = e.id
                        WHERE e.scope_type = ? AND e.scope_id = ?
                          AND (e.normalized_term = ? OR a.normalized_alias = ?)
                        ORDER BY CASE WHEN e.normalized_term = ? THEN 0 ELSE 1 END,
                                 e.id LIMIT 1
                        """,
                        (
                            context.scope_type,
                            context.scope_id,
                            normalized,
                            normalized,
                            normalized,
                        ),
                    ).fetchone()
                    if entry is None:
                        cursor = conn.execute(
                            """
                            INSERT INTO jargon_entries (
                                scope_type, scope_id, term, normalized_term, status,
                                occurrence_count, confidence, first_seen_at,
                                last_seen_at, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
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
                        entry_id = int(cursor.lastrowid)
                    else:
                        if entry["status"] == JargonStatus.REJECTED.value:
                            continue
                        entry_id = int(entry["id"])
                    duplicate = conn.execute(
                        """
                        SELECT id FROM jargon_evidence
                        WHERE entry_id = ? AND message_id = ? AND content_hash = ?
                        """,
                        (entry_id, context.message_id, content_hash),
                    ).fetchone()
                    if duplicate is not None:
                        continue

                    normalized_meaning = normalize_term(term.guess)
                    sense = conn.execute(
                        """
                        SELECT * FROM jargon_senses
                        WHERE entry_id = ? AND normalized_meaning = ?
                        """,
                        (entry_id, normalized_meaning),
                    ).fetchone()
                    had_verified = (
                        conn.execute(
                            """
                            SELECT 1 FROM jargon_senses
                            WHERE entry_id = ? AND status = 'verified' LIMIT 1
                            """,
                            (entry_id,),
                        ).fetchone()
                        is not None
                    )
                    if sense is None:
                        cursor = conn.execute(
                            """
                            INSERT INTO jargon_senses (
                                entry_id, meaning, normalized_meaning, confidence,
                                status, version, created_by, reason,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, 1, 'llm_protocol', ?, ?, ?)
                            """,
                            (
                                entry_id,
                                term.guess[:1_000],
                                normalized_meaning,
                                term.confidence,
                                desired_status,
                                term.reason[:1_000],
                                now,
                                now,
                            ),
                        )
                        sense_id = int(cursor.lastrowid)
                        accepted = int(not had_verified)
                    else:
                        sense_id = int(sense["id"])
                        next_sense_status = str(sense["status"])
                        if next_sense_status not in {
                            JargonStatus.VERIFIED.value,
                            JargonStatus.REJECTED.value,
                        }:
                            next_sense_status = desired_status
                        conn.execute(
                            """
                            UPDATE jargon_senses
                            SET confidence = MAX(confidence, ?), status = ?,
                                version = version + 1, reason = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                term.confidence,
                                next_sense_status,
                                term.reason[:1_000],
                                now,
                                sense_id,
                            ),
                        )
                        accepted = int(next_sense_status != JargonStatus.REJECTED.value)

                    conn.execute(
                        """
                        INSERT INTO jargon_evidence (
                            entry_id, sense_id, message_id, content_hash, sender_id,
                            source_text, observed_at, valid
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            entry_id,
                            sense_id,
                            context.message_id,
                            content_hash,
                            context.sender_id,
                            context.user_text[:2_000],
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE jargon_entries
                        SET occurrence_count = occurrence_count + 1,
                            last_seen_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, entry_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO jargon_inference_logs (
                            entry_id, sense_id, message_id, proposed_meaning, confidence,
                            reason, accepted, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            sense_id,
                            context.message_id,
                            term.guess[:1_000],
                            term.confidence,
                            term.reason[:1_000],
                            accepted,
                            now,
                        ),
                    )
                    self._refresh_entry_state(conn, entry_id, now)
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
        messages: Sequence[str] = (),
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
        model: str,
        duration_ms: int,
        stage: str = "final",
    ) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            normalized_stage = stage if stage in {"final", "tool"} else "final"
            conn.execute(
                """
                INSERT INTO protocol_logs (
                    request_id, scope_type, scope_id, message_id, sender_id,
                    success, action, failure_code, failure_detail, raw_output,
                    raw_output_snapshot, raw_snapshot_complete, messages_json,
                    response_snapshot_json, response_snapshot_complete, model,
                    duration_ms, stage, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    raw_output,
                    1,
                    json.dumps(tuple(messages), ensure_ascii=False),
                    json.dumps(
                        response_snapshot or {},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    int(response_snapshot_complete),
                    model,
                    max(0, duration_ms),
                    normalized_stage,
                    now,
                ),
            )
            cutoff = (
                datetime.now(UTC) - timedelta(days=self._log_retention_days)
            ).isoformat(timespec="seconds")
            conn.execute("DELETE FROM protocol_logs WHERE created_at < ?", (cutoff,))
            conn.commit()

        await self._run(operation)

    async def record_context_run(
        self,
        context: MessageContext,
        sections: Sequence[ContextSection],
        protocol_mode: str,
        request_snapshot: dict[str, Any] | None = None,
        request_snapshot_complete: bool = False,
    ) -> None:
        """Persist one bounded context-composition trace transactionally.

        Args:
            context: Trusted identifiers for the active request.
            sections: Ordered context sections prepared for the provider request.
            protocol_mode: Configured protocol injection mode.
            request_snapshot: Complete final ``ProviderRequest`` structure.
            request_snapshot_complete: Whether serialization avoided lossy fallbacks.
        """

        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            ordered_sections = sorted(sections, key=lambda item: item.ordinal)
            included_sections = sum(
                1 for section in ordered_sections if section.included
            )
            omitted_sections = len(ordered_sections) - included_sections
            estimated_tokens = sum(
                section.applied_tokens
                for section in ordered_sections
                if section.included
            )
            request_snapshot_json = json.dumps(
                request_snapshot or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT id, scope_type, scope_id, message_id, sender_id,
                           protocol_mode, estimated_tokens, included_sections,
                           omitted_sections, request_snapshot_json,
                           request_snapshot_complete
                    FROM humanize_context_runs WHERE request_id = ?
                    """,
                    (context.request_id,),
                ).fetchone()
                if existing is not None:
                    expected_run = (
                        context.scope_type,
                        context.scope_id,
                        context.message_id,
                        context.sender_id,
                        protocol_mode,
                        max(0, estimated_tokens),
                        included_sections,
                        omitted_sections,
                        request_snapshot_json,
                        int(request_snapshot_complete),
                    )
                    stored_run = tuple(
                        existing[key]
                        for key in (
                            "scope_type",
                            "scope_id",
                            "message_id",
                            "sender_id",
                            "protocol_mode",
                            "estimated_tokens",
                            "included_sections",
                            "omitted_sections",
                            "request_snapshot_json",
                            "request_snapshot_complete",
                        )
                    )
                    stored_sections = conn.execute(
                        """
                        SELECT section_key, ordinal, priority, targets_json,
                               source_type, source_refs_json, required, included,
                               budget_tokens, estimated_tokens, applied_tokens,
                               item_count, reason, content_hash
                        FROM humanize_context_sections
                        WHERE run_id = ? ORDER BY ordinal, id
                        """,
                        (int(existing["id"]),),
                    ).fetchall()
                    expected_sections = [
                        (
                            section.key,
                            section.ordinal,
                            section.priority,
                            json.dumps(section.targets, ensure_ascii=False),
                            section.source_type,
                            json.dumps(section.source_refs, ensure_ascii=False),
                            int(section.required),
                            int(section.included),
                            section.budget_tokens,
                            max(0, section.estimated_tokens),
                            max(0, section.applied_tokens),
                            max(0, section.item_count),
                            section.reason[:200],
                            hashlib.sha256(
                                section.content.encode("utf-8", errors="replace")
                            ).hexdigest(),
                        )
                        for section in ordered_sections
                    ]
                    stored_section_values = [tuple(row) for row in stored_sections]
                    if (
                        stored_run != expected_run
                        or stored_section_values != expected_sections
                    ):
                        raise RuntimeError(
                            "context run already exists with different trace data"
                        )
                    conn.commit()
                    return
                conn.execute(
                    """
                    INSERT INTO humanize_context_runs (
                        request_id, scope_type, scope_id, message_id, sender_id,
                        protocol_mode, estimated_tokens, included_sections,
                        omitted_sections, request_snapshot_json,
                        request_snapshot_complete, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        context.request_id,
                        context.scope_type,
                        context.scope_id,
                        context.message_id,
                        context.sender_id,
                        protocol_mode,
                        max(0, estimated_tokens),
                        included_sections,
                        omitted_sections,
                        request_snapshot_json,
                        int(request_snapshot_complete),
                        now,
                    ),
                )
                run = conn.execute(
                    "SELECT id FROM humanize_context_runs WHERE request_id = ?",
                    (context.request_id,),
                ).fetchone()
                if run is None:
                    raise RuntimeError("context run was not persisted")
                run_id = int(run["id"])
                conn.executemany(
                    """
                    INSERT INTO humanize_context_sections (
                        run_id, section_key, ordinal, priority, targets_json,
                        source_type, source_refs_json, required, included,
                        budget_tokens, estimated_tokens, applied_tokens,
                        item_count, reason, content_preview, content_hash,
                        content_chars, preview_truncated, content_snapshot,
                        snapshot_complete, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            run_id,
                            section.key,
                            section.ordinal,
                            section.priority,
                            json.dumps(section.targets, ensure_ascii=False),
                            section.source_type,
                            json.dumps(section.source_refs, ensure_ascii=False),
                            int(section.required),
                            int(section.included),
                            section.budget_tokens,
                            max(0, section.estimated_tokens),
                            max(0, section.applied_tokens),
                            max(0, section.item_count),
                            section.reason[:200],
                            section.content[:_CONTEXT_PREVIEW_CHARS],
                            hashlib.sha256(
                                section.content.encode("utf-8", errors="replace")
                            ).hexdigest(),
                            len(section.content),
                            int(len(section.content) > _CONTEXT_PREVIEW_CHARS),
                            section.content,
                            1,
                            now,
                        )
                        for section in ordered_sections
                    ],
                )
                conn.execute(
                    "DELETE FROM jargon_injection_logs WHERE request_id = ?",
                    (context.request_id,),
                )
                jargon_rows = []
                for section in sections:
                    if section.key != "known_terms" or not section.included:
                        continue
                    for source_ref in section.source_refs:
                        prefix, separator, raw_id = source_ref.partition(":")
                        if prefix != "jargon" or not separator or not raw_id.isdigit():
                            continue
                        entry_id = int(raw_id)
                        if (
                            conn.execute(
                                "SELECT 1 FROM jargon_entries WHERE id = ?", (entry_id,)
                            ).fetchone()
                            is None
                        ):
                            continue
                        jargon_rows.append(
                            (
                                context.request_id,
                                context.scope_type,
                                context.scope_id,
                                context.message_id,
                                entry_id,
                                section.reason,
                                now,
                            )
                        )
                if jargon_rows:
                    conn.executemany(
                        """
                        INSERT INTO jargon_injection_logs (
                            request_id, scope_type, scope_id, message_id,
                            entry_id, selected, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        jargon_rows,
                    )
                cutoff = (
                    datetime.now(UTC) - timedelta(days=self._log_retention_days)
                ).isoformat(timespec="seconds")
                conn.execute(
                    "DELETE FROM humanize_context_runs WHERE created_at < ?",
                    (cutoff,),
                )
                conn.execute(
                    "DELETE FROM jargon_injection_logs WHERE created_at < ?",
                    (cutoff,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def list_context_runs(
        self,
        *,
        scope_type: str,
        scope_id: str,
        section_key: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Return filtered context runs without private content payloads.

        Args:
            scope_type: Optional group or private scope type.
            scope_id: Optional exact scope identifier.
            section_key: Optional required section key.
            page: One-based page number.
            page_size: Bounded result count.

        Returns:
            Paginated run summaries.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            clauses: list[str] = []
            params: list[Any] = []
            if scope_type:
                clauses.append("r.scope_type = ?")
                params.append(scope_type)
            if scope_id:
                clauses.append("r.scope_id = ?")
                params.append(scope_id)
            if section_key:
                clauses.append(
                    "EXISTS (SELECT 1 FROM humanize_context_sections s "
                    "WHERE s.run_id = r.id AND s.section_key = ?)"
                )
                params.append(section_key)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM humanize_context_runs r {where}",
                    params,
                ).fetchone()["count"]
            )
            rows = conn.execute(
                f"""
                SELECT r.id, r.request_id, r.scope_type, r.scope_id, r.message_id,
                       r.sender_id, r.protocol_mode, r.estimated_tokens,
                       r.included_sections, r.omitted_sections, r.created_at
                FROM humanize_context_runs r
                {where}
                ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        return await self._run(operation)

    async def get_context_run(self, request_id: str) -> dict[str, Any] | None:
        """Return one context run with its full injection and response snapshot.

        Args:
            request_id: Exact request identifier.

        Returns:
            Run detail, or None when the trace does not exist.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            run = conn.execute(
                """
                SELECT id, request_id, scope_type, scope_id, message_id, sender_id,
                       protocol_mode, estimated_tokens, included_sections,
                       omitted_sections, request_snapshot_json,
                       request_snapshot_complete, created_at
                FROM humanize_context_runs WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if run is None:
                return None
            sections = []
            for row in conn.execute(
                """
                SELECT section_key, ordinal, priority, targets_json, source_type,
                       source_refs_json, required, included, budget_tokens,
                       estimated_tokens, applied_tokens, item_count, reason,
                       content_preview, content_hash, content_chars,
                       preview_truncated, content_snapshot, snapshot_complete,
                       created_at
                FROM humanize_context_sections
                WHERE run_id = ? ORDER BY ordinal, id
                """,
                (int(run["id"]),),
            ).fetchall():
                item = dict(row)
                item["targets"] = json.loads(item.pop("targets_json"))
                item["source_refs"] = json.loads(item.pop("source_refs_json"))
                item["required"] = bool(item["required"])
                item["included"] = bool(item["included"])
                item["preview_truncated"] = bool(item["preview_truncated"])
                item["snapshot_complete"] = bool(item["snapshot_complete"])
                item["content"] = item.pop("content_snapshot")
                sections.append(item)
            run_item = dict(run)
            run_item.pop("id", None)
            raw_request_snapshot = run_item.pop("request_snapshot_json")
            request_snapshot_complete = bool(run_item.pop("request_snapshot_complete"))
            try:
                stored_request_snapshot = json.loads(raw_request_snapshot)
            except (TypeError, json.JSONDecodeError):
                stored_request_snapshot = {}
                request_snapshot_complete = False
            if not isinstance(stored_request_snapshot, dict):
                stored_request_snapshot = {}
                request_snapshot_complete = False
            request_snapshot = {
                "snapshot_kind": "provider_request",
                "snapshot_complete": request_snapshot_complete,
                "provider_request": stored_request_snapshot or None,
            }
            response_row = conn.execute(
                """
                SELECT success, action, failure_code, failure_detail,
                       raw_output_snapshot, raw_snapshot_complete, messages_json,
                       response_snapshot_json, response_snapshot_complete,
                       model, duration_ms, stage, created_at
                FROM protocol_logs
                WHERE request_id = ? AND stage = 'final'
                ORDER BY id DESC LIMIT 1
                """,
                (request_id,),
            ).fetchone()
            response = None
            response_snapshot = None
            if response_row is not None:
                response = dict(response_row)
                raw_response_snapshot = response.pop("response_snapshot_json")
                llm_snapshot_complete = bool(response.pop("response_snapshot_complete"))
                response["success"] = bool(response["success"])
                response["snapshot_complete"] = bool(
                    response.pop("raw_snapshot_complete")
                )
                response["raw_output"] = response.pop("raw_output_snapshot")
                response["messages"] = json.loads(response.pop("messages_json"))
                try:
                    llm_response = json.loads(raw_response_snapshot)
                except (TypeError, json.JSONDecodeError):
                    llm_response = {}
                    llm_snapshot_complete = False
                if not isinstance(llm_response, dict):
                    llm_response = {}
                    llm_snapshot_complete = False
                response_snapshot = {
                    "snapshot_kind": "llm_response",
                    "snapshot_complete": bool(
                        llm_snapshot_complete and response["snapshot_complete"]
                    ),
                    "llm_response": llm_response or None,
                    "protocol": dict(response),
                }
            snapshot_sections = [
                {
                    "section_key": item["section_key"],
                    "ordinal": item["ordinal"],
                    "content": item["content"],
                    "targets": item["targets"],
                    "source_refs": item["source_refs"],
                    "included": item["included"],
                    "snapshot_complete": item["snapshot_complete"],
                }
                for item in sections
            ]
            return {
                "run": run_item,
                "sections": sections,
                "request_snapshot": request_snapshot,
                "response_snapshot": response_snapshot,
                "snapshot": {
                    "snapshot_kind": "context_injection",
                    "snapshot_complete": all(
                        item["snapshot_complete"] for item in snapshot_sections
                    ),
                    "run": dict(run_item),
                    "sections": snapshot_sections,
                },
                "response": response,
            }

        return await self._run(operation)

    async def record_llm_usage_sample(
        self,
        *,
        request_id: str,
        stage: str,
        scope_type: str,
        scope_id: str,
        conversation_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        model: str = "",
        provider_cache_capability: str = "unknown",
        epoch_id: str = "",
        request_fingerprint: str = "",
        prefix_fingerprint: str = "",
        first_difference: str = "",
        longest_common_prefix_chars: int = 0,
        epoch_reason: str = "",
        cache_observability: str = "unknown",
        input_cached: int = 0,
        input_other: int = 0,
        output_tokens: int = 0,
        usage_observed: bool = False,
        duration_ms: int = 0,
        ttft_ms: int | None = None,
    ) -> None:
        """Persist one provider usage and prefix-cache observation.

        Args:
            request_id: AstrBot request identifier.
            stage: ``tool``, ``final`` or ``repair`` stage.
            scope_type: Scope type for the conversation.
            scope_id: Scope identifier for the conversation.
            conversation_id: AstrBot conversation identifier.
            provider_id: Non-secret provider instance ID.
            provider_type: Provider adapter type.
            model: Effective model name.
            provider_cache_capability: Declared or inferred cache capability.
            epoch_id: Conversation cache epoch, when available.
            request_fingerprint: Exact final request fingerprint.
            prefix_fingerprint: Stable prefix fingerprint.
            first_difference: First structural difference from the previous request.
            longest_common_prefix_chars: Exact common JSON-prefix size.
            epoch_reason: Stable reason for retaining or rolling the epoch.
            cache_observability: ``observable``, ``unsupported`` or ``unknown``.
            input_cached: Provider-reported cached input tokens.
            input_other: Provider-reported uncached input tokens.
            output_tokens: Provider-reported output tokens.
            usage_observed: Whether the provider supplied a usage object.
            duration_ms: Measured response duration in milliseconds.
            ttft_ms: Time to first observed response chunk, when measurable.
        """
        clean_request = str(request_id or "").strip()[:200]
        clean_stage = str(stage or "final").strip()[:40] or "final"
        now = _now()
        clean_capability = str(provider_cache_capability or "unknown").lower()
        if clean_capability not in {
            "implicit",
            "explicit",
            "unsupported",
            "unknown",
        }:
            clean_capability = "unknown"
        if clean_capability == "unknown" and usage_observed and int(input_cached) > 0:
            clean_capability = "implicit"
        clean_observability = str(cache_observability or "unknown").lower()
        if clean_observability not in {"observable", "unsupported", "unknown"}:
            clean_observability = "unknown"
        if clean_observability == "unknown" and usage_observed:
            clean_observability = "observable"
        if clean_capability == "unsupported":
            clean_observability = "unsupported"
        values = (
            clean_request,
            clean_stage,
            str(scope_type or "")[:120],
            str(scope_id or "")[:300],
            str(conversation_id or scope_id or "")[:300],
            str(provider_id or "")[:160],
            str(provider_type or "")[:160],
            str(model or "")[:200],
            clean_capability,
            str(epoch_id or "")[:200],
            str(request_fingerprint or "")[:128],
            str(prefix_fingerprint or "")[:128],
            str(first_difference or "")[:500],
            max(0, int(longest_common_prefix_chars)),
            str(epoch_reason or "")[:80],
            clean_observability,
            max(0, int(input_cached)),
            max(0, int(input_other)),
            max(0, int(output_tokens)),
            int(bool(usage_observed)),
            max(0, int(duration_ms)),
            max(0, int(ttft_ms)) if ttft_ms is not None else None,
            now,
        )

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO humanize_prompt_prefix_samples (
                    request_id, stage, scope_type, scope_id, conversation_id,
                    provider_id, provider_type, model, provider_cache_capability,
                    epoch_id, request_fingerprint, prefix_fingerprint,
                    first_difference, longest_common_prefix_chars, epoch_reason,
                    cache_observability,
                    input_cached, input_other, output_tokens, usage_observed,
                    duration_ms, ttft_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.execute(
                """
                INSERT INTO humanize_llm_usage_samples (
                    request_id, stage, scope_type, scope_id, provider_id,
                    provider_type, model, request_fingerprint, input_cached,
                    input_other, output_tokens, usage_observed, duration_ms,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_request,
                    clean_stage,
                    str(scope_type or "")[:120],
                    str(scope_id or "")[:300],
                    str(provider_id or "")[:160],
                    str(provider_type or "")[:160],
                    str(model or "")[:200],
                    str(request_fingerprint or "")[:128],
                    max(0, int(input_cached)),
                    max(0, int(input_other)),
                    max(0, int(output_tokens)),
                    int(bool(usage_observed)),
                    max(0, int(duration_ms)),
                    now,
                ),
            )
            clean_provider_id = str(provider_id or "")[:160]
            clean_model = str(model or "")[:200]
            if clean_provider_id and clean_model:
                conn.execute(
                    """
                    INSERT INTO humanize_provider_cache_capabilities (
                        provider_id, provider_type, model, capability,
                        usage_observability, observed_samples, cached_samples,
                        input_cached, input_other, output_tokens, reason,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id, model) DO UPDATE SET
                        provider_type = excluded.provider_type,
                        capability = CASE
                            WHEN humanize_provider_cache_capabilities.capability = 'explicit'
                                THEN 'explicit'
                            WHEN excluded.capability = 'unknown'
                                THEN humanize_provider_cache_capabilities.capability
                            ELSE excluded.capability END,
                        usage_observability = CASE
                            WHEN excluded.usage_observability = 'observable'
                                THEN 'observable'
                            WHEN humanize_provider_cache_capabilities.usage_observability = 'observable'
                                THEN 'observable'
                            ELSE excluded.usage_observability END,
                        observed_samples = observed_samples + excluded.observed_samples,
                        cached_samples = cached_samples + excluded.cached_samples,
                        input_cached = input_cached + excluded.input_cached,
                        input_other = input_other + excluded.input_other,
                        output_tokens = output_tokens + excluded.output_tokens,
                        reason = excluded.reason,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        clean_provider_id,
                        str(provider_type or "")[:160],
                        clean_model,
                        clean_capability,
                        clean_observability,
                        int(bool(usage_observed)),
                        int(bool(usage_observed and int(input_cached) > 0)),
                        max(0, int(input_cached)),
                        max(0, int(input_other)),
                        max(0, int(output_tokens)),
                        "usage_sample" if usage_observed else "usage_unobservable",
                        now,
                        now,
                    ),
                )
            conn.commit()

        await self._run(operation)

    async def get_latest_prompt_prefix_sample(
        self, *, scope_type: str, scope_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """Return the latest hash-only prefix observation for epoch recovery."""

        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                """
                SELECT epoch_id, request_fingerprint, prefix_fingerprint,
                       first_difference, longest_common_prefix_chars,
                       epoch_reason, created_at
                FROM humanize_prompt_prefix_samples
                WHERE scope_type = ? AND scope_id = ? AND conversation_id = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (
                    str(scope_type or "")[:120],
                    str(scope_id or "")[:300],
                    str(conversation_id or scope_id or "")[:300],
                ),
            ).fetchone()
            return dict(row) if row is not None else None

        return await self._run(operation)

    async def list_provider_cache_capabilities(self) -> list[dict[str, Any]]:
        """List non-secret Provider cache capability observations."""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT provider_id, provider_type, model, capability,
                       usage_observability, observed_samples, cached_samples,
                       input_cached, input_other, output_tokens, reason,
                       first_seen_at, last_seen_at
                FROM humanize_provider_cache_capabilities
                ORDER BY last_seen_at DESC, provider_id, model
                """
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)

    async def get_context_stats(self, *, days: int) -> dict[str, Any]:
        """Aggregate bounded context-composition statistics.

        Args:
            days: Number of recent UTC days to include.

        Returns:
            Run totals, section summaries, and stable reason counts.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            cutoff = (datetime.now(UTC) - timedelta(days=max(1, days))).isoformat(
                timespec="seconds"
            )
            runs = conn.execute(
                """
                SELECT COUNT(*) AS runs,
                       COALESCE(SUM(estimated_tokens), 0) AS total_tokens,
                       COALESCE(AVG(estimated_tokens), 0) AS average_tokens
                FROM humanize_context_runs WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            sections = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT s.section_key,
                           COUNT(*) AS occurrences,
                           SUM(CASE WHEN s.included = 1 THEN 1 ELSE 0 END) AS included,
                           SUM(CASE WHEN s.included = 0 THEN 1 ELSE 0 END) AS omitted,
                           ROUND(AVG(s.estimated_tokens), 1) AS average_tokens,
                           SUM(s.applied_tokens) AS total_applied_tokens,
                           SUM(s.item_count) AS total_items
                    FROM humanize_context_sections s
                    JOIN humanize_context_runs r ON r.id = s.run_id
                    WHERE r.created_at >= ?
                    GROUP BY s.section_key ORDER BY MIN(s.ordinal), s.section_key
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            reasons = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT s.section_key, s.reason, COUNT(*) AS count
                    FROM humanize_context_sections s
                    JOIN humanize_context_runs r ON r.id = s.run_id
                    WHERE r.created_at >= ?
                    GROUP BY s.section_key, s.reason
                    ORDER BY count DESC, s.section_key, s.reason
                    """,
                    (cutoff,),
                ).fetchall()
            ]
            return {
                "days": max(1, days),
                "runs": int(runs["runs"] or 0),
                "total_tokens": int(runs["total_tokens"] or 0),
                "average_tokens": round(float(runs["average_tokens"] or 0), 1),
                "sections": sections,
                "reasons": reasons,
            }

        return await self._run(operation)

    async def get_overview(self) -> dict[str, Any]:
        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            counts = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN EXISTS (
                           SELECT 1 FROM jargon_senses s
                           WHERE s.entry_id = jargon_entries.id
                             AND s.status IN ('candidate', 'provisional')
                       ) THEN 1 ELSE 0 END) AS pending
                FROM jargon_entries
                WHERE status <> 'rejected' AND enabled = 1
                """
            ).fetchone()
            start_date = datetime.now(UTC).date() - timedelta(days=6)
            since = f"{start_date.isoformat()}T00:00:00+00:00"
            protocol = conn.execute(
                """
                WITH final_protocol AS (
                    SELECT p.* FROM protocol_logs p
                    JOIN (
                        SELECT request_id, MAX(id) AS final_id
                        FROM protocol_logs WHERE stage = 'final'
                        GROUP BY request_id
                    ) latest ON latest.final_id = p.id
                )
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS blocked
                FROM final_protocol WHERE created_at >= ?
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
                WITH final_protocol AS (
                    SELECT p.* FROM protocol_logs p
                    JOIN (
                        SELECT request_id, MAX(id) AS final_id
                        FROM protocol_logs WHERE stage = 'final'
                        GROUP BY request_id
                    ) latest ON latest.final_id = p.id
                )
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success
                FROM final_protocol
                WHERE created_at >= ?
                GROUP BY substr(created_at, 1, 10)
                """,
                (since,),
            ).fetchall()
            actions = conn.execute(
                """
                WITH final_protocol AS (
                    SELECT p.* FROM protocol_logs p
                    JOIN (
                        SELECT request_id, MAX(id) AS final_id
                        FROM protocol_logs WHERE stage = 'final'
                        GROUP BY request_id
                    ) latest ON latest.final_id = p.id
                )
                SELECT SUM(CASE WHEN action = 'Reply' THEN 1 ELSE 0 END) AS reply,
                       SUM(CASE WHEN action = 'No Reply' THEN 1 ELSE 0 END) AS no_reply
                FROM final_protocol WHERE created_at >= ?
                """,
                (since,),
            ).fetchone()
            context = conn.execute(
                """
                SELECT COUNT(*) AS total_runs,
                       COALESCE(AVG(estimated_tokens), 0) AS average_tokens,
                       SUM(CASE WHEN omitted_sections > 0 THEN 1 ELSE 0 END) AS omitted_runs
                FROM humanize_context_runs WHERE created_at >= ?
                """,
                (since,),
            ).fetchone()
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
                "action_distribution": {
                    "Reply": int(actions["reply"] or 0),
                    "No Reply": int(actions["no_reply"] or 0),
                },
                "context_stats": {
                    "total_runs": int(context["total_runs"] or 0),
                    "average_tokens": round(float(context["average_tokens"] or 0), 1),
                    "omitted_runs": int(context["omitted_runs"] or 0),
                },
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
                conditions.append(
                    "(e.term LIKE ? OR EXISTS ("
                    "SELECT 1 FROM jargon_aliases a WHERE a.entry_id = e.id "
                    "AND a.alias LIKE ?) OR EXISTS ("
                    "SELECT 1 FROM jargon_senses s WHERE s.entry_id = e.id "
                    "AND s.meaning LIKE ?))"
                )
                query = f"%{search}%"
                params.extend([query, query, query])
            if status:
                if status == "conflict":
                    conditions.append(
                        "(SELECT COUNT(*) FROM jargon_senses s "
                        "WHERE s.entry_id = e.id AND s.status <> 'rejected') > 1 "
                        "AND ((SELECT COUNT(*) FROM jargon_senses s "
                        "WHERE s.entry_id = e.id AND s.status IN "
                        "('candidate', 'provisional')) > 0 OR "
                        "(SELECT COUNT(*) FROM jargon_senses s "
                        "WHERE s.entry_id = e.id AND s.status = 'verified') = 0)"
                    )
                elif status == "disabled":
                    conditions.append("e.enabled = 0")
                else:
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
                f"SELECT COUNT(*) AS count FROM jargon_entries e WHERE {where}",
                params,
            ).fetchone()["count"]
            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT e.id, e.term, e.normalized_term, e.scope_type, e.scope_id,
                       e.status, e.occurrence_count, e.confidence, e.last_seen_at,
                   e.enabled, e.match_mode, e.case_sensitive,
                   e.preferred_sense_id,
                   COALESCE((
                       SELECT s.meaning FROM jargon_senses s
                       WHERE s.id = e.preferred_sense_id AND s.status <> 'rejected'
                    ), (
                       SELECT s.meaning FROM jargon_senses s
                       WHERE s.entry_id = e.id AND s.status <> 'rejected'
                       ORDER BY CASE s.status WHEN 'verified' THEN 0 ELSE 1 END,
                                s.confidence DESC, s.id LIMIT 1
                   ), '') AS meaning,
                       (SELECT COUNT(*) FROM jargon_aliases a
                        WHERE a.entry_id = e.id) AS alias_count,
                       (SELECT COUNT(*) FROM jargon_senses s
                        WHERE s.entry_id = e.id AND s.status <> 'rejected') AS sense_count,
                       (SELECT COUNT(*) FROM jargon_senses s
                        WHERE s.entry_id = e.id AND s.status = 'verified') AS verified_sense_count,
                       (SELECT COUNT(*) FROM jargon_senses s
                        WHERE s.entry_id = e.id
                          AND s.status IN ('candidate', 'provisional')) AS pending_sense_count,
                       (SELECT s.status FROM jargon_senses s
                        WHERE s.id = e.preferred_sense_id) AS preferred_sense_status,
                       (SELECT s.confidence FROM jargon_senses s
                        WHERE s.id = e.preferred_sense_id) AS preferred_sense_confidence
                FROM jargon_entries e
                WHERE {where}
                ORDER BY e.updated_at DESC, e.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                item["case_sensitive"] = bool(item["case_sensitive"])
                item["has_conflict"] = int(item["sense_count"] or 0) > 1 and (
                    int(item["pending_sense_count"] or 0) > 0
                    or int(item["verified_sense_count"] or 0) == 0
                )
                item["preferred_sense"] = (
                    {
                        "id": int(item["preferred_sense_id"]),
                        "meaning": item["meaning"],
                        "status": item.pop("preferred_sense_status"),
                        "confidence": item.pop("preferred_sense_confidence"),
                    }
                    if item["preferred_sense_id"] is not None
                    else None
                )
                if item["preferred_sense"] is None:
                    item.pop("preferred_sense_status", None)
                    item.pop("preferred_sense_confidence", None)
                items.append(item)
            return {
                "items": items,
                "total": int(total),
                "page": page,
                "page_size": page_size,
            }

        return await self._run(operation)

    async def get_jargon_detail(self, entry_id: int) -> dict[str, Any] | None:
        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            entry = conn.execute(
                "SELECT * FROM jargon_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if entry is None:
                return None
            aliases = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, alias, normalized_alias, created_at
                    FROM jargon_aliases WHERE entry_id = ? ORDER BY id
                    """,
                    (entry_id,),
                ).fetchall()
            ]
            senses = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, meaning, normalized_meaning, confidence, status,
                           version, created_by, reason, created_at, updated_at,
                           CASE WHEN id = ? THEN 1 ELSE 0 END AS is_preferred,
                           (SELECT COUNT(*) FROM jargon_evidence ev
                            WHERE ev.sense_id = jargon_senses.id) AS evidence_count
                    FROM jargon_senses WHERE entry_id = ?
                    ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END,
                             CASE status WHEN 'verified' THEN 0 ELSE 1 END,
                             confidence DESC, id
                    """,
                    (
                        entry["preferred_sense_id"],
                        entry_id,
                        entry["preferred_sense_id"],
                    ),
                ).fetchall()
            ]
            for sense in senses:
                sense["is_preferred"] = bool(sense["is_preferred"])
                sense["preferred"] = sense["is_preferred"]
            evidence = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT id, sense_id, message_id, sender_id, source_text,
                           observed_at, valid
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
                    SELECT id, sense_id, message_id, proposed_meaning, confidence,
                           reason, accepted, created_at
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
            entry_item = dict(entry)
            entry_item["enabled"] = bool(entry_item["enabled"])
            entry_item["case_sensitive"] = bool(entry_item["case_sensitive"])
            active_senses = [sense for sense in senses if sense["status"] != "rejected"]
            verified_count = sum(
                1 for sense in active_senses if sense["status"] == "verified"
            )
            pending_count = sum(
                1
                for sense in active_senses
                if sense["status"] in {"candidate", "provisional"}
            )
            entry_item["verified_sense_count"] = verified_count
            entry_item["pending_sense_count"] = pending_count
            entry_item["sense_count"] = len(active_senses)
            entry_item["alias_count"] = len(aliases)
            entry_item["has_conflict"] = len(active_senses) > 1 and (
                pending_count > 0 or verified_count == 0
            )
            entry_item["meaning"] = (
                str(active_senses[0]["meaning"]) if active_senses else ""
            )
            return {
                "entry": entry_item,
                "aliases": aliases,
                "senses": senses,
                "evidence": evidence,
                "inferences": inferences,
                "injections": injections,
            }

        return await self._run(operation)

    async def apply_jargon_action(
        self,
        entry_id: int,
        action: str,
        meaning: str = "",
        *,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        def operation(conn: sqlite3.Connection) -> bool:
            now = _now()
            data = dict(payload or {})
            if meaning and "meaning" not in data:
                data["meaning"] = meaning

            def sense_id_from(key: str = "sense_id") -> int:
                try:
                    value = int(data.get(key))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a positive integer") from exc
                if value <= 0:
                    raise ValueError(f"{key} must be a positive integer")
                return value

            def clean_meaning() -> str:
                value = str(data.get("meaning") or "").strip()
                if not value or len(value) > 1_000:
                    raise ValueError("meaning must contain 1 to 1000 characters")
                return value

            conn.execute("BEGIN IMMEDIATE")
            try:
                exists = conn.execute(
                    "SELECT * FROM jargon_entries WHERE id = ?", (entry_id,)
                ).fetchone()
                if exists is None:
                    conn.rollback()
                    return False
                normalized_action = {
                    "sense_create": "create_sense",
                    "sense_update": "update_sense",
                    "sense_confirm": "confirm_sense",
                    "sense_reject": "reject_sense",
                    "sense_preferred": "set_preferred",
                    "set_preferred_sense": "set_preferred",
                    "sense_merge": "merge_sense",
                    "sense_delete": "delete_sense",
                }.get(action, action)
                if normalized_action in {
                    "confirm",
                    "reject",
                    "update",
                    "delete",
                } and data.get("sense_id"):
                    normalized_action = f"{normalized_action}_sense"

                if normalized_action == "confirm":
                    target = conn.execute(
                        """
                        SELECT id FROM jargon_senses
                        WHERE entry_id = ? AND status <> 'rejected'
                        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END,
                                 confidence DESC, id LIMIT 1
                        """,
                        (entry_id, exists["preferred_sense_id"]),
                    ).fetchone()
                    if target is None:
                        raise ValueError("entry has no active sense")
                    conn.execute(
                        """
                        UPDATE jargon_senses
                        SET status = 'verified', confidence = MAX(confidence, 1.0),
                            created_by = 'admin', updated_at = ? WHERE id = ?
                        """,
                        (now, int(target["id"])),
                    )
                    conn.execute(
                        "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                        (int(target["id"]), entry_id),
                    )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "reject":
                    conn.execute(
                        "UPDATE jargon_entries SET status = 'rejected', updated_at = ? WHERE id = ?",
                        (now, entry_id),
                    )
                    conn.execute(
                        "UPDATE jargon_senses SET status = 'rejected', created_by = 'admin', updated_at = ? WHERE entry_id = ?",
                        (now, entry_id),
                    )
                    conn.execute(
                        "UPDATE jargon_entries SET preferred_sense_id = NULL WHERE id = ?",
                        (entry_id,),
                    )
                elif normalized_action == "update":
                    clean = clean_meaning()
                    target = conn.execute(
                        """
                        SELECT id FROM jargon_senses
                        WHERE entry_id = ? AND status <> 'rejected'
                        ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END,
                                 confidence DESC, id LIMIT 1
                        """,
                        (entry_id, exists["preferred_sense_id"]),
                    ).fetchone()
                    if target is None:
                        cursor = conn.execute(
                            """
                            INSERT INTO jargon_senses (
                                entry_id, meaning, normalized_meaning, confidence,
                                status, version, created_by, reason, created_at, updated_at
                            ) VALUES (?, ?, ?, 1.0, 'verified', 1, 'admin',
                                      'manual update', ?, ?)
                            """,
                            (entry_id, clean, normalize_term(clean), now, now),
                        )
                        target_id = int(cursor.lastrowid)
                    else:
                        target_id = int(target["id"])
                        conn.execute(
                            """
                            UPDATE jargon_senses
                            SET meaning = ?, normalized_meaning = ?, confidence = 1.0,
                                status = 'verified', version = version + 1,
                                created_by = 'admin', reason = 'manual update',
                                updated_at = ? WHERE id = ?
                            """,
                            (clean, normalize_term(clean), now, target_id),
                        )
                    conn.execute(
                        "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                        (target_id, entry_id),
                    )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "update_entry":
                    updates: list[str] = []
                    values: list[Any] = []
                    if "enabled" in data:
                        enabled_value = data["enabled"]
                        enabled = (
                            enabled_value
                            if isinstance(enabled_value, bool)
                            else str(enabled_value).strip().lower()
                            in {"1", "true", "yes", "on"}
                        )
                        updates.append("enabled = ?")
                        values.append(int(enabled))
                    if "match_mode" in data:
                        match_mode = str(data["match_mode"]).strip().lower()
                        if match_mode not in {"smart", "contains", "exact"}:
                            raise ValueError("unsupported jargon match mode")
                        updates.append("match_mode = ?")
                        values.append(match_mode)
                    if "case_sensitive" in data:
                        case_value = data["case_sensitive"]
                        case_sensitive = (
                            case_value
                            if isinstance(case_value, bool)
                            else str(case_value).strip().lower()
                            in {"1", "true", "yes", "on"}
                        )
                        updates.append("case_sensitive = ?")
                        values.append(int(case_sensitive))
                    if "term" in data:
                        clean_term = str(data["term"] or "").strip()
                        if not clean_term or len(clean_term) > 128:
                            raise ValueError("term must contain 1 to 128 characters")
                        normalized_term = normalize_term(clean_term)
                        conflict = conn.execute(
                            """
                            SELECT e.id FROM jargon_entries e
                            LEFT JOIN jargon_aliases a ON a.entry_id = e.id
                            WHERE e.scope_type = ? AND e.scope_id = ?
                              AND e.id <> ?
                              AND (e.normalized_term = ? OR a.normalized_alias = ?)
                            LIMIT 1
                            """,
                            (
                                exists["scope_type"],
                                exists["scope_id"],
                                entry_id,
                                normalized_term,
                                normalized_term,
                            ),
                        ).fetchone()
                        if conflict is not None:
                            raise ValueError("term already exists in this scope")
                        updates.extend(["term = ?", "normalized_term = ?"])
                        values.extend([clean_term, normalized_term])
                        conn.execute(
                            """
                            DELETE FROM jargon_aliases
                            WHERE entry_id = ? AND normalized_alias = ?
                            """,
                            (entry_id, normalized_term),
                        )
                    if updates:
                        updates.append("updated_at = ?")
                        values.extend([now, entry_id])
                        conn.execute(
                            f"UPDATE jargon_entries SET {', '.join(updates)} WHERE id = ?",
                            values,
                        )
                elif normalized_action == "replace_aliases":
                    raw_aliases = data.get("aliases", [])
                    if not isinstance(raw_aliases, list):
                        raise ValueError("aliases must be a list")
                    clean_aliases: list[tuple[str, str]] = []
                    seen: set[str] = set()
                    for raw_alias in raw_aliases[:50]:
                        alias = str(raw_alias or "").strip()
                        normalized_alias = normalize_term(alias)
                        if not alias or len(alias) > 128 or not normalized_alias:
                            raise ValueError("alias must contain 1 to 128 characters")
                        if (
                            normalized_alias in seen
                            or normalized_alias == exists["normalized_term"]
                        ):
                            continue
                        conflict = conn.execute(
                            """
                            SELECT e.id FROM jargon_entries e
                            LEFT JOIN jargon_aliases a ON a.entry_id = e.id
                            WHERE e.scope_type = ? AND e.scope_id = ? AND e.id <> ?
                              AND (e.normalized_term = ? OR a.normalized_alias = ?)
                            LIMIT 1
                            """,
                            (
                                exists["scope_type"],
                                exists["scope_id"],
                                entry_id,
                                normalized_alias,
                                normalized_alias,
                            ),
                        ).fetchone()
                        if conflict is not None:
                            raise ValueError("alias already belongs to another entry")
                        seen.add(normalized_alias)
                        clean_aliases.append((alias, normalized_alias))
                    conn.execute(
                        "DELETE FROM jargon_aliases WHERE entry_id = ?", (entry_id,)
                    )
                    conn.executemany(
                        """
                        INSERT INTO jargon_aliases (
                            entry_id, alias, normalized_alias, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        [
                            (entry_id, alias, normalized_alias, now)
                            for alias, normalized_alias in clean_aliases
                        ],
                    )
                    conn.execute(
                        "UPDATE jargon_entries SET updated_at = ? WHERE id = ?",
                        (now, entry_id),
                    )
                elif normalized_action == "create_sense":
                    clean = clean_meaning()
                    confidence = max(0.0, min(float(data.get("confidence", 1.0)), 1.0))
                    status = str(data.get("status") or "candidate").strip().lower()
                    if status not in {
                        "candidate",
                        "provisional",
                        "verified",
                        "rejected",
                    }:
                        raise ValueError("unsupported sense status")
                    cursor = conn.execute(
                        """
                        INSERT INTO jargon_senses (
                            entry_id, meaning, normalized_meaning, confidence,
                            status, version, created_by, reason, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, 'admin', 'manual create', ?, ?)
                        """,
                        (
                            entry_id,
                            clean,
                            normalize_term(clean),
                            confidence,
                            status,
                            now,
                            now,
                        ),
                    )
                    if status == "verified" and bool(data.get("preferred", False)):
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                            (int(cursor.lastrowid), entry_id),
                        )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "update_sense":
                    sense_id = sense_id_from()
                    sense = conn.execute(
                        "SELECT * FROM jargon_senses WHERE id = ? AND entry_id = ?",
                        (sense_id, entry_id),
                    ).fetchone()
                    if sense is None:
                        raise ValueError("sense does not belong to entry")
                    clean = clean_meaning()
                    confidence = max(
                        0.0,
                        min(float(data.get("confidence", sense["confidence"])), 1.0),
                    )
                    conn.execute(
                        """
                        UPDATE jargon_senses
                        SET meaning = ?, normalized_meaning = ?, confidence = ?,
                            version = version + 1, created_by = 'admin',
                            reason = 'manual update', updated_at = ? WHERE id = ?
                        """,
                        (clean, normalize_term(clean), confidence, now, sense_id),
                    )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "confirm_sense":
                    sense_id = sense_id_from()
                    cursor = conn.execute(
                        """
                        UPDATE jargon_senses
                        SET status = 'verified', confidence = MAX(confidence, 1.0),
                            created_by = 'admin', updated_at = ?
                        WHERE id = ? AND entry_id = ?
                        """,
                        (now, sense_id, entry_id),
                    )
                    if cursor.rowcount == 0:
                        raise ValueError("sense does not belong to entry")
                    if (
                        bool(data.get("preferred", False))
                        or exists["preferred_sense_id"] is None
                    ):
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                            (sense_id, entry_id),
                        )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "reject_sense":
                    sense_id = sense_id_from()
                    cursor = conn.execute(
                        """
                        UPDATE jargon_senses
                        SET status = 'rejected', created_by = 'admin', updated_at = ?
                        WHERE id = ? AND entry_id = ?
                        """,
                        (now, sense_id, entry_id),
                    )
                    if cursor.rowcount == 0:
                        raise ValueError("sense does not belong to entry")
                    if exists["preferred_sense_id"] == sense_id:
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = NULL WHERE id = ?",
                            (entry_id,),
                        )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "set_preferred":
                    sense_id = sense_id_from()
                    sense = conn.execute(
                        """
                        SELECT id FROM jargon_senses
                        WHERE id = ? AND entry_id = ? AND status = 'verified'
                        """,
                        (sense_id, entry_id),
                    ).fetchone()
                    if sense is None:
                        raise ValueError("preferred sense must be verified")
                    conn.execute(
                        "UPDATE jargon_entries SET preferred_sense_id = ?, updated_at = ? WHERE id = ?",
                        (sense_id, now, entry_id),
                    )
                elif normalized_action == "merge_sense":
                    source_id = sense_id_from(
                        "source_sense_id"
                        if data.get("source_sense_id") is not None
                        else "sense_id"
                    )
                    target_id = sense_id_from("target_sense_id")
                    if source_id == target_id:
                        raise ValueError("source and target senses must differ")
                    rows = conn.execute(
                        """
                        SELECT * FROM jargon_senses
                        WHERE entry_id = ? AND id IN (?, ?)
                        """,
                        (entry_id, source_id, target_id),
                    ).fetchall()
                    by_id = {int(row["id"]): row for row in rows}
                    if source_id not in by_id or target_id not in by_id:
                        raise ValueError("sense does not belong to entry")
                    merged_status = (
                        "verified"
                        if "verified"
                        in {by_id[source_id]["status"], by_id[target_id]["status"]}
                        else str(by_id[target_id]["status"])
                    )
                    conn.execute(
                        "UPDATE jargon_evidence SET sense_id = ? WHERE sense_id = ?",
                        (target_id, source_id),
                    )
                    conn.execute(
                        "UPDATE jargon_inference_logs SET sense_id = ? WHERE sense_id = ?",
                        (target_id, source_id),
                    )
                    conn.execute(
                        """
                        UPDATE jargon_senses
                        SET confidence = MAX(confidence, ?), status = ?,
                            version = version + 1, created_by = 'admin',
                            reason = 'manual merge', updated_at = ? WHERE id = ?
                        """,
                        (
                            float(by_id[source_id]["confidence"]),
                            merged_status,
                            now,
                            target_id,
                        ),
                    )
                    conn.execute("DELETE FROM jargon_senses WHERE id = ?", (source_id,))
                    if exists["preferred_sense_id"] == source_id:
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                            (target_id, entry_id),
                        )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "delete_sense":
                    sense_id = sense_id_from()
                    cursor = conn.execute(
                        "DELETE FROM jargon_evidence WHERE entry_id = ? AND sense_id = ?",
                        (entry_id, sense_id),
                    )
                    conn.execute(
                        "DELETE FROM jargon_inference_logs WHERE entry_id = ? AND sense_id = ?",
                        (entry_id, sense_id),
                    )
                    deleted = conn.execute(
                        "DELETE FROM jargon_senses WHERE entry_id = ? AND id = ?",
                        (entry_id, sense_id),
                    )
                    if deleted.rowcount == 0 and cursor.rowcount == 0:
                        raise ValueError("sense does not belong to entry")
                    if exists["preferred_sense_id"] == sense_id:
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = NULL WHERE id = ?",
                            (entry_id,),
                        )
                    self._refresh_entry_state(conn, entry_id, now)
                elif normalized_action == "delete":
                    conn.execute("DELETE FROM jargon_entries WHERE id = ?", (entry_id,))
                else:
                    raise ValueError(f"unsupported action: {normalized_action}")
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def export_jargons(
        self,
        *,
        search: str = "",
        scope_type: str = "",
        scope_id: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        """Export filtered jargon entries with aliases, senses, and evidence.

        Args:
            search: Optional term, alias, or meaning substring.
            scope_type: Optional exact scope type.
            scope_id: Optional exact scope identifier.
            status: Optional entry, conflict, or disabled status filter.

        Returns:
            Versioned JSON-serializable export payload.
        """
        listing = await self.list_jargons(
            search=search,
            status=status,
            scope_id=scope_id,
            scope_type=scope_type,
            page=1,
            page_size=1_000_000,
        )
        items = []
        for row in listing["items"]:
            detail = await self.get_jargon_detail(int(row["id"]))
            if detail is not None:
                items.append(detail)
        return {
            "schema_version": 2,
            "exported_at": _now(),
            "filters": {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "status": status,
                "search": search,
            },
            "total": len(items),
            "items": items,
        }

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
                       failure_code, failure_detail, model, duration_ms, stage,
                       created_at, CASE WHEN stage = 'final' AND id = (
                           SELECT MAX(final.id) FROM protocol_logs final
                           WHERE final.request_id = protocol_logs.request_id
                             AND final.stage = 'final'
                       ) THEN 1 ELSE 0 END AS is_final
                FROM protocol_logs
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["is_final"] = bool(item["is_final"])
                items.append(item)
            return {"items": items, "total": total}

        return await self._run(operation)

    async def record_protocol_and_enqueue_memory(
        self,
        context: MessageContext,
        outcome: Any | None = None,
        raw_output: str = "",
        model: str = "",
        duration_ms: int = 0,
        request_snapshot: dict[str, Any] | None = None,
        actual_messages: Sequence[str] = (),
        provider_id: str = "",
        scope_type: str = "",
        scope_hash: str = "",
        subject_hash: str = "",
        conversation_hash: str = "",
        action: str = "reply",
        *,
        memory_job: dict[str, Any] | None = None,
        success: bool = True,
        failure_code: str = "",
        failure_detail: str = "",
        messages: Sequence[str] = (),
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool | None = None,
        stage: str = "final",
    ) -> dict[str, Any]:
        """Atomically persist one dispatched response and its extraction job.

        Args:
            context: Trusted request metadata. Raw identifiers are stored only in the
                existing protocol log, never in the new memory tables.
            outcome: Parsed final outcome or a compatible object.
            raw_output: Validated provider output before protocol removal.
            model: Provider model identifier.
            duration_ms: End-to-end request duration.
            request_snapshot: Optional response snapshot retained by protocol audit.
            actual_messages: Exact text successfully delivered to the platform.
            provider_id: Chat Provider selected for background extraction.
            scope_type: Memory visibility scope type.
            scope_hash: HMAC-derived scope identifier.
            subject_hash: HMAC-derived subject identifier.
            conversation_hash: HMAC-derived conversation identifier for ordering.
            action: Normalized reply action.
            memory_job: Runtime-built anonymized extraction job. This is the active
                service contract; the explicit scope arguments remain compatible.
            success: Protocol validation and dispatch outcome.
            failure_code: Stable protocol failure code.
            failure_detail: Bounded protocol failure detail.
            messages: Exact delivered messages used by the active service contract.
            response_snapshot: Complete provider response snapshot.
            response_snapshot_complete: Whether snapshot serialization was lossless.
            stage: Protocol stage, either ``final`` or ``tool``.

        Returns:
            Protocol log and idempotent memory job identifiers.

        Raises:
            ValueError: If required memory hashes or payload fields are invalid.
        """
        if memory_job is not None and not isinstance(memory_job, dict):
            raise ValueError("memory job must be a JSON object")
        job = dict(memory_job or {})
        clean_scope_type = str(
            job.get("scope_type") or scope_type or context.scope_type
        ).strip()[:40]
        clean_scope_hash = str(job.get("scope_hash") or scope_hash or "").strip()[:160]
        clean_subject_hash = str(job.get("subject_hash") or subject_hash or "").strip()[
            :160
        ]
        clean_conversation_hash = str(
            job.get("conversation_hash") or conversation_hash or ""
        ).strip()[:160]
        clean_agent_id = (
            str(
                job.get("agent_id") or getattr(context, "agent_id", "") or "default"
            ).strip()[:160]
            or "default"
        )
        normalized_action = (
            str(job.get("action") or action or "reply")
            .strip()
            .lower()
            .replace(" ", "_")
        )
        if normalized_action not in {"reply", "no_reply"}:
            outcome_action = getattr(outcome, "action", None)
            normalized_action = (
                str(getattr(outcome_action, "value", outcome_action) or "reply")
                .strip()
                .lower()
                .replace(" ", "_")
            )
        if normalized_action not in {"reply", "no_reply"}:
            raise ValueError("unsupported memory turn action")
        outcome_messages = (
            getattr(outcome, "messages", ()) if outcome is not None else ()
        )
        delivered_source = messages or actual_messages or outcome_messages
        delivered = tuple(str(item) for item in delivered_source if str(item))
        protocol_action = str(action or "").strip()
        if not protocol_action:
            protocol_action = "No Reply" if normalized_action == "no_reply" else "Reply"
        normalized_stage = stage if stage in {"final", "tool"} else "final"
        snapshot = (
            response_snapshot if response_snapshot is not None else request_snapshot
        )
        snapshot_complete = (
            bool(response_snapshot_complete)
            if response_snapshot_complete is not None
            else bool(snapshot)
        )
        now = _now_precise()
        enqueue = bool(job) and bool(success) and normalized_stage == "final"
        if enqueue and (not clean_scope_type or not clean_scope_hash):
            raise ValueError("memory scope type and HMAC hash are required")
        job_type = str(job.get("job_type") or "extract_turn").strip().lower()
        if job_type == "extract":
            job_type = "extract_turn"
        if enqueue and job_type not in {
            "extract_turn",
            "embed_example",
        }:
            raise ValueError("unsupported memory job type")
        idempotency_key = str(
            job.get("idempotency_key") or job.get("request_id") or context.request_id
        ).strip()[:160]
        if enqueue and not idempotency_key:
            raise ValueError("memory job idempotency key is required")
        job_key = f"{job_type}:{idempotency_key}"
        clean_provider_id = str(
            job.get("chat_provider_id") or job.get("provider_id") or provider_id or ""
        ).strip()[:160]
        payload = {
            "job_type": job_type,
            "idempotency_key": idempotency_key,
            "request_id": str(job.get("request_id") or context.request_id)[:160],
            "scope_type": clean_scope_type,
            "scope_hash": clean_scope_hash,
            "subject_hash": clean_subject_hash,
            "conversation_hash": clean_conversation_hash,
            "agent_id": clean_agent_id,
            "user_text": str(job.get("user_text", context.user_text) or "")[:8_000],
            "assistant_messages": [
                str(item)[:8_000]
                for item in job.get("assistant_messages", delivered)
                if str(item)
            ][:20],
            "action": normalized_action,
            "chat_provider_id": clean_provider_id,
            "occurred_at": str(job.get("occurred_at") or context.occurred_at or now)[
                :80
            ],
            "source_complete": bool(
                job.get("source_complete", context.source_complete)
            ),
        }
        if job_type == "embed_example":
            payload["entity_id"] = max(0, int(job.get("entity_id") or 0))
        payload_json = _json_text(payload)
        snapshot_json = _json_text(snapshot or {})

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing_log = conn.execute(
                    "SELECT id FROM protocol_logs "
                    "WHERE request_id = ? AND stage = ? AND success = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (context.request_id, normalized_stage, int(bool(success))),
                ).fetchone()
                if existing_log is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO protocol_logs (
                            request_id, scope_type, scope_id, message_id, sender_id,
                            success, action, failure_code, failure_detail, raw_output,
                            raw_output_snapshot, raw_snapshot_complete, messages_json,
                            response_snapshot_json, response_snapshot_complete, model,
                            duration_ms, stage, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            context.request_id,
                            context.scope_type,
                            context.scope_id,
                            context.message_id,
                            context.sender_id,
                            int(bool(success)),
                            protocol_action,
                            str(failure_code or "")[:160],
                            str(failure_detail or "")[:1_000],
                            str(raw_output or "")[: self._raw_log_chars],
                            str(raw_output or ""),
                            _json_text(delivered, "[]"),
                            snapshot_json,
                            int(snapshot_complete),
                            str(model or ""),
                            max(0, int(duration_ms)),
                            normalized_stage,
                            now,
                        ),
                    )
                    protocol_log_id = int(cursor.lastrowid)
                else:
                    protocol_log_id = int(existing_log["id"])
                job_cursor = None
                job_row = None
                if enqueue:
                    job_table_sql = str(
                        conn.execute(
                            "SELECT sql FROM sqlite_master "
                            "WHERE type = 'table' AND name = 'humanize_memory_jobs'"
                        ).fetchone()["sql"]
                    )
                    stored_job_type = job_type
                    if (
                        job_type == "extract_turn"
                        and "extract_turn" not in job_table_sql
                    ):
                        stored_job_type = "extract"
                    job_cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO humanize_memory_jobs (
                            job_key, job_type, request_id, provider_id, scope_type,
                            scope_hash, subject_hash, conversation_hash, agent_id,
                            payload_json, status, attempts, next_run_at, created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                        """,
                        (
                            job_key,
                            stored_job_type,
                            str(job.get("request_id") or context.request_id)[:160],
                            clean_provider_id,
                            clean_scope_type,
                            clean_scope_hash,
                            clean_subject_hash,
                            clean_conversation_hash,
                            clean_agent_id,
                            payload_json,
                            now,
                            now,
                            now,
                        ),
                    )
                    job_row = conn.execute(
                        "SELECT id, status FROM humanize_memory_jobs WHERE job_key = ?",
                        (job_key,),
                    ).fetchone()
                    if job_row is None:
                        raise RuntimeError("memory extraction job was not persisted")
                cutoff = (
                    datetime.now(UTC) - timedelta(days=self._log_retention_days)
                ).isoformat(timespec="seconds")
                conn.execute(
                    "DELETE FROM protocol_logs WHERE created_at < ?", (cutoff,)
                )
                conn.commit()
                return {
                    "protocol_log_id": protocol_log_id,
                    "job_id": int(job_row["id"]) if job_row is not None else None,
                    "job_status": str(job_row["status"]) if job_row is not None else "",
                    "job_created": bool(job_cursor and job_cursor.rowcount == 1),
                }
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def claim_memory_job(
        self, lease_owner: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        """Atomically claim one due memory job.

        Args:
            lease_owner: Unique worker identifier.
            lease_seconds: Lease duration before crash recovery may retry the job.

        Returns:
            Claimed job with decoded payload, or ``None`` when no job is due.

        Raises:
            ValueError: If the worker identifier is empty.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        if not clean_owner:
            raise ValueError("memory job lease owner is required")
        claim_time = datetime.now(UTC)
        now = claim_time.isoformat(timespec="microseconds")
        lease_expires = (
            claim_time + timedelta(seconds=max(1, int(lease_seconds)))
        ).isoformat(timespec="microseconds")

        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE humanize_memory_jobs
                    SET status = 'retry', lease_owner = '', lease_expires_at = NULL,
                        next_run_at = ?, error = 'worker lease expired', updated_at = ?
                    WHERE status = 'running' AND (
                        lease_expires_at IS NULL OR lease_expires_at <= ?
                    )
                    """,
                    (now, now, now),
                )
                row = conn.execute(
                    """
                    SELECT j.* FROM humanize_memory_jobs j
                    WHERE j.status IN ('pending', 'retry') AND j.next_run_at <= ?
                      AND (
                          j.conversation_hash = '' OR NOT EXISTS (
                              SELECT 1 FROM humanize_memory_jobs running
                              WHERE running.status = 'running'
                                AND running.conversation_hash = j.conversation_hash
                                AND running.agent_id = j.agent_id
                          )
                      )
                    ORDER BY j.next_run_at, j.id LIMIT 1
                    """,
                    (now,),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                cursor = conn.execute(
                    """
                    UPDATE humanize_memory_jobs
                    SET status = 'running', attempts = attempts + 1,
                        lease_owner = ?, lease_expires_at = ?, error = '', updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'retry')
                    """,
                    (clean_owner, lease_expires, now, int(row["id"])),
                )
                if cursor.rowcount != 1:
                    conn.rollback()
                    return None
                claimed = conn.execute(
                    "SELECT * FROM humanize_memory_jobs WHERE id = ?",
                    (int(row["id"]),),
                ).fetchone()
                conn.commit()
                if claimed is None:
                    return None
                result = dict(claimed)
                result["payload"] = _json_value(result.pop("payload_json"), {})
                if result.get("job_type") == "extract":
                    result["job_type"] = "extract_turn"
                if isinstance(result["payload"], dict):
                    result["payload"]["job_type"] = result["job_type"]
                return result
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def claim_memory_job_batch(
        self,
        lease_owner: str,
        lease_seconds: int = 90,
        batch_turns: int = 4,
        idle_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        """Claim one immediate job or one eligible same-subject extraction batch.

        Args:
            lease_owner: Unique worker identifier.
            lease_seconds: Lease duration for every claimed row.
            batch_turns: Number of pending turns that triggers immediate extraction.
            idle_seconds: Conversation idle time that flushes a smaller batch.

        Returns:
            Claimed jobs in conversation order, or an empty list when none is due.

        Raises:
            ValueError: If the worker identifier is empty.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        if not clean_owner:
            raise ValueError("memory job lease owner is required")
        batch_size = max(1, min(int(batch_turns), 20))
        idle_delay = max(0, min(int(idle_seconds), 86_400))
        claim_time = datetime.now(UTC)
        now = claim_time.isoformat(timespec="microseconds")
        idle_cutoff = (claim_time - timedelta(seconds=idle_delay)).isoformat(
            timespec="microseconds"
        )
        lease_expires = (
            claim_time + timedelta(seconds=max(1, int(lease_seconds)))
        ).isoformat(timespec="microseconds")

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE humanize_memory_jobs
                    SET status = 'retry', lease_owner = '', lease_expires_at = NULL,
                        next_run_at = ?, error = 'worker lease expired', updated_at = ?
                    WHERE status = 'running' AND (
                        lease_expires_at IS NULL OR lease_expires_at <= ?
                    )
                    """,
                    (now, now, now),
                )
                selected = conn.execute(
                    """
                    SELECT j.* FROM humanize_memory_jobs j
                    WHERE j.status IN ('pending', 'retry') AND j.next_run_at <= ?
                      AND (
                          j.conversation_hash = '' OR NOT EXISTS (
                              SELECT 1 FROM humanize_memory_jobs running
                              WHERE running.status = 'running'
                                AND running.conversation_hash = j.conversation_hash
                                AND running.agent_id = j.agent_id
                          )
                      )
                      AND (
                          j.job_type NOT IN ('extract', 'extract_turn')
                          OR ? <= 1
                          OR (
                              SELECT COUNT(*) FROM humanize_memory_jobs queued
                              WHERE queued.status IN ('pending', 'retry')
                                AND queued.next_run_at <= ?
                                AND queued.job_type IN ('extract', 'extract_turn')
                                AND queued.conversation_hash = j.conversation_hash
                                AND queued.scope_hash = j.scope_hash
                                AND queued.subject_hash = j.subject_hash
                                AND queued.agent_id = j.agent_id
                          ) >= ?
                          OR (
                              SELECT MAX(queued.created_at)
                              FROM humanize_memory_jobs queued
                              WHERE queued.status IN ('pending', 'retry')
                                AND queued.next_run_at <= ?
                                AND queued.job_type IN ('extract', 'extract_turn')
                                AND queued.conversation_hash = j.conversation_hash
                                AND queued.scope_hash = j.scope_hash
                                AND queued.subject_hash = j.subject_hash
                                AND queued.agent_id = j.agent_id
                          ) <= ?
                      )
                    ORDER BY j.next_run_at, j.id LIMIT 1
                    """,
                    (now, batch_size, now, batch_size, now, idle_cutoff),
                ).fetchone()
                if selected is None:
                    conn.commit()
                    return []
                if str(selected["job_type"]) in {"extract", "extract_turn"}:
                    rows = conn.execute(
                        """
                        SELECT * FROM humanize_memory_jobs
                        WHERE status IN ('pending', 'retry') AND next_run_at <= ?
                          AND job_type IN ('extract', 'extract_turn')
                          AND conversation_hash = ? AND scope_hash = ?
                          AND subject_hash = ? AND agent_id = ?
                        ORDER BY next_run_at, id LIMIT ?
                        """,
                        (
                            now,
                            str(selected["conversation_hash"]),
                            str(selected["scope_hash"]),
                            str(selected["subject_hash"]),
                            str(selected["agent_id"]),
                            batch_size,
                        ),
                    ).fetchall()
                else:
                    rows = [selected]
                ids = [int(row["id"]) for row in rows]
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"""
                    UPDATE humanize_memory_jobs
                    SET status = 'running', attempts = attempts + 1,
                        lease_owner = ?, lease_expires_at = ?, error = '', updated_at = ?
                    WHERE id IN ({placeholders})
                      AND status IN ('pending', 'retry')
                    """,
                    (clean_owner, lease_expires, now, *ids),
                )
                if cursor.rowcount != len(ids):
                    conn.rollback()
                    return []
                claimed = conn.execute(
                    f"SELECT * FROM humanize_memory_jobs "
                    f"WHERE id IN ({placeholders}) ORDER BY next_run_at, id",
                    ids,
                ).fetchall()
                conn.commit()
                result: list[dict[str, Any]] = []
                for row in claimed:
                    item = dict(row)
                    item["payload"] = _json_value(item.pop("payload_json"), {})
                    if item.get("job_type") == "extract":
                        item["job_type"] = "extract_turn"
                    if isinstance(item["payload"], dict):
                        item["payload"]["job_type"] = item["job_type"]
                    result.append(item)
                return result
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def renew_memory_job(
        self, job_id: int, lease_owner: str, lease_seconds: int = 90
    ) -> bool:
        """Extend an active memory-job lease owned by the calling worker.

        Args:
            job_id: Claimed job identifier.
            lease_owner: Worker that owns the active lease.
            lease_seconds: New lease duration measured from the renewal time.

        Returns:
            Whether the running job still belonged to the caller and was renewed.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        if not clean_owner or int(job_id) <= 0:
            return False
        renewed_at = datetime.now(UTC)
        now = renewed_at.isoformat(timespec="microseconds")
        lease_expires = (
            renewed_at + timedelta(seconds=max(1, int(lease_seconds)))
        ).isoformat(timespec="microseconds")

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                """
                UPDATE humanize_memory_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (lease_expires, now, int(job_id), clean_owner),
            )
            conn.commit()
            return cursor.rowcount == 1

        return await self._run(operation)

    async def release_memory_job(
        self,
        job_id: int,
        lease_owner: str,
        reason: str = "worker_cancelled",
    ) -> bool:
        """Release an owned running job for immediate retry.

        Args:
            job_id: Claimed job identifier.
            lease_owner: Worker that owns the active lease.
            reason: Bounded diagnostic reason retained on the job.

        Returns:
            Whether the running job still belonged to the caller and was released.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        if not clean_owner or int(job_id) <= 0:
            return False
        now = _now_precise()
        clean_reason = str(reason or "worker_cancelled").strip()[:2_000]

        def operation(conn: sqlite3.Connection) -> bool:
            cursor = conn.execute(
                """
                UPDATE humanize_memory_jobs
                SET status = 'retry', next_run_at = ?, lease_owner = '',
                    lease_expires_at = NULL, error = ?, completed_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (now, clean_reason, now, int(job_id), clean_owner),
            )
            conn.commit()
            return cursor.rowcount == 1

        return await self._run(operation)

    async def complete_memory_job(
        self, job_id: int, lease_owner: str
    ) -> dict[str, Any]:
        """Mark a leased memory job complete and remove its transient payload.

        Args:
            job_id: Claimed job identifier.
            lease_owner: Worker that owns the active lease.

        Returns:
            Updated job summary.

        Raises:
            RuntimeError: If the lease has been lost.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        now = _now_precise()

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            cursor = conn.execute(
                """
                UPDATE humanize_memory_jobs
                SET status = 'completed', payload_json = '{}', lease_owner = '',
                    lease_expires_at = NULL, error = '', completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (now, now, int(job_id), clean_owner),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise RuntimeError("memory job lease was lost")
            row = conn.execute(
                "SELECT id, job_key, job_type, status, attempts, completed_at "
                "FROM humanize_memory_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
            conn.commit()
            return dict(row) if row is not None else {"id": int(job_id)}

        return await self._run(operation)

    async def retry_memory_job(
        self,
        job_id: int,
        lease_owner: str,
        error: str,
        max_attempts: int,
        delay_seconds: int,
    ) -> dict[str, Any]:
        """Release a failed job for retry or move it to dead-letter state.

        Args:
            job_id: Claimed job identifier.
            lease_owner: Worker that owns the active lease.
            error: Bounded failure description.
            max_attempts: Attempt limit including the active claim.
            delay_seconds: Delay before another worker may claim the job.

        Returns:
            Updated job status and schedule.

        Raises:
            RuntimeError: If the lease has been lost.
        """
        clean_owner = str(lease_owner or "").strip()[:160]
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat(timespec="microseconds")
        next_run = (now_dt + timedelta(seconds=max(0, int(delay_seconds)))).isoformat(
            timespec="microseconds"
        )

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT attempts FROM humanize_memory_jobs "
                "WHERE id = ? AND status = 'running' AND lease_owner = ?",
                (int(job_id), clean_owner),
            ).fetchone()
            if row is None:
                raise RuntimeError("memory job lease was lost")
            dead = int(row["attempts"]) >= max(1, int(max_attempts))
            status = "dead" if dead else "retry"
            conn.execute(
                """
                UPDATE humanize_memory_jobs
                SET status = ?, next_run_at = ?, lease_owner = '',
                    lease_expires_at = NULL, error = ?,
                    payload_json = CASE WHEN ? = 'dead' THEN '{}' ELSE payload_json END,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (
                    status,
                    next_run,
                    str(error or "")[:2_000],
                    status,
                    now if dead else None,
                    now,
                    int(job_id),
                    clean_owner,
                ),
            )
            updated = conn.execute(
                "SELECT id, job_key, job_type, status, attempts, next_run_at, error "
                "FROM humanize_memory_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
            conn.commit()
            return dict(updated) if updated is not None else {"id": int(job_id)}

        return await self._run(operation)

    async def list_memory_agent_options(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return persona IDs observed by the local memory subsystem.

        Args:
            limit: Maximum number of distinct IDs to return.

        Returns:
            Persona IDs with aggregate usage counts and latest activity times.
        """
        clean_limit = max(1, min(int(limit), 500))

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT agent_id,
                       SUM(observed_count) AS observed_count,
                       MAX(last_seen_at) AS last_seen_at
                FROM (
                    SELECT agent_id, COUNT(*) AS observed_count,
                           MAX(updated_at) AS last_seen_at
                    FROM humanize_memory_jobs GROUP BY agent_id
                    UNION ALL
                    SELECT agent_id, COUNT(*) AS observed_count,
                           MAX(updated_at) AS last_seen_at
                    FROM humanize_reply_examples GROUP BY agent_id
                    UNION ALL
                    SELECT agent_id, COUNT(*) AS observed_count,
                           MAX(created_at) AS last_seen_at
                    FROM humanize_reply_example_usage GROUP BY agent_id
                ) observed
                WHERE agent_id <> ''
                GROUP BY agent_id
                ORDER BY last_seen_at DESC, agent_id ASC
                LIMIT ?
                """,
                (clean_limit,),
            ).fetchall()
            return [
                {
                    "id": str(row["agent_id"]),
                    "observed_count": int(row["observed_count"] or 0),
                    "last_seen_at": str(row["last_seen_at"] or ""),
                }
                for row in rows
            ]

        return await self._run(operation)

    async def list_memory_jobs(
        self, filters: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Return paginated background memory jobs without exposing payload text.

        Args:
            filters: Optional status, type, provider, HMAC scope, and pagination fields.
            **kwargs: Equivalent explicit fields for compatibility.

        Returns:
            Paginated job summaries.
        """
        options = {**(filters or {}), **kwargs}
        page = max(1, int(options.get("page", 1)))
        page_size = max(1, min(int(options.get("page_size", 50)), 200))
        clauses: list[str] = []
        params: list[Any] = []
        for key in (
            "status",
            "job_type",
            "provider_id",
            "scope_type",
            "scope_hash",
            "agent_id",
        ):
            value = str(options.get(key) or "").strip()
            if value:
                if key == "job_type" and value in {"extract", "extract_turn"}:
                    clauses.append("job_type IN ('extract', 'extract_turn')")
                else:
                    clauses.append(f"{key} = ?")
                    params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM humanize_memory_jobs {where}",
                    params,
                ).fetchone()["count"]
            )
            rows = conn.execute(
                f"""
                SELECT id, job_key, job_type, request_id, provider_id, scope_type,
                       scope_hash, subject_hash, conversation_hash, agent_id, status,
                       attempts, next_run_at, lease_owner, lease_expires_at, error,
                       created_at, updated_at, completed_at
                FROM humanize_memory_jobs {where}
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            items = [dict(row) for row in rows]
            for item in items:
                if item.get("job_type") == "extract":
                    item["job_type"] = "extract_turn"
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        return await self._run(operation)

    async def list_reply_examples(
        self, filters: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Return a paginated reply-example list.

        Args:
            filters: Optional status, enabled, scope, agent, topic, and search fields.
            **kwargs: Equivalent explicit fields for compatibility.

        Returns:
            Paginated examples with decoded turns and tags.
        """
        options = {**(filters or {}), **kwargs}
        page = max(1, int(options.get("page", 1)))
        page_size = max(1, min(int(options.get("page_size", 50)), 200))
        clauses: list[str] = []
        params: list[Any] = []
        for key in (
            "status",
            "scope_type",
            "scope_hash",
            "agent_id",
            "topic",
            "intent",
        ):
            value = str(options.get(key) or "").strip()
            if value:
                clauses.append(f"e.{key} = ?")
                params.append(value)
        if "enabled" in options and options.get("enabled") not in (None, ""):
            enabled = options.get("enabled")
            if isinstance(enabled, str):
                enabled = enabled.strip().lower() in {"1", "true", "yes", "on"}
            clauses.append("e.enabled = ?")
            params.append(int(bool(enabled)))
        search = str(options.get("search") or options.get("query") or "").strip()
        if search:
            escaped = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append(
                "(e.title LIKE ? ESCAPE '\\' OR e.topic LIKE ? ESCAPE '\\' "
                "OR e.intent LIKE ? ESCAPE '\\' OR e.keywords_json LIKE ? ESCAPE '\\' "
                "OR e.style_tags_json LIKE ? ESCAPE '\\' "
                "OR e.turns_json LIKE ? ESCAPE '\\' "
                "OR e.ideal_reply LIKE ? ESCAPE '\\')"
            )
            params.extend([f"%{escaped}%"] * 7)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS count FROM humanize_reply_examples e {where}",
                    params,
                ).fetchone()["count"]
            )
            rows = conn.execute(
                f"""
                SELECT e.* FROM humanize_reply_examples e {where}
                ORDER BY e.updated_at DESC, e.id DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            return {
                "items": [self._reply_example_row(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        return await self._run(operation)

    async def get_reply_example_detail(self, example_id: int) -> dict[str, Any] | None:
        """Return one reply example with revisions, audit, usage, and embeddings.

        Args:
            example_id: Stable example identifier.

        Returns:
            Complete example detail, or ``None`` when missing.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT * FROM humanize_reply_examples WHERE id = ?", (int(example_id),)
            ).fetchone()
            if row is None:
                return None
            item = self._reply_example_row(row)
            revisions = []
            for revision_row in conn.execute(
                "SELECT revision, action, actor, reason, snapshot_json, created_at "
                "FROM humanize_reply_example_revisions WHERE example_id = ? "
                "ORDER BY revision DESC",
                (int(example_id),),
            ).fetchall():
                revision = dict(revision_row)
                revision["snapshot"] = _json_value(revision.pop("snapshot_json"), {})
                revisions.append(revision)
            item["revisions"] = revisions
            item["audit"] = [
                self._audit_row(audit_row)
                for audit_row in conn.execute(
                    "SELECT * FROM humanize_memory_audit "
                    "WHERE entity_type = 'example' AND entity_id = ? "
                    "ORDER BY created_at DESC, id DESC",
                    (int(example_id),),
                ).fetchall()
            ]
            item["usage"] = [
                dict(usage_row)
                for usage_row in conn.execute(
                    "SELECT * FROM humanize_reply_example_usage "
                    "WHERE example_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
                    (int(example_id),),
                ).fetchall()
            ]
            item["embeddings"] = [
                {
                    key: value
                    for key, value in dict(embedding_row).items()
                    if key != "vector_json"
                }
                for embedding_row in conn.execute(
                    "SELECT * FROM humanize_embeddings "
                    "WHERE entity_type = 'example' AND entity_id = ? "
                    "ORDER BY generation DESC, updated_at DESC",
                    (int(example_id),),
                ).fetchall()
            ]
            return item

        return await self._run(operation)

    async def apply_reply_example_action(
        self, payload: dict[str, Any], actor: str = "web_admin"
    ) -> dict[str, Any]:
        """Create, edit, review, enable, or tombstone a reply example.

        Args:
            payload: Administrative action and example fields.
            actor: Audit actor label.

        Returns:
            Persisted example row.

        Raises:
            ValueError: If fields or action are invalid.
            KeyError: If an existing target is missing.
            RuntimeError: If optimistic revision validation fails.
        """
        if not isinstance(payload, dict):
            raise ValueError("reply example action payload must be an object")
        action = str(payload.get("action") or "update").strip().lower()
        if action == "save":
            action = "update" if payload.get("id") else "create"
        supported = {
            "create",
            "update",
            "approve",
            "reject",
            "delete",
            "tombstone",
            "restore",
            "enable",
            "disable",
        }
        if action not in supported:
            raise ValueError("unsupported reply example action")
        clean_actor = str(actor or "web_admin").strip()[:120] or "web_admin"
        reason = str(payload.get("reason") or action).strip()[:500]
        example_id = int(payload.get("id") or payload.get("example_id") or 0)
        if action != "create" and example_id <= 0:
            raise ValueError("reply example id is required")
        expected_revision = int(payload.get("revision") or payload.get("version") or 0)
        now = _now_precise()

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = None
                if action != "create":
                    row = conn.execute(
                        "SELECT * FROM humanize_reply_examples WHERE id = ?",
                        (example_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError("reply example not found")
                    if expected_revision and int(row["revision"]) != expected_revision:
                        raise RuntimeError("reply example revision conflict")
                before = self._reply_example_row(row) if row is not None else {}
                title = str(
                    payload.get("title") or (row["title"] if row else "")
                ).strip()[:240]
                scope_type = str(
                    payload.get("scope_type")
                    or (row["scope_type"] if row else "global")
                ).strip()[:40]
                scope_hash = str(
                    payload["scope_hash"]
                    if "scope_hash" in payload
                    else (row["scope_hash"] if row else "")
                ).strip()[:160]
                subject_hash = str(
                    payload["subject_hash"]
                    if "subject_hash" in payload
                    else (row["subject_hash"] if row else "")
                ).strip()[:160]
                agent_id = (
                    str(
                        payload.get("agent_id")
                        or (row["agent_id"] if row else "default")
                    ).strip()[:160]
                    or "default"
                )
                if not title or not scope_type or not scope_hash:
                    raise ValueError("reply example title and HMAC scope are required")
                turns_value = payload.get(
                    "turns", _json_value(str(row["turns_json"]), []) if row else []
                )
                if not isinstance(turns_value, list) or not 1 <= len(turns_value) <= 3:
                    raise ValueError("reply example requires one to three turns")
                turns: list[dict[str, str]] = []
                for turn in turns_value:
                    if not isinstance(turn, dict):
                        raise ValueError("reply example turn must be an object")
                    role = str(turn.get("role") or "user").strip().lower()
                    content = str(turn.get("content") or "").strip()[:4_000]
                    if role not in {"user", "assistant"} or not content:
                        raise ValueError(
                            "reply example turn has invalid role or content"
                        )
                    turns.append({"role": role, "content": content})
                ideal_reply = str(
                    payload.get("ideal_reply") or (row["ideal_reply"] if row else "")
                ).strip()[:8_000]
                if not ideal_reply:
                    raise ValueError("reply example ideal reply is required")
                style_tags = payload.get(
                    "style_tags",
                    _json_value(str(row["style_tags_json"]), []) if row else [],
                )
                keywords = payload.get(
                    "keywords",
                    _json_value(str(row["keywords_json"]), []) if row else [],
                )
                if not isinstance(style_tags, list) or not isinstance(keywords, list):
                    raise ValueError("reply example tags and keywords must be lists")
                style_tags = list(
                    dict.fromkeys(
                        str(item).strip()[:80]
                        for item in style_tags
                        if str(item).strip()
                    )
                )[:30]
                keywords = list(
                    dict.fromkeys(
                        str(item).strip()[:120]
                        for item in keywords
                        if str(item).strip()
                    )
                )[:50]
                status = str(row["status"] if row else payload.get("status") or "draft")
                enabled = (
                    bool(row["enabled"]) if row else bool(payload.get("enabled", False))
                )
                deleted_at = row["deleted_at"] if row else None
                if action == "approve":
                    status, enabled, deleted_at = "approved", True, None
                elif action == "reject":
                    status, enabled, deleted_at = "rejected", False, None
                elif action in {"delete", "tombstone"}:
                    status, enabled, deleted_at = "tombstoned", False, now
                elif action == "restore":
                    status, enabled, deleted_at = "draft", False, None
                elif action == "enable":
                    if status != "approved":
                        raise ValueError("only approved reply examples may be enabled")
                    enabled = True
                elif action == "disable":
                    enabled = False
                elif action in {"create", "update"}:
                    requested_status = (
                        str(payload.get("status") or status).strip().lower()
                    )
                    if requested_status not in {
                        "draft",
                        "approved",
                        "rejected",
                        "tombstoned",
                    }:
                        raise ValueError("unsupported reply example status")
                    status = requested_status
                    enabled = (
                        bool(payload.get("enabled", enabled)) and status == "approved"
                    )
                    deleted_at = now if status == "tombstoned" else None
                quality = max(
                    0.0,
                    min(
                        float(
                            payload.get(
                                "quality_score", row["quality_score"] if row else 0.8
                            )
                        ),
                        1.0,
                    ),
                )
                values = {
                    "title": title,
                    "scope_type": scope_type,
                    "scope_hash": scope_hash,
                    "subject_hash": subject_hash,
                    "agent_id": agent_id,
                    "topic": str(
                        payload.get("topic", row["topic"] if row else "") or ""
                    )[:240],
                    "intent": str(
                        payload.get("intent", row["intent"] if row else "") or ""
                    )[:240],
                    "style_tags": style_tags,
                    "keywords": keywords,
                    "turns": turns,
                    "ideal_reply": ideal_reply,
                    "conditions": str(
                        payload.get("conditions", row["conditions"] if row else "")
                        or ""
                    )[:2_000],
                    "exclusions": str(
                        payload.get("exclusions", row["exclusions"] if row else "")
                        or ""
                    )[:2_000],
                    "notes": str(
                        payload.get("notes", row["notes"] if row else "") or ""
                    )[:4_000],
                    "status": status,
                    "enabled": enabled,
                    "quality_score": quality,
                    "source_type": str(
                        payload.get(
                            "source_type", row["source_type"] if row else "manual"
                        )
                        or "manual"
                    )[:80],
                    "source_context_run_id": str(
                        payload.get(
                            "source_context_run_id",
                            row["source_context_run_id"] if row else "",
                        )
                        or ""
                    )[:160],
                }
                content_hash = hashlib.sha256(
                    _json_text(values).encode("utf-8", errors="replace")
                ).hexdigest()
                if row is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO humanize_reply_examples (
                            title, scope_type, scope_hash, subject_hash, agent_id,
                            topic, intent, style_tags_json, keywords_json, turns_json,
                            ideal_reply, conditions, exclusions, notes, status, enabled,
                            quality_score, source_type, source_context_run_id,
                            content_hash, revision, created_at, updated_at, deleted_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                        """,
                        (
                            title,
                            scope_type,
                            scope_hash,
                            subject_hash,
                            agent_id,
                            values["topic"],
                            values["intent"],
                            _json_text(style_tags, "[]"),
                            _json_text(keywords, "[]"),
                            _json_text(turns, "[]"),
                            ideal_reply,
                            values["conditions"],
                            values["exclusions"],
                            values["notes"],
                            status,
                            int(enabled),
                            quality,
                            values["source_type"],
                            values["source_context_run_id"],
                            content_hash,
                            now,
                            now,
                            deleted_at,
                        ),
                    )
                    current_id = int(cursor.lastrowid)
                else:
                    current_id = int(row["id"])
                    conn.execute(
                        """
                        UPDATE humanize_reply_examples
                        SET title = ?, scope_type = ?, scope_hash = ?, subject_hash = ?,
                            agent_id = ?, topic = ?, intent = ?, style_tags_json = ?,
                            keywords_json = ?, turns_json = ?, ideal_reply = ?,
                            conditions = ?, exclusions = ?, notes = ?, status = ?,
                            enabled = ?, quality_score = ?, source_type = ?,
                            source_context_run_id = ?, content_hash = ?,
                            revision = revision + 1, updated_at = ?, deleted_at = ?
                        WHERE id = ?
                        """,
                        (
                            title,
                            scope_type,
                            scope_hash,
                            subject_hash,
                            agent_id,
                            values["topic"],
                            values["intent"],
                            _json_text(style_tags, "[]"),
                            _json_text(keywords, "[]"),
                            _json_text(turns, "[]"),
                            ideal_reply,
                            values["conditions"],
                            values["exclusions"],
                            values["notes"],
                            status,
                            int(enabled),
                            quality,
                            values["source_type"],
                            values["source_context_run_id"],
                            content_hash,
                            now,
                            deleted_at,
                            current_id,
                        ),
                    )
                stored = conn.execute(
                    "SELECT * FROM humanize_reply_examples WHERE id = ?", (current_id,)
                ).fetchone()
                if stored is None:
                    raise RuntimeError("reply example was not persisted")
                after = self._reply_example_row(stored)
                conn.execute(
                    """
                    INSERT INTO humanize_reply_example_revisions (
                        example_id, revision, action, actor, reason, snapshot_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        current_id,
                        int(after["revision"]),
                        action,
                        clean_actor,
                        reason,
                        _json_text(after),
                        now,
                    ),
                )
                self._record_memory_audit_sync(
                    conn,
                    entity_type="example",
                    entity_id=current_id,
                    action=action,
                    actor=clean_actor,
                    reason=reason,
                    before=before,
                    after=after,
                    created_at=now,
                )
                self._sync_reply_example_fts_sync(conn, after)
                conn.commit()
                return after
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

    async def search_reply_examples(
        self,
        scope_filters: Any,
        query: str,
        limit: int,
        min_quality: float,
        agent_id: str = "default",
    ) -> list[dict[str, Any]]:
        """Search approved reply examples without ever returning a cached reply.

        Args:
            scope_filters: Allowed HMAC scopes.
            query: Current plain user text.
            limit: Maximum few-shot examples.
            min_quality: Minimum administrator quality score.
            agent_id: Active agent key. Shared examples must explicitly use ``*``.

        Returns:
            Ranked example rows with lexical score components.
        """
        scope_sql, scope_params = self._scope_clause(scope_filters, alias="e")
        clean_query = str(query or "").strip()[:4_000]
        if not scope_sql or not clean_query:
            return []
        bounded_limit = max(1, min(int(limit), 20))
        quality_floor = max(0.0, min(float(min_quality), 1.0))
        normalized_query = normalize_term(clean_query)
        query_compact = "".join(
            character for character in normalized_query if character.isalnum()
        )
        query_bigrams = {
            query_compact[index : index + 2]
            for index in range(max(0, len(query_compact) - 1))
        }
        clean_agent = str(agent_id or "default").strip()[:160] or "default"

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            fts_ids: set[int] = set()
            if self._fts_available:
                try:
                    phrase = '"' + clean_query.replace('"', '""') + '"'
                    fts_ids = {
                        int(row["example_id"])
                        for row in conn.execute(
                            "SELECT example_id FROM humanize_reply_example_fts "
                            "WHERE humanize_reply_example_fts MATCH ? LIMIT 200",
                            (phrase,),
                        ).fetchall()
                    }
                except sqlite3.OperationalError:
                    fts_ids = set()
            rows = conn.execute(
                f"""
                SELECT e.* FROM humanize_reply_examples e
                WHERE ({scope_sql}) AND e.status = 'approved' AND e.enabled = 1
                  AND e.quality_score >= ?
                  AND (
                      e.agent_id IN (?, '*')
                      OR (? = 'default' AND e.agent_id = '')
                  )
                ORDER BY e.quality_score DESC, e.updated_at DESC, e.id ASC LIMIT 500
                """,
                [*scope_params, quality_floor, clean_agent, clean_agent],
            ).fetchall()
            ranked: list[dict[str, Any]] = []
            for row in rows:
                item = self._reply_example_row(row)
                condition_terms = [
                    normalize_term(value)
                    for value in re.split(
                        r"[\r\n,，;；]+", str(item.get("conditions") or "")
                    )
                    if normalize_term(value)
                ]
                exclusion_terms = [
                    normalize_term(value)
                    for value in re.split(
                        r"[\r\n,，;；]+", str(item.get("exclusions") or "")
                    )
                    if normalize_term(value)
                ]
                matched_exclusions: list[str] = []
                for term in exclusion_terms:
                    if term.isascii() and all(
                        character.isalnum() or character.isspace() for character in term
                    ):
                        matched = (
                            re.search(
                                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                                normalized_query,
                            )
                            is not None
                        )
                    else:
                        matched = term in normalized_query
                    if matched:
                        matched_exclusions.append(term)
                if matched_exclusions:
                    continue
                matched_conditions: list[str] = []
                for term in condition_terms:
                    if term.isascii() and all(
                        character.isalnum() or character.isspace() for character in term
                    ):
                        matched = (
                            re.search(
                                rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])",
                                normalized_query,
                            )
                            is not None
                        )
                    else:
                        matched = term in normalized_query
                    if matched:
                        matched_conditions.append(term)
                if condition_terms and not matched_conditions:
                    continue
                fields = [
                    str(item["title"]),
                    str(item["topic"]),
                    str(item["intent"]),
                    *[str(value) for value in item["keywords"]],
                    *[str(turn.get("content") or "") for turn in item["turns"]],
                ]
                normalized_fields = [normalize_term(value) for value in fields]
                exact_keyword = float(
                    normalized_query
                    in {normalize_term(str(value)) for value in item["keywords"]}
                )
                exact_topic = float(
                    normalized_query
                    in {
                        normalize_term(str(item["topic"])),
                        normalize_term(str(item["intent"])),
                    }
                )
                substring = float(
                    any(
                        normalized_query and normalized_query in value
                        for value in normalized_fields
                    )
                )
                field_bigrams: set[str] = set()
                for field in normalized_fields:
                    compact = "".join(
                        character for character in field if character.isalnum()
                    )
                    field_bigrams.update(
                        compact[index : index + 2]
                        for index in range(max(0, len(compact) - 1))
                    )
                bigram_overlap = (
                    len(query_bigrams & field_bigrams) / len(query_bigrams)
                    if query_bigrams
                    else 0.0
                )
                condition_match = float(bool(matched_conditions))
                fts = float(int(item["id"]) in fts_ids)
                if not any(
                    (
                        exact_keyword,
                        exact_topic,
                        substring,
                        bigram_overlap,
                        condition_match,
                        fts,
                    )
                ):
                    continue
                score = (
                    exact_keyword * 0.45
                    + exact_topic * 0.35
                    + substring * 0.2
                    + bigram_overlap * 0.25
                    + condition_match * 0.2
                    + fts * 0.2
                    + float(item["quality_score"]) * 0.2
                )
                item["score"] = round(score, 6)
                item["filter_reason"] = (
                    "conditions_matched:" + ",".join(matched_conditions)[:480]
                    if condition_terms
                    else "no_conditions"
                )
                item["score_components"] = {
                    "exact_keyword": exact_keyword,
                    "exact_topic_or_intent": exact_topic,
                    "substring": substring,
                    "bigram_overlap": round(bigram_overlap, 6),
                    "condition_match": condition_match,
                    "fts": fts,
                    "quality": float(item["quality_score"]),
                }
                ranked.append(item)
            ranked.sort(
                key=lambda item: (
                    -float(item["score"]),
                    -float(item["quality_score"]),
                    str(item["updated_at"]),
                    int(item["id"]),
                )
            )
            return ranked[:bounded_limit]

        return await self._run(operation)

    async def record_reply_example_usage(
        self,
        *,
        request_id: str,
        usages: Sequence[dict[str, Any]] = (),
        scope_type: str = "",
        scope_hash: str = "",
        agent_id: str = "default",
        query: str = "",
        query_hash: str = "",
        example_id: int = 0,
        selected_ids: Sequence[int] = (),
        candidate_count: int = 0,
        score: float = 0.0,
        selected: bool = False,
        reason: str = "",
        rank: int = 0,
        duration_ms: int = 0,
    ) -> None:
        """Record candidate and selected reply-example usage.

        Args:
            request_id: Current request identifier.
            usages: Optional batch of usage dictionaries.
            scope_type: HMAC scope type for a single record.
            scope_hash: HMAC scope identifier for a single record.
            agent_id: Active logical agent used for the retrieval.
            query: Plain query; only its hash is persisted.
            query_hash: Precomputed query hash used by the runtime service.
            example_id: Example identifier for a single record.
            selected_ids: Runtime-selected example identifiers.
            candidate_count: Number of candidates considered.
            score: Retrieval score for a single record.
            selected: Whether the example was injected.
            reason: Selection or filtering reason.
            rank: Stable candidate rank.
            duration_ms: Retrieval duration.
        """
        records = list(usages)
        if not records and example_id:
            records = [
                {
                    "example_id": example_id,
                    "score": score,
                    "selected": selected,
                    "reason": reason,
                    "rank": rank,
                }
            ]
        if not records and selected_ids:
            records = [
                {
                    "example_id": int(selected_id),
                    "selected": True,
                    "reason": reason,
                    "rank": position,
                }
                for position, selected_id in enumerate(selected_ids, start=1)
                if int(selected_id) > 0
            ]
        if not records:
            records = [{"reason": reason}]
        clean_query_hash = str(query_hash or "").strip()[:128]
        if not clean_query_hash:
            clean_query_hash = hashlib.sha256(
                str(query or "").encode("utf-8", errors="replace")
            ).hexdigest()
        now = _now_precise()
        clean_agent = str(agent_id or "default").strip()[:160] or "default"

        def operation(conn: sqlite3.Connection) -> None:
            conn.executemany(
                """
                INSERT INTO humanize_reply_example_usage (
                    request_id, scope_type, scope_hash, agent_id, query_hash, example_id,
                    score, rank, selected, candidate_count, duration_ms, reason,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(request_id or "")[:160],
                        str(item.get("scope_type") or scope_type)[:40],
                        str(item.get("scope_hash") or scope_hash)[:160],
                        str(item.get("agent_id") or clean_agent).strip()[:160]
                        or "default",
                        clean_query_hash,
                        int(item.get("example_id") or 0) or None,
                        float(item.get("score") or 0.0),
                        max(0, int(item.get("rank") or 0)),
                        int(bool(item.get("selected", False))),
                        max(
                            0,
                            int(item.get("candidate_count", candidate_count) or 0),
                        ),
                        max(0, int(item.get("duration_ms", duration_ms) or 0)),
                        str(item.get("reason") or reason)[:500],
                        now,
                    )
                    for item in records
                    if isinstance(item, dict)
                ],
            )
            conn.commit()

        await self._run(operation)

    async def list_recallable_reply_examples(
        self,
        scope_filters: Any,
        min_quality: float = 0.0,
        agent_id: str = "default",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List reviewed examples eligible for scoped vector recall.

        Args:
            scope_filters: Prevalidated HMAC visibility scopes.
            min_quality: Minimum administrator quality score.
            agent_id: Active logical agent identifier.
            limit: Maximum number of rows returned.

        Returns:
            Approved and enabled examples in deterministic order.
        """
        scope_sql, scope_params = self._scope_clause(scope_filters, alias="e")
        if not scope_sql:
            return []
        clean_agent = str(agent_id or "default").strip()[:160] or "default"
        quality_floor = max(0.0, min(float(min_quality), 1.0))
        bounded_limit = max(1, min(int(limit), 5_000))

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                f"""
                SELECT e.* FROM humanize_reply_examples e
                WHERE ({scope_sql}) AND e.status = 'approved' AND e.enabled = 1
                  AND e.quality_score >= ?
                  AND (
                      e.agent_id IN (?, '*')
                      OR (? = 'default' AND e.agent_id = '')
                  )
                  AND TRIM(e.conditions) = '' AND TRIM(e.exclusions) = ''
                ORDER BY e.quality_score DESC, e.updated_at DESC, e.id ASC
                LIMIT ?
                """,
                [
                    *scope_params,
                    quality_floor,
                    clean_agent,
                    clean_agent,
                    bounded_limit,
                ],
            ).fetchall()
            return [self._reply_example_row(row) for row in rows]

        return await self._run(operation)

    async def upsert_embedding(
        self,
        entity_type: str,
        entity_id: int,
        provider_id: str,
        model: str,
        dimension: int,
        vector: Sequence[float],
        generation: str | int,
    ) -> dict[str, Any]:
        """Persist one normalized embedding derived from a reply example.

        Args:
            entity_type: Must be ``example``.
            entity_id: Source row identifier.
            provider_id: Explicit AstrBot embedding Provider identifier.
            model: Provider model identifier.
            dimension: Expected vector dimension.
            vector: Finite non-zero vector values.
            generation: Provider/model/dimension generation fingerprint.

        Returns:
            Stored embedding metadata without duplicating source content.

        Raises:
            ValueError: If metadata or vector values are invalid.
            KeyError: If the source reply example no longer exists.
        """
        clean_type = str(entity_type or "").strip().lower()
        clean_entity_id = int(entity_id)
        clean_provider = str(provider_id or "").strip()[:160]
        clean_model = str(model or "").strip()[:240]
        clean_generation = str(generation or "").strip()[:160]
        clean_dimension = int(dimension)
        if clean_type != "example":
            raise ValueError("unsupported embedding entity type")
        if clean_entity_id <= 0 or not clean_provider or not clean_generation:
            raise ValueError("embedding source, provider, and generation are required")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding vector must contain numbers") from exc
        if clean_dimension <= 0 or len(values) != clean_dimension:
            raise ValueError("embedding dimension does not match vector length")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding vector contains non-finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("embedding vector must be non-zero")
        normalized = [value / norm for value in values]
        vector_json = _json_text(normalized, "[]")
        now = _now_precise()

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            source = conn.execute(
                "SELECT content_hash FROM humanize_reply_examples WHERE id = ?",
                (clean_entity_id,),
            ).fetchone()
            if source is None:
                raise KeyError("embedding source not found")
            content_hash = str(source["content_hash"])
            conn.execute(
                """
                INSERT INTO humanize_embeddings (
                    entity_type, entity_id, provider_id, model, dimension,
                    generation, content_hash, vector_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id, provider_id, model, generation)
                DO UPDATE SET dimension = excluded.dimension,
                              content_hash = excluded.content_hash,
                              vector_json = excluded.vector_json,
                              updated_at = excluded.updated_at
                """,
                (
                    clean_type,
                    clean_entity_id,
                    clean_provider,
                    clean_model,
                    clean_dimension,
                    clean_generation,
                    content_hash,
                    vector_json,
                    now,
                ),
            )
            conn.commit()
            return {
                "entity_type": clean_type,
                "entity_id": clean_entity_id,
                "provider_id": clean_provider,
                "model": clean_model,
                "dimension": clean_dimension,
                "generation": clean_generation,
                "content_hash": content_hash,
                "updated_at": now,
            }

        return await self._run(operation)

    async def list_embeddings(
        self,
        entity_type: str = "",
        provider_id: str = "",
        model: str = "",
        generation: str | int = "",
        entity_ids: Sequence[int] = (),
    ) -> list[dict[str, Any]]:
        """List persisted vectors for an exact Provider generation.

        Args:
            entity_type: Optional ``example`` filter.
            provider_id: Optional exact Provider identifier.
            model: Optional exact model identifier.
            generation: Optional exact generation fingerprint.
            entity_ids: Optional bounded source identifier set.

        Returns:
            Matching rows with decoded vectors.

        Raises:
            ValueError: If the entity type is unsupported.
        """
        clean_type = str(entity_type or "").strip().lower()
        if clean_type and clean_type != "example":
            raise ValueError("unsupported embedding entity type")
        clauses: list[str] = [
            "(entity_type = 'example' AND EXISTS ("
            "SELECT 1 FROM humanize_reply_examples source_example "
            "WHERE source_example.id = humanize_embeddings.entity_id "
            "AND source_example.content_hash = humanize_embeddings.content_hash "
            "AND source_example.status = 'approved' "
            "AND source_example.enabled = 1))"
        ]
        params: list[Any] = []
        if clean_type:
            clauses.append("entity_type = ?")
            params.append(clean_type)
        clean_provider = str(provider_id or "").strip()[:160]
        if clean_provider:
            clauses.append("provider_id = ?")
            params.append(clean_provider)
        clean_model = str(model or "").strip()[:240]
        if clean_model:
            clauses.append("model = ?")
            params.append(clean_model)
        clean_generation = str(generation or "").strip()[:160]
        if clean_generation:
            clauses.append("generation = ?")
            params.append(clean_generation)
        clean_ids = tuple(
            dict.fromkeys(int(value) for value in entity_ids if int(value) > 0)
        )[:10_000]
        if clean_ids:
            placeholders = ",".join("?" for _ in clean_ids)
            clauses.append(f"entity_id IN ({placeholders})")
            params.extend(clean_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT entity_type, entity_id, provider_id, model, dimension, "
                "generation, content_hash, vector_json, updated_at "
                f"FROM humanize_embeddings {where} "
                "ORDER BY generation DESC, entity_type, entity_id",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                vector_value = _json_value(str(item.pop("vector_json")), [])
                item["vector"] = vector_value if isinstance(vector_value, list) else []
                result.append(item)
            return result

        return await self._run(operation)

    @staticmethod
    def _scope_clause(scope_filters: Any, *, alias: str = "") -> tuple[str, list[Any]]:
        """Build a deny-by-default HMAC scope predicate.

        Args:
            scope_filters: One scope dictionary, an ``items`` wrapper, or a sequence.
            alias: Trusted internal SQL table alias.

        Returns:
            SQL predicate and positional parameters. Empty input returns no predicate.
        """
        if isinstance(scope_filters, dict):
            wrapped = scope_filters.get("items")
            values = wrapped if isinstance(wrapped, (list, tuple)) else [scope_filters]
        elif isinstance(scope_filters, (list, tuple)):
            values = scope_filters
        else:
            return "", []
        prefix = f"{alias}." if alias in {"m", "e"} else ""
        clauses: list[str] = []
        params: list[Any] = []
        seen: set[tuple[str, str, str]] = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            scope_type = str(item.get("scope_type") or "").strip()[:40]
            scope_hash = str(item.get("scope_hash") or "").strip()[:160]
            subject_hash = str(item.get("subject_hash") or "").strip()[:160]
            key = (scope_type, scope_hash, subject_hash)
            if not scope_type or not scope_hash or key in seen:
                continue
            seen.add(key)
            clauses.append(
                f"({prefix}scope_type = ? AND {prefix}scope_hash = ? "
                f"AND {prefix}subject_hash = ?)"
            )
            params.extend(key)
        return " OR ".join(clauses), params

    @staticmethod
    def _reply_example_row(
        row: sqlite3.Row | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Decode one reply-example row into the shared service/WebUI shape.

        Args:
            row: SQLite row or mapping.

        Returns:
            Decoded example object, or an empty object for ``None``.
        """
        if row is None:
            return {}
        item = dict(row)
        for column, key in (
            ("style_tags_json", "style_tags"),
            ("keywords_json", "keywords"),
            ("turns_json", "turns"),
        ):
            decoded = _json_value(str(item.pop(column, "[]")), [])
            item[key] = decoded if isinstance(decoded, list) else []
        item["enabled"] = bool(item.get("enabled", False))
        item["example_id"] = int(item.get("id", 0))
        item["version"] = int(item.get("revision", 1))
        return item

    @staticmethod
    def _audit_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        """Decode before/after snapshots from one memory audit row.

        Args:
            row: SQLite audit row.

        Returns:
            Audit object with decoded snapshots.
        """
        item = dict(row)
        item["before"] = _json_value(str(item.pop("before_json", "{}")), {})
        item["after"] = _json_value(str(item.pop("after_json", "{}")), {})
        return item

    @staticmethod
    def _record_memory_audit_sync(
        conn: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: int,
        action: str,
        actor: str,
        reason: str,
        before: dict[str, Any],
        after: dict[str, Any],
        created_at: str,
    ) -> None:
        """Append one immutable memory/example/index audit event.

        Args:
            conn: Active transaction connection.
            entity_type: Audited entity category.
            entity_id: Audited row identifier.
            action: Stable mutation name.
            actor: Mutation actor label.
            reason: Bounded administrator or worker reason.
            before: Pre-mutation snapshot.
            after: Post-mutation snapshot.
            created_at: Transaction timestamp.
        """
        conn.execute(
            """
            INSERT INTO humanize_memory_audit (
                entity_type, entity_id, action, actor, reason,
                before_json, after_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                max(0, int(entity_id)),
                str(action or "")[:80],
                str(actor or "")[:120],
                str(reason or "")[:500],
                _json_text(before),
                _json_text(after),
                created_at,
            ),
        )

    def _sync_reply_example_fts_sync(
        self, conn: sqlite3.Connection, item: dict[str, Any]
    ) -> None:
        """Refresh one optional reply-example FTS document fail-open.

        Args:
            conn: Active transaction connection.
            item: Decoded reply-example snapshot.
        """
        if not self._fts_available:
            return
        example_id = int(item.get("id", 0))
        if example_id <= 0:
            return
        search_text = "\n".join(
            (
                str(item.get("title") or ""),
                str(item.get("topic") or ""),
                str(item.get("intent") or ""),
                " ".join(str(value) for value in item.get("keywords", [])),
                "\n".join(
                    str(turn.get("content") or "")
                    for turn in item.get("turns", [])
                    if isinstance(turn, dict)
                ),
                str(item.get("ideal_reply") or ""),
            )
        )
        try:
            conn.execute(
                "DELETE FROM humanize_reply_example_fts WHERE example_id = ?",
                (example_id,),
            )
            conn.execute(
                "INSERT INTO humanize_reply_example_fts(example_id, search_text) "
                "VALUES (?, ?)",
                (example_id, search_text),
            )
        except sqlite3.OperationalError:
            self._fts_available = False

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
        """Create the active schema and remove obsolete memory and Control storage.

        Args:
            conn: Open SQLite migration connection.

        Raises:
            RuntimeError: If the database is newer than this plugin version.
        """
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {version} is newer than supported {_SCHEMA_VERSION}"
            )

        conn.executescript(_SCHEMA)
        conn.executescript(_PROMPT_TEMPLATE_SCHEMA)
        conn.executescript(_DROP_LEGACY_CONTROL_SCHEMA)
        conn.executescript(_CONTEXT_SCHEMA)
        conn.executescript(_PROVIDER_OBSERVABILITY_SCHEMA)
        conn.executescript(_PROVIDER_CACHE_SCHEMA)
        conn.executescript(_MEMORY_SCHEMA)
        conn.executescript(_DROP_LEGACY_MEMORY_SCHEMA)
        conn.execute("DELETE FROM humanize_embeddings WHERE entity_type = 'memory'")
        conn.execute("DELETE FROM humanize_memory_audit WHERE entity_type = 'memory'")
        conn.execute(
            "DELETE FROM humanize_memory_jobs "
            "WHERE job_type IN ('embed_memory', 'rebuild_index')"
        )
        self._migrate_jargon_v2(conn)

        for table, column, definition in (
            (
                "humanize_context_sections",
                "content_chars",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_context_sections",
                "preview_truncated",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_context_sections",
                "content_snapshot",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_context_sections",
                "snapshot_complete",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_context_runs",
                "request_snapshot_json",
                "TEXT NOT NULL DEFAULT '{}'",
            ),
            (
                "humanize_context_runs",
                "request_snapshot_complete",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            ("protocol_logs", "raw_output_snapshot", "TEXT NOT NULL DEFAULT ''"),
            ("protocol_logs", "raw_snapshot_complete", "INTEGER NOT NULL DEFAULT 0"),
            ("protocol_logs", "messages_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("protocol_logs", "response_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            (
                "protocol_logs",
                "response_snapshot_complete",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_prompt_prefix_samples",
                "conversation_id",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_prompt_prefix_samples",
                "provider_cache_capability",
                "TEXT NOT NULL DEFAULT 'unknown'",
            ),
            (
                "humanize_prompt_prefix_samples",
                "longest_common_prefix_chars",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_prompt_prefix_samples",
                "epoch_reason",
                "TEXT NOT NULL DEFAULT ''",
            ),
            ("humanize_prompt_prefix_samples", "ttft_ms", "INTEGER"),
            (
                "humanize_prompt_templates",
                "memory_extraction_content",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_prompt_templates",
                "reply_examples_content",
                "TEXT NOT NULL DEFAULT ''",
            ),
        ):
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        for table, column, definition in (
            ("humanize_memory_jobs", "scope_type", "TEXT NOT NULL DEFAULT ''"),
            ("humanize_memory_jobs", "scope_hash", "TEXT NOT NULL DEFAULT ''"),
            ("humanize_memory_jobs", "subject_hash", "TEXT NOT NULL DEFAULT ''"),
            (
                "humanize_memory_jobs",
                "conversation_hash",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_memory_jobs",
                "agent_id",
                "TEXT NOT NULL DEFAULT 'default'",
            ),
            (
                "humanize_reply_example_usage",
                "scope_type",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_reply_example_usage",
                "scope_hash",
                "TEXT NOT NULL DEFAULT ''",
            ),
            (
                "humanize_reply_example_usage",
                "agent_id",
                "TEXT NOT NULL DEFAULT 'default'",
            ),
            (
                "humanize_reply_example_usage",
                "query_hash",
                "TEXT NOT NULL DEFAULT ''",
            ),
            ("humanize_reply_example_usage", "rank", "INTEGER NOT NULL DEFAULT 0"),
            (
                "humanize_reply_example_usage",
                "candidate_count",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_reply_example_usage",
                "duration_ms",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "humanize_reply_examples",
                "agent_id",
                "TEXT NOT NULL DEFAULT 'default'",
            ),
        ):
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        conn.executescript(_MEMORY_INDEX_SCHEMA)
        try:
            conn.executescript(_MEMORY_FTS_SCHEMA)
            self._fts_available = True
        except sqlite3.OperationalError as exc:
            if (
                "fts5" not in str(exc).lower()
                and "no such module" not in str(exc).lower()
            ):
                raise
            self._fts_available = False

        if version < 5:
            conn.execute(
                "UPDATE humanize_context_sections "
                "SET content_snapshot = content_preview, snapshot_complete = 0 "
                "WHERE content_snapshot = ''"
            )
            conn.execute(
                "UPDATE protocol_logs "
                "SET raw_output_snapshot = raw_output, raw_snapshot_complete = 0 "
                "WHERE raw_output_snapshot = ''"
            )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_humanize_prefix_samples_conversation "
            "ON humanize_prompt_prefix_samples("
            "scope_type, scope_id, conversation_id, created_at DESC)"
        )
        now = _now()

        if (
            conn.execute(
                "SELECT 1 FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            is None
        ):
            defaults = PromptTemplates()
            table_columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info(humanize_prompt_templates)"
                ).fetchall()
            }
            values: dict[str, Any] = {
                "id": 1,
                "rule_content": defaults.rule,
                "protocol_content": defaults.protocol,
                "repair_content": defaults.repair,
                "memory_extraction_content": defaults.memory_extraction,
                "reply_examples_content": defaults.reply_examples,
                "updated_at": now,
            }
            for legacy_column in (
                "archive_l0_system_content",
                "archive_l0_user_content",
                "archive_l1_system_content",
                "archive_l1_user_content",
            ):
                if legacy_column in table_columns:
                    values[legacy_column] = ""
            names = list(values)
            placeholders = ", ".join("?" for _ in names)
            conn.execute(
                f"INSERT INTO humanize_prompt_templates ({', '.join(names)}) "
                f"VALUES ({placeholders})",
                tuple(values[name] for name in names),
            )
        else:
            conn.execute(
                "UPDATE humanize_prompt_templates "
                "SET memory_extraction_content = ? "
                "WHERE memory_extraction_content = ''",
                (PromptTemplates().memory_extraction,),
            )
            conn.execute(
                "UPDATE humanize_prompt_templates "
                "SET reply_examples_content = ? "
                "WHERE reply_examples_content = ''",
                (PromptTemplates().reply_examples,),
            )
            if version < 22:
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET protocol_content = ?, updated_at = ? "
                    "WHERE protocol_content = ?",
                    (PromptTemplates().protocol, now, LEGACY_PROTOCOL_TEMPLATE),
                )
            if version < 23:
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET repair_content = ?, updated_at = ? "
                    "WHERE repair_content = ?",
                    (PromptTemplates().repair, now, LEGACY_REPAIR_TEMPLATE),
                )

        if self._fts_available and version < 18:
            conn.execute("DELETE FROM humanize_reply_example_fts")
            example_rows = conn.execute(
                """
                SELECT id, title, topic, intent, keywords_json, turns_json, ideal_reply
                FROM humanize_reply_examples
                """
            ).fetchall()
            conn.executemany(
                "INSERT INTO humanize_reply_example_fts(example_id, search_text) "
                "VALUES (?, ?)",
                [
                    (
                        int(row["id"]),
                        "\n".join(
                            str(row[key] or "")
                            for key in (
                                "title",
                                "topic",
                                "intent",
                                "keywords_json",
                                "turns_json",
                                "ideal_reply",
                            )
                        ),
                    )
                    for row in example_rows
                ],
            )
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()

    @staticmethod
    def _migrate_jargon_v2(conn: sqlite3.Connection) -> None:
        """Expand the original single-sense schema without changing entry IDs.

        Args:
            conn: Open SQLite migration connection.
        """
        entry_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jargon_entries)").fetchall()
        }
        for column, definition in (
            ("enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("match_mode", "TEXT NOT NULL DEFAULT 'smart'"),
            ("case_sensitive", "INTEGER NOT NULL DEFAULT 0"),
            ("preferred_sense_id", "INTEGER"),
        ):
            if column not in entry_columns:
                conn.execute(
                    f"ALTER TABLE jargon_entries ADD COLUMN {column} {definition}"
                )
        sense_tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN ('jargon_senses', 'jargon_senses_v3')"
            ).fetchall()
        }
        sense_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(jargon_senses)").fetchall()
        }
        if (
            "jargon_senses" not in sense_tables
            or "normalized_meaning" not in sense_columns
        ):
            if "jargon_senses" in sense_tables:
                conn.execute("ALTER TABLE jargon_senses RENAME TO jargon_senses_v3")
                sense_tables.discard("jargon_senses")
                sense_tables.add("jargon_senses_v3")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jargon_senses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL REFERENCES jargon_entries(id) ON DELETE CASCADE,
                    meaning TEXT NOT NULL,
                    normalized_meaning TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(entry_id, normalized_meaning)
                )
                """
            )
        if "jargon_senses_v3" in sense_tables:
            for row in conn.execute(
                "SELECT * FROM jargon_senses_v3 ORDER BY id"
            ).fetchall():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO jargon_senses (
                        id, entry_id, meaning, normalized_meaning, confidence, status,
                        version, created_by, reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(row["id"]),
                        int(row["entry_id"]),
                        str(row["meaning"]),
                        normalize_term(str(row["meaning"])),
                        float(row["confidence"]),
                        str(row["status"]),
                        int(row["version"]),
                        str(row["created_by"]),
                        str(row["reason"]),
                        str(row["created_at"]),
                        str(row["updated_at"]),
                    ),
                )
            conn.execute("DROP TABLE jargon_senses_v3")
        for table, column in (
            ("jargon_evidence", "sense_id"),
            ("jargon_inference_logs", "sense_id"),
            ("protocol_logs", "stage"),
        ):
            columns = {
                str(row["name"])
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if column not in columns:
                definition = (
                    "INTEGER"
                    if column == "sense_id"
                    else "TEXT NOT NULL DEFAULT 'final'"
                )
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.executescript(_JARGON_V2_SCHEMA)
        conn.execute(
            """
            UPDATE jargon_evidence
            SET sense_id = (
                SELECT s.id FROM jargon_senses s
                WHERE s.entry_id = jargon_evidence.entry_id
                ORDER BY s.id LIMIT 1
            )
            """
        )
        conn.execute(
            """
            UPDATE jargon_inference_logs
            SET sense_id = (
                SELECT s.id FROM jargon_senses s
                WHERE s.entry_id = jargon_inference_logs.entry_id
                ORDER BY s.id LIMIT 1
            )
            """
        )
        conn.execute(
            """
            UPDATE jargon_entries
            SET preferred_sense_id = (
                SELECT s.id FROM jargon_senses s
                WHERE s.entry_id = jargon_entries.id AND s.status <> 'rejected'
                ORDER BY CASE s.status WHEN 'verified' THEN 0 ELSE 1 END,
                         s.confidence DESC, s.id LIMIT 1
            )
            """
        )

    @staticmethod
    def _refresh_entry_state(
        conn: sqlite3.Connection, entry_id: int, updated_at: str
    ) -> None:
        """Derive entry state from active senses without overwriting meanings.

        Args:
            conn: Active transaction connection.
            entry_id: Entry whose aggregate state must be refreshed.
            updated_at: Timestamp shared by the surrounding transaction.
        """
        entry = conn.execute(
            "SELECT preferred_sense_id FROM jargon_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if entry is None:
            return
        senses = conn.execute(
            """
            SELECT id, status, confidence FROM jargon_senses
            WHERE entry_id = ? AND status <> 'rejected'
            ORDER BY CASE status WHEN 'verified' THEN 0 ELSE 1 END,
                     confidence DESC, id
            """,
            (entry_id,),
        ).fetchall()
        verified = [sense for sense in senses if sense["status"] == "verified"]
        if verified:
            status = JargonStatus.VERIFIED.value
        elif len(senses) > 1:
            status = JargonStatus.AMBIGUOUS.value
        elif senses:
            status = str(senses[0]["status"])
        else:
            status = JargonStatus.REJECTED.value
        active_ids = {int(sense["id"]) for sense in senses}
        preferred = entry["preferred_sense_id"]
        if preferred not in active_ids:
            if verified:
                preferred = int(verified[0]["id"])
            elif len(senses) == 1:
                preferred = int(senses[0]["id"])
            else:
                preferred = None
        confidence = max((float(sense["confidence"]) for sense in senses), default=0.0)
        conn.execute(
            """
            UPDATE jargon_entries
            SET status = ?, confidence = ?, preferred_sense_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, confidence, preferred, updated_at, entry_id),
        )

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
