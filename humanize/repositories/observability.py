"""observability domain persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from .base import _now

__all__ = ["ObservabilityRepository"]


class ObservabilityRepository:
    """Domain mixin: observability storage."""

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

    async def record_llm_call(
        self,
        *,
        call_type: str,
        scope_type: str = "",
        scope_id: str = "",
        conversation_id: str = "",
        request_id: str = "",
        provider_id: str = "",
        provider_type: str = "",
        model: str = "",
        input_cached: int = 0,
        input_other: int = 0,
        output_tokens: int = 0,
        usage_observed: bool = False,
        duration_ms: int = 0,
        status: str = "ok",
        error: str = "",
    ) -> None:
        """Persist one proxied auxiliary LLM call with provider usage.

        Args:
            call_type: Stable call stage such as ``transcribe_sticker``,
                ``extract`` or ``openviking``.
            scope_type: Scope type when known at call time.
            scope_id: Scope identifier when known.
            conversation_id: Conversation identifier when known.
            request_id: Pipeline request linkage when available.
            provider_id: Non-secret provider instance ID.
            provider_type: Provider adapter type.
            model: Effective model name.
            input_cached: Provider-reported cached input tokens.
            input_other: Provider-reported uncached input tokens.
            output_tokens: Provider-reported output tokens.
            usage_observed: Whether the provider supplied a usage object.
            duration_ms: Measured call duration in milliseconds.
            status: ``ok`` or ``error`` outcome of the provider call.
            error: Truncated error description for failed calls.
        """
        clean_call_type = str(call_type or "").strip()[:40] or "unknown"
        clean_status = str(status or "ok").strip().lower()
        if clean_status not in {"ok", "error"}:
            clean_status = "error" if clean_status else "ok"
        values = (
            clean_call_type,
            str(scope_type or "")[:120],
            str(scope_id or "")[:300],
            str(conversation_id or "")[:300],
            str(request_id or "")[:200],
            str(provider_id or "")[:160],
            str(provider_type or "")[:160],
            str(model or "")[:200],
            max(0, int(input_cached)),
            max(0, int(input_other)),
            max(0, int(output_tokens)),
            int(bool(usage_observed)),
            max(0, int(duration_ms)),
            clean_status,
            str(error or "")[:500],
            _now(),
        )

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO humanize_llm_call_log (
                    call_type, scope_type, scope_id, conversation_id,
                    request_id, provider_id, provider_type, model,
                    input_cached, input_other, output_tokens, usage_observed,
                    duration_ms, status, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.commit()

        await self._run(operation)

    async def get_usage_overview(self, *, days: int = 7) -> dict[str, Any]:
        """Aggregate provider-reported usage across pipeline and auxiliary calls.

        Args:
            days: Trailing window size in days (1-90).

        Returns:
            Totals, per-model, per-call-type, daily and recent-call samples.
            Tokens are provider-reported values only; never estimated.
        """
        window_days = max(1, min(int(days), 90))
        start_date = datetime.now(UTC).date() - timedelta(days=window_days - 1)
        since = f"{start_date.isoformat()}T00:00:00+00:00"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            union_sql = """
                SELECT call_type, source, model, provider_id, scope_type,
                       scope_id, request_id, input_cached,
                       input_other, output_tokens, usage_observed,
                       duration_ms, status, error, created_at
                FROM (
                    SELECT stage AS call_type, 'pipeline' AS source, model,
                           provider_id, scope_type, scope_id, request_id,
                           input_cached, input_other,
                           output_tokens, usage_observed, duration_ms,
                           'ok' AS status, '' AS error, created_at
                    FROM humanize_llm_usage_samples
                    WHERE created_at >= ?
                    UNION ALL
                    SELECT call_type, 'aux' AS source, model, provider_id,
                           scope_type, scope_id, request_id,
                           input_cached, input_other, output_tokens,
                           usage_observed, duration_ms, status, error, created_at
                    FROM humanize_llm_call_log
                    WHERE created_at >= ?
                )
            """
            params = (since, since)
            totals = conn.execute(
                f"""
                SELECT COUNT(*) AS calls,
                       SUM(usage_observed) AS observed,
                       SUM(input_cached) AS input_cached,
                       SUM(input_other) AS input_other,
                       SUM(output_tokens) AS output_tokens,
                       AVG(duration_ms) AS avg_duration_ms
                FROM ({union_sql})
                """,
                params,
            ).fetchone()
            total_calls = int(totals["calls"] or 0)
            total_cached = int(totals["input_cached"] or 0)
            total_other = int(totals["input_other"] or 0)
            total_output = int(totals["output_tokens"] or 0)
            total_input = total_cached + total_other
            cache_share = (
                round(total_cached * 100 / total_input, 1) if total_input else None
            )
            by_model_rows = conn.execute(
                f"""
                SELECT model,
                       COUNT(*) AS calls,
                       SUM(usage_observed) AS observed,
                       SUM(input_cached) AS input_cached,
                       SUM(input_other) AS input_other,
                       SUM(output_tokens) AS output_tokens,
                       AVG(duration_ms) AS avg_duration_ms
                FROM ({union_sql})
                GROUP BY model
                ORDER BY SUM(input_cached + input_other + output_tokens) DESC,
                         calls DESC
                LIMIT 12
                """,
                params,
            ).fetchall()
            by_call_type_rows = conn.execute(
                f"""
                SELECT call_type, source, COUNT(*) AS calls,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                       SUM(input_cached) AS input_cached,
                       SUM(input_other) AS input_other,
                       SUM(output_tokens) AS output_tokens,
                       AVG(duration_ms) AS avg_duration_ms,
                       MAX(created_at) AS last_seen_at
                FROM ({union_sql})
                GROUP BY call_type, source
                ORDER BY calls DESC
                """,
                params,
            ).fetchall()
            daily_rows = conn.execute(
                f"""
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS calls,
                       SUM(input_cached) AS input_cached,
                       SUM(input_other) AS input_other,
                       SUM(output_tokens) AS output_tokens
                FROM ({union_sql})
                GROUP BY substr(created_at, 1, 10)
                """,
                params,
            ).fetchall()
            daily_by_date = {str(row["day"]): row for row in daily_rows}
            daily = []
            for offset in range(window_days):
                day = start_date + timedelta(days=offset)
                row = daily_by_date.get(day.isoformat())
                daily.append(
                    {
                        "date": day.isoformat(),
                        "label": day.strftime("%m-%d"),
                        "calls": int(row["calls"] or 0) if row else 0,
                        "input_cached": int(row["input_cached"] or 0) if row else 0,
                        "input_other": int(row["input_other"] or 0) if row else 0,
                        "output_tokens": int(row["output_tokens"] or 0) if row else 0,
                    }
                )
            recent_rows = conn.execute(
                f"""
                SELECT call_type, source, model, provider_id, scope_type,
                       scope_id, request_id, input_cached,
                       input_other, output_tokens, usage_observed,
                       duration_ms, status, error, created_at
                FROM ({union_sql})
                ORDER BY created_at DESC
                LIMIT 12
                """,
                params,
            ).fetchall()
            return {
                "days": window_days,
                "totals": {
                    "calls": total_calls,
                    "usage_observed_calls": int(totals["observed"] or 0),
                    "input_cached": total_cached,
                    "input_other": total_other,
                    "output_tokens": total_output,
                    "avg_duration_ms": (
                        round(float(totals["avg_duration_ms"] or 0), 1)
                        if total_calls
                        else None
                    ),
                    "cache_share": cache_share,
                },
                "by_model": [dict(row) for row in by_model_rows],
                "by_call_type": [dict(row) for row in by_call_type_rows],
                "daily": daily,
                "recent": [dict(row) for row in recent_rows],
            }

        return await self._run(operation)

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
            pending_rows = conn.execute(
                """
                SELECT e.id, e.term, e.scope_type, e.scope_id, e.status,
                       e.confidence, e.updated_at,
                       (SELECT COUNT(*) FROM jargon_senses s
                        WHERE s.entry_id = e.id
                          AND s.status IN ('candidate', 'provisional')) AS pending_sense_count
                FROM jargon_entries e
                WHERE EXISTS (
                    SELECT 1 FROM jargon_senses s
                    WHERE s.entry_id = e.id
                      AND s.status IN ('candidate', 'provisional')
                )
                ORDER BY e.updated_at DESC, e.id DESC
                LIMIT 10
                """
            ).fetchall()
            pending_items = []
            for row in pending_rows:
                item = dict(row)
                item["pending_sense_count"] = int(item["pending_sense_count"] or 0)
                pending_items.append(item)
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
                "pending_items": pending_items,
            }

        return await self._run(operation)
