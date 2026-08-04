"""Shared SQLite infrastructure for the Humanize repository.

This module owns connection management, timestamp/JSON helpers, row
decoders, audit writes, and the async ``_run`` wrapper so the domain
mixins in ``sqlite.py`` stay focused on per-table SQL.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


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


class SQLiteRepositoryBase:
    """Connection lifecycle and shared helpers for domain mixins.

    Attributes:
        db_path: SQLite file path.
    """

    _lock: asyncio.Lock
    _db_path: Path
    _fts_available: bool

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
