"""Proactive evaluation state persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from typing import Any

from .base import _now

__all__ = ["ProactiveRepository"]


class ProactiveRepository:
    """Domain mixin: per-group proactive evaluation window state.

    One row per group scope keeps the adaptive window length plus the last
    evaluation timestamp. Columns left NULL mean "never set"; the service
    layer applies its own defaults.
    """

    async def get_proactive_state(self, *, scope_id: str) -> dict[str, Any]:
        """Read the proactive window state for one group scope.

        Args:
            scope_id: Group session identifier (``unified_msg_origin``).

        Returns:
            State mapping with nullable fields, or ``{}`` when absent.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT window_seconds, last_eval_at, updated_at "
                "FROM humanize_proactive_state WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if row is None:
                return {}
            return dict(row)

        return await self._run(operation)

    async def update_proactive_state(
        self,
        *,
        scope_id: str,
        window_seconds: int | None = None,
        last_eval_at: str | None = None,
    ) -> None:
        """Merge one group's proactive state atomically.

        Fields left as ``None`` keep their stored value; absent rows are
        created on the fly so callers never need a separate insert step.

        Args:
            scope_id: Group session identifier.
            window_seconds: New adaptive window length in seconds.
            last_eval_at: Timestamp of the latest evaluation.
        """

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO humanize_proactive_state (
                        scope_id, window_seconds, last_eval_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope_id) DO UPDATE SET
                        window_seconds = COALESCE(
                            excluded.window_seconds, window_seconds),
                        last_eval_at = COALESCE(
                            excluded.last_eval_at, last_eval_at),
                        updated_at = excluded.updated_at
                    """,
                    (scope_id, window_seconds, last_eval_at, _now()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def reset_proactive_state(self, *, scope_id: str) -> None:
        """Drop one group's proactive state; the next evaluation starts fresh.

        Args:
            scope_id: Group session identifier.
        """

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "DELETE FROM humanize_proactive_state WHERE scope_id = ?",
                    (scope_id,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)
