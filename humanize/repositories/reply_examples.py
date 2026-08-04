"""reply_examples domain persistence for the Humanize repository."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from ..jargon.normalizer import normalize_term
from .base import _json_text, _json_value, _now_precise

__all__ = ["ReplyExamplesRepository"]


class ReplyExamplesRepository:
    """Domain mixin: reply_examples storage."""

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
