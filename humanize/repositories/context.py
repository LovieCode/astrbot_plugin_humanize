"""context domain persistence for the Humanize repository."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ..domain.models import ContextSection, MessageContext
from .base import _now
from .migrations import _CONTEXT_PREVIEW_CHARS

__all__ = ["ContextRepository"]


class ContextRepository:
    """Domain mixin: context storage."""

    async def record_context_run(
        self,
        context: MessageContext,
        sections: Sequence[ContextSection],
        protocol_mode: str,
        request_snapshot: dict[str, Any] | None = None,
        request_snapshot_complete: bool = False,
        request_snapshot_final: dict[str, Any] | None = None,
        request_snapshot_final_complete: bool = False,
    ) -> None:
        """Persist one bounded context-composition trace transactionally.

        Args:
            context: Trusted identifiers for the active request.
            sections: Ordered context sections prepared for the provider request.
            protocol_mode: Configured protocol injection mode.
            request_snapshot: Complete final ``ProviderRequest`` structure captured
                at the request hook.
            request_snapshot_complete: Whether serialization avoided lossy fallbacks.
            request_snapshot_final: Complete provider-visible context captured after
                the agent run assembled the real request.
            request_snapshot_final_complete: Whether final serialization was lossless.
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
            request_snapshot_final_json = json.dumps(
                request_snapshot_final or {},
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
                           request_snapshot_complete, request_snapshot_final_json,
                           request_snapshot_final_complete
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
                        request_snapshot_final_json,
                        int(request_snapshot_final_complete),
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
                            "request_snapshot_final_json",
                            "request_snapshot_final_complete",
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
                        request_snapshot_complete, request_snapshot_final_json,
                        request_snapshot_final_complete, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        request_snapshot_final_json,
                        int(request_snapshot_final_complete),
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

    async def update_context_run_final_snapshot(
        self,
        context: MessageContext,
        request_snapshot_final: dict[str, Any] | None = None,
        request_snapshot_final_complete: bool = False,
    ) -> bool:
        """Persist the provider-visible final context after the agent run.

        The final snapshot is captured after AstrBot assembled the real request
        (persona, knowledge base, file extraction, tool prompts included) and
        after the model produced its reasoning and response. It is written as an
        idempotent update to the existing context run.

        Args:
            context: Trusted identifiers for the active request.
            request_snapshot_final: Complete provider-visible context structure.
            request_snapshot_final_complete: Whether serialization was lossless.

        Returns:
            ``True`` only when an existing run was updated.
        """

        def operation(conn: sqlite3.Connection) -> bool:
            request_snapshot_final_json = json.dumps(
                request_snapshot_final or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    """
                    SELECT id FROM humanize_context_runs WHERE request_id = ?
                    """,
                    (context.request_id,),
                ).fetchone()
                if existing is None:
                    conn.rollback()
                    return False
                conn.execute(
                    """
                    UPDATE humanize_context_runs
                    SET request_snapshot_final_json = ?,
                        request_snapshot_final_complete = ?
                    WHERE request_id = ?
                    """,
                    (
                        request_snapshot_final_json,
                        int(request_snapshot_final_complete),
                        context.request_id,
                    ),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

        return await self._run(operation)

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
                       r.included_sections, r.omitted_sections, r.created_at,
                       fp.success AS protocol_success,
                       fp.action AS protocol_action,
                       fp.failure_code AS protocol_failure_code,
                       fp.duration_ms AS protocol_duration_ms,
                       fp.model AS protocol_model,
                       fp.no_reply_reason AS protocol_no_reply_reason,
                       mp.content_preview AS message_preview
                FROM humanize_context_runs r
                LEFT JOIN (
                    SELECT p.* FROM protocol_logs p
                    JOIN (
                        SELECT request_id, MAX(id) AS final_id
                        FROM protocol_logs WHERE stage = 'final'
                        GROUP BY request_id
                    ) latest ON latest.final_id = p.id
                ) fp ON fp.request_id = r.request_id
                LEFT JOIN humanize_context_sections mp
                    ON mp.run_id = r.id AND mp.section_key = 'current_message'
                {where}
                ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?
                """,
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                if item.get("protocol_success") is None:
                    item["protocol_summary"] = None
                else:
                    item["protocol_summary"] = {
                        "success": bool(item["protocol_success"]),
                        "action": item["protocol_action"],
                        "failure_code": item["protocol_failure_code"],
                        "duration_ms": int(item["protocol_duration_ms"] or 0),
                        "model": item["protocol_model"],
                        "no_reply_reason": item["protocol_no_reply_reason"] or "",
                    }
                for column in (
                    "protocol_success",
                    "protocol_action",
                    "protocol_failure_code",
                    "protocol_duration_ms",
                    "protocol_model",
                    "protocol_no_reply_reason",
                ):
                    item.pop(column, None)
                items.append(item)
            return {
                "items": items,
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
                       request_snapshot_complete, request_snapshot_final_json,
                       request_snapshot_final_complete, created_at
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
            raw_request_snapshot_final = run_item.pop("request_snapshot_final_json")
            request_snapshot_final_complete = bool(
                run_item.pop("request_snapshot_final_complete")
            )
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
            try:
                stored_request_snapshot_final = json.loads(raw_request_snapshot_final)
            except (TypeError, json.JSONDecodeError):
                stored_request_snapshot_final = {}
                request_snapshot_final_complete = False
            if not isinstance(stored_request_snapshot_final, dict):
                stored_request_snapshot_final = {}
                request_snapshot_final_complete = False
            request_snapshot_final = {
                "snapshot_kind": "provider_request_final",
                "snapshot_complete": request_snapshot_final_complete,
                "provider_request": stored_request_snapshot_final or None,
            }
            all_response_rows = conn.execute(
                """
                SELECT success, action, failure_code, failure_detail,
                       raw_output_snapshot, raw_snapshot_complete, messages_json,
                       response_snapshot_json, response_snapshot_complete,
                       model, duration_ms, stage, created_at
                FROM protocol_logs
                WHERE request_id = ?
                ORDER BY id ASC
                """,
                (request_id,),
            ).fetchall()
            response = None
            response_snapshot = None
            response_sequence: list[dict[str, Any]] = []
            latest_row_item: dict[str, Any] | None = None
            latest_response_snapshot: dict[str, Any] | None = None
            for row in all_response_rows:
                row_item = dict(row)
                raw_response_snapshot = row_item.pop("response_snapshot_json")
                llm_snapshot_complete = bool(row_item.pop("response_snapshot_complete"))
                row_item["success"] = bool(row_item["success"])
                row_item["snapshot_complete"] = bool(
                    row_item.pop("raw_snapshot_complete")
                )
                row_item["raw_output"] = row_item.pop("raw_output_snapshot")
                try:
                    row_item["messages"] = json.loads(row_item.pop("messages_json"))
                except (TypeError, json.JSONDecodeError):
                    row_item["messages"] = []
                try:
                    llm_response = json.loads(raw_response_snapshot)
                except (TypeError, json.JSONDecodeError):
                    llm_response = {}
                    llm_snapshot_complete = False
                if not isinstance(llm_response, dict):
                    llm_response = {}
                    llm_snapshot_complete = False
                turn_snapshot = {
                    "snapshot_kind": "llm_response",
                    "snapshot_complete": bool(
                        llm_snapshot_complete and row_item["snapshot_complete"]
                    ),
                    "llm_response": llm_response or None,
                    "protocol": dict(row_item),
                }
                # 主响应取 final 阶段，而非插入顺序的首行（首行是 tool 轮）。
                # 同一请求理论上可能有多个 final（重试），后写覆盖 → 取 id 最大者。
                if row_item["stage"] == "final":
                    response = row_item
                    response_snapshot = turn_snapshot
                latest_row_item = row_item
                latest_response_snapshot = turn_snapshot
                response_sequence.append(
                    {
                        "stage": row_item["stage"],
                        "success": row_item["success"],
                        "action": row_item["action"],
                        "failure_code": row_item["failure_code"],
                        "raw_output": row_item["raw_output"],
                        "messages": row_item["messages"],
                        "snapshot": llm_response or None,
                        "snapshot_complete": bool(
                            llm_snapshot_complete and row_item["snapshot_complete"]
                        ),
                    }
                )
            # 兜底：无 final 阶段（例如只落了 tool 轮）时退化为最后一行，
            # 避免 response 为 None 让调用方拿到空结果。
            if response is None and latest_row_item is not None:
                response = latest_row_item
                response_snapshot = latest_response_snapshot
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
                "request_snapshot_final": request_snapshot_final,
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
                "response_sequence": response_sequence,
            }

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
