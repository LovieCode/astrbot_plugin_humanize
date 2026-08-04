"""Jargon (black-talk) domain persistence for the Humanize repository."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from ..domain.models import (
    JargonStatus,
    KnownSense,
    KnownTerm,
    MessageContext,
    UnknownTerm,
)
from ..jargon.normalizer import normalize_term
from .base import _now

__all__ = ["JargonRepository"]


class JargonRepository:
    """Domain mixin: jargon entries, senses, evidence and injection logs."""

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

            def clean_aliases(
                existing_term: str, scope_type: str, scope_id: str
            ) -> list[tuple[str, str]]:
                """Validate optional aliases against scope-wide conflicts.

                Args:
                    existing_term: Entry term that must not collide with an alias.
                    scope_type: Scope type used by the conflict query.
                    scope_id: Scope identifier used by the conflict query.

                Returns:
                    Deduplicated (alias, normalized_alias) pairs.

                Raises:
                    ValueError: If any alias is invalid or already used.
                """
                raw_aliases = data.get("aliases", [])
                if not isinstance(raw_aliases, list):
                    raise ValueError("aliases must be a list")
                clean: list[tuple[str, str]] = []
                seen: set[str] = set()
                for raw_alias in raw_aliases[:50]:
                    alias = str(raw_alias or "").strip()
                    normalized_alias = normalize_term(alias)
                    if not alias or len(alias) > 128 or not normalized_alias:
                        raise ValueError("alias must contain 1 to 128 characters")
                    if normalized_alias in seen or normalized_alias == existing_term:
                        continue
                    conflict = conn.execute(
                        """
                        SELECT e.id FROM jargon_entries e
                        LEFT JOIN jargon_aliases a ON a.entry_id = e.id
                        WHERE e.scope_type = ? AND e.scope_id = ?
                          AND (e.normalized_term = ? OR a.normalized_alias = ?)
                        LIMIT 1
                        """,
                        (scope_type, scope_id, normalized_alias, normalized_alias),
                    ).fetchone()
                    if conflict is not None:
                        raise ValueError("alias already belongs to another entry")
                    seen.add(normalized_alias)
                    clean.append((alias, normalized_alias))
                return clean

            conn.execute("BEGIN IMMEDIATE")
            try:
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

                if normalized_action == "create_entry":
                    term = str(data.get("term") or "").strip()
                    if not term or len(term) > 128:
                        raise ValueError("term must contain 1 to 128 characters")
                    normalized_term = normalize_term(term)
                    if not normalized_term:
                        raise ValueError("term must contain 1 to 128 characters")
                    scope_type = str(data.get("scope_type") or "").strip()
                    scope_id = str(data.get("scope_id") or "").strip()
                    if not scope_type or not scope_id:
                        raise ValueError("scope_type and scope_id are required")
                    clean = clean_meaning()
                    confidence = max(0.0, min(float(data.get("confidence", 1.0)), 1.0))
                    status = str(data.get("status") or "candidate").strip().lower()
                    if status not in {
                        "candidate",
                        "provisional",
                        "verified",
                        "rejected",
                    }:
                        raise ValueError("unsupported entry status")
                    conflict = conn.execute(
                        """
                        SELECT e.id FROM jargon_entries e
                        LEFT JOIN jargon_aliases a ON a.entry_id = e.id
                        WHERE e.scope_type = ? AND e.scope_id = ?
                          AND (e.normalized_term = ? OR a.normalized_alias = ?)
                        LIMIT 1
                        """,
                        (scope_type, scope_id, normalized_term, normalized_term),
                    ).fetchone()
                    if conflict is not None:
                        raise ValueError("term already exists in this scope")
                    aliases = clean_aliases(normalized_term, scope_type, scope_id)
                    cursor = conn.execute(
                        """
                        INSERT INTO jargon_entries (
                            scope_type, scope_id, term, normalized_term, status,
                            occurrence_count, confidence, first_seen_at,
                            last_seen_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope_type,
                            scope_id,
                            term,
                            normalized_term,
                            status,
                            confidence,
                            now,
                            now,
                            now,
                            now,
                        ),
                    )
                    new_entry_id = int(cursor.lastrowid)
                    sense_cursor = conn.execute(
                        """
                        INSERT INTO jargon_senses (
                            entry_id, meaning, normalized_meaning, confidence,
                            status, version, created_by, reason, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, 'admin', 'manual create', ?, ?)
                        """,
                        (
                            new_entry_id,
                            clean,
                            normalize_term(clean),
                            confidence,
                            status,
                            now,
                            now,
                        ),
                    )
                    if status == "verified":
                        conn.execute(
                            "UPDATE jargon_entries SET preferred_sense_id = ? WHERE id = ?",
                            (int(sense_cursor.lastrowid), new_entry_id),
                        )
                    conn.executemany(
                        """
                        INSERT INTO jargon_aliases (
                            entry_id, alias, normalized_alias, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        [
                            (new_entry_id, alias, normalized_alias, now)
                            for alias, normalized_alias in aliases
                        ],
                    )
                    self._refresh_entry_state(conn, new_entry_id, now)
                    conn.commit()
                    return True

                exists = conn.execute(
                    "SELECT * FROM jargon_entries WHERE id = ?", (entry_id,)
                ).fetchone()
                if exists is None:
                    conn.rollback()
                    return False
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
