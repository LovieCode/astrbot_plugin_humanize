"""Group policy persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from typing import Any

from .base import _now

__all__ = ["GroupPolicyRepository"]

POLICY_MODES = ("silent", "no_proactive", "admin", "mention", "full")
GLOBAL_POLICY_SCOPE = "global"
DEFAULT_POLICY_MODE = "mention"


def _validate_speak_probability(value: Any) -> int | None:
    """Normalize one speak-probability input to ``None`` or an int in 1..100.

    Args:
        value: ``None`` clears the expectation; integers (1-100) set it.

    Returns:
        The normalized value.

    Raises:
        ValueError: When the value is neither clearable nor a valid percent.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("期望发言概率必须是 1-100 的整数或留空")
    if not 1 <= value <= 100:
        raise ValueError("期望发言概率必须在 1-100 之间")
    return value


class GroupPolicyRepository:
    """Domain mixin: per-group participation policy, WebUI managed.

    One row per group scope stores the participation mode; the special
    ``global`` scope holds the default applied to groups without their own
    row. Rows are small and read on every incoming message, so resolution
    stays a single indexed query with no cache.
    """

    async def list_group_policies(self) -> list[dict[str, Any]]:
        """Return every policy row (global default and per-group overrides)."""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT scope_id, mode, speak_probability, updated_at "
                "FROM humanize_group_policy "
                "ORDER BY scope_id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)

    async def set_group_policy_mode(self, *, scope_id: str, mode: str) -> None:
        """Upsert one policy row after validating the mode value."""
        token = str(scope_id or "").strip()
        if not token:
            raise ValueError("缺少会话标识")
        if mode not in POLICY_MODES:
            raise ValueError(f"未知模式: {mode}")

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO humanize_group_policy (scope_id, mode, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(scope_id) DO UPDATE SET
                        mode = excluded.mode,
                        updated_at = excluded.updated_at
                    """,
                    (token, mode, _now()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def set_group_speak_probability(
        self, *, scope_id: str, probability: int | None
    ) -> None:
        """Upsert one row's expected speak probability, leaving mode intact.

        期望发言概率是软性提示（注入 <Rule> 由模型权衡），不是硬限制；
        ``None`` 表示清除该行设置，回退到全局默认（或无期望）。

        Args:
            scope_id: Group scope identifier or the ``global`` default row.
            probability: 1-100 percent, or ``None`` to clear.

        Raises:
            ValueError: On empty scope or an out-of-range probability.
        """
        token = str(scope_id or "").strip()
        if not token:
            raise ValueError("缺少会话标识")
        normalized = _validate_speak_probability(probability)

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO humanize_group_policy
                        (scope_id, mode, speak_probability, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(scope_id) DO UPDATE SET
                        speak_probability = excluded.speak_probability,
                        updated_at = excluded.updated_at
                    """,
                    (token, DEFAULT_POLICY_MODE, normalized, _now()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def clear_group_policy(self, *, scope_id: str) -> None:
        """Drop one policy row (the scope falls back to the global default)."""
        token = str(scope_id or "").strip()
        if not token:
            raise ValueError("缺少会话标识")
        if token == GLOBAL_POLICY_SCOPE:
            raise ValueError("全局默认模式请直接修改，不能清除")

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "DELETE FROM humanize_group_policy WHERE scope_id = ?",
                    (token,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def remember_session(self, *, scope_id: str, display_name: str) -> None:
        """Record one observed session's display name for WebUI reference.

        Called on every incoming group message; the name is refreshed so the
        policy page can label rows and offer candidates when adding overrides.
        """
        token = str(scope_id or "").strip()
        if not token:
            return

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO humanize_session_meta (scope_id, display_name, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(scope_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        updated_at = excluded.updated_at
                    """,
                    (token, str(display_name or "").strip(), _now()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        await self._run(operation)

    async def list_known_sessions(self) -> list[dict[str, Any]]:
        """Return observed sessions with their last known display name."""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT scope_id, display_name, updated_at FROM humanize_session_meta "
                "ORDER BY updated_at DESC, scope_id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)
