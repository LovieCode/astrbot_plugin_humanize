"""protocol domain persistence for the Humanize repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ..domain.models import MessageContext
from .base import _json_text, _now, _now_precise

__all__ = ["ProtocolRepository"]

# 协议日志的合法阶段：正常 final/tool 之外，主动触发走 proactive_<kind>。
_PROTOCOL_STAGES = {"final", "tool", "proactive_window", "proactive_direct"}
# 记忆抽取只在模型真正回复的正式回合发生。
_MEMORY_JOB_STAGES = {"final", "proactive_window", "proactive_direct"}


class ProtocolRepository:
    """Domain mixin: protocol storage."""

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
        no_reply_reason: str = "",
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
        model: str,
        duration_ms: int,
        stage: str = "final",
    ) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            normalized_stage = stage if stage in _PROTOCOL_STAGES else "final"
            conn.execute(
                """
                INSERT INTO protocol_logs (
                    request_id, scope_type, scope_id, message_id, sender_id,
                    success, action, failure_code, failure_detail, raw_output,
                    raw_output_snapshot, raw_snapshot_complete, messages_json,
                    no_reply_reason,
                    response_snapshot_json, response_snapshot_complete, model,
                    duration_ms, stage, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    str(no_reply_reason or "")[:500],
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
        no_reply_reason: str = "",
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
        outcome_messages = (
            getattr(outcome, "messages", ()) if outcome is not None else ()
        )
        delivered_source = messages or actual_messages or outcome_messages
        delivered = tuple(str(item) for item in delivered_source if str(item))
        protocol_action = str(action or "").strip()
        if not protocol_action:
            protocol_action = "No Reply" if normalized_action == "no_reply" else "Reply"
        normalized_stage = stage if stage in _PROTOCOL_STAGES else "final"
        snapshot = (
            response_snapshot if response_snapshot is not None else request_snapshot
        )
        snapshot_complete = (
            bool(response_snapshot_complete)
            if response_snapshot_complete is not None
            else bool(snapshot)
        )
        now = _now_precise()
        enqueue = bool(job) and bool(success) and normalized_stage in _MEMORY_JOB_STAGES
        # Wait 只出现在无记忆任务的主动轮（等待不产生回合），此时动作仅进
        # 协议日志；只有真正入队抽取任务时才要求动作是 reply/no_reply。
        if normalized_action not in {"reply", "no_reply"} and (
            enqueue or not bool(job)
        ):
            raise ValueError("unsupported memory turn action")
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
            "action": "No Reply" if normalized_action == "no_reply" else "Reply",
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
                            no_reply_reason,
                            response_snapshot_json, response_snapshot_complete, model,
                            duration_ms, stage, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            str(no_reply_reason or "")[:500],
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
