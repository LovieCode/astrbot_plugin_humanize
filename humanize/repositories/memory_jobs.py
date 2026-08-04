"""memory_jobs domain persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .base import _json_value, _now_precise

__all__ = ["MemoryJobsRepository"]


class MemoryJobsRepository:
    """Domain mixin: memory_jobs storage."""

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
