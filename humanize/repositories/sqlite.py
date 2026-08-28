from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, TypeVar

from ..domain.prompts import (
    LEGACY_MESSAGES_PROTOCOL_TEMPLATE,
    LEGACY_PROTOCOL_TEMPLATE,
    LEGACY_REPAIR_TEMPLATE,
    LEGACY_RULE_TEMPLATE,
    LEGACY_VERSIONED_REPAIR_TEMPLATE,
    PromptTemplates,
)
from ..jargon.normalizer import normalize_term
from .base import (
    SQLiteRepositoryBase,
    _now,
)
from .context import ContextRepository
from .embeddings import EmbeddingsRepository
from .image_cache import ImageCacheRepository
from .jargon import JargonRepository
from .memory_jobs import MemoryJobsRepository
from .migrations import (
    _CONTEXT_SCHEMA,
    _DROP_LEGACY_CONTROL_SCHEMA,
    _DROP_LEGACY_MEMORY_SCHEMA,
    _JARGON_V2_SCHEMA,
    _MEMORY_FTS_SCHEMA,
    _MEMORY_INDEX_SCHEMA,
    _MEMORY_SCHEMA,
    _PROMPT_TEMPLATE_SCHEMA,
    _PROVIDER_CACHE_SCHEMA,
    _PROVIDER_OBSERVABILITY_SCHEMA,
    _SCHEMA,
    _SCHEMA_VERSION,
)
from .observability import ObservabilityRepository
from .protocol import ProtocolRepository
from .reply_examples import ReplyExamplesRepository
from .templates import PromptTemplateRepository

T = TypeVar("T")


class SQLiteRepository(
    SQLiteRepositoryBase,
    ContextRepository,
    EmbeddingsRepository,
    ImageCacheRepository,
    JargonRepository,
    MemoryJobsRepository,
    ObservabilityRepository,
    PromptTemplateRepository,
    ProtocolRepository,
    ReplyExamplesRepository,
):
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
            (
                "humanize_context_runs",
                "request_snapshot_final_json",
                "TEXT NOT NULL DEFAULT '{}'",
            ),
            (
                "humanize_context_runs",
                "request_snapshot_final_complete",
                "INTEGER NOT NULL DEFAULT 0",
            ),
            ("protocol_logs", "raw_output_snapshot", "TEXT NOT NULL DEFAULT ''"),
            ("protocol_logs", "raw_snapshot_complete", "INTEGER NOT NULL DEFAULT 0"),
            ("protocol_logs", "messages_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("protocol_logs", "response_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("protocol_logs", "no_reply_reason", "TEXT NOT NULL DEFAULT ''"),
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
            ("humanize_memory_jobs", "result_json", "TEXT NOT NULL DEFAULT '{}'"),
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
            if version < 24:
                # 新版协议：去掉版本号、<Messages> 必填、No Reply 写原因；
                # 只替换未修改过的旧默认，自定义模板保留原样。
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET protocol_content = ?, updated_at = ? "
                    "WHERE protocol_content = ?",
                    (
                        PromptTemplates().protocol,
                        now,
                        LEGACY_MESSAGES_PROTOCOL_TEMPLATE,
                    ),
                )
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET repair_content = ?, updated_at = ? "
                    "WHERE repair_content = ?",
                    (
                        PromptTemplates().repair,
                        now,
                        LEGACY_VERSIONED_REPAIR_TEMPLATE,
                    ),
                )
            if version < 25:
                # 新版基础规则：距离感表述更明确，并新增口语化要求；
                # 只替换未修改过的旧默认，自定义模板保留原样。
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET rule_content = ?, updated_at = ? "
                    "WHERE rule_content = ?",
                    (PromptTemplates().rule, now, LEGACY_RULE_TEMPLATE),
                )
                # v2 移除了 {{version}} 变量：任何仍引用它的协议/修复模板
                # （含用户改过的旧版本）必然校验失效并被读取回退为默认。
                # 这里直接以新默认替换，使自愈落库、告警止息；
                # 不含 {{version}} 的自定义内容不受影响。
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET protocol_content = ?, updated_at = ? "
                    "WHERE protocol_content LIKE '%{{version}}%'",
                    (PromptTemplates().protocol, now),
                )
                conn.execute(
                    "UPDATE humanize_prompt_templates "
                    "SET repair_content = ?, updated_at = ? "
                    "WHERE repair_content LIKE '%{{version}}%'",
                    (PromptTemplates().repair, now),
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
