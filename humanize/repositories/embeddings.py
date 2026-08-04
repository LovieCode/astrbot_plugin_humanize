"""embeddings domain persistence for the Humanize repository."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from typing import Any

from .base import _json_text, _json_value, _now_precise

__all__ = ["EmbeddingsRepository"]


class EmbeddingsRepository:
    """Domain mixin: embeddings storage."""

    async def upsert_embedding(
        self,
        entity_type: str,
        entity_id: int,
        provider_id: str,
        model: str,
        dimension: int,
        vector: Sequence[float],
        generation: str | int,
    ) -> dict[str, Any]:
        """Persist one normalized embedding derived from a reply example.

        Args:
            entity_type: Must be ``example``.
            entity_id: Source row identifier.
            provider_id: Explicit AstrBot embedding Provider identifier.
            model: Provider model identifier.
            dimension: Expected vector dimension.
            vector: Finite non-zero vector values.
            generation: Provider/model/dimension generation fingerprint.

        Returns:
            Stored embedding metadata without duplicating source content.

        Raises:
            ValueError: If metadata or vector values are invalid.
            KeyError: If the source reply example no longer exists.
        """
        clean_type = str(entity_type or "").strip().lower()
        clean_entity_id = int(entity_id)
        clean_provider = str(provider_id or "").strip()[:160]
        clean_model = str(model or "").strip()[:240]
        clean_generation = str(generation or "").strip()[:160]
        clean_dimension = int(dimension)
        if clean_type != "example":
            raise ValueError("unsupported embedding entity type")
        if clean_entity_id <= 0 or not clean_provider or not clean_generation:
            raise ValueError("embedding source, provider, and generation are required")
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding vector must contain numbers") from exc
        if clean_dimension <= 0 or len(values) != clean_dimension:
            raise ValueError("embedding dimension does not match vector length")
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding vector contains non-finite values")
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("embedding vector must be non-zero")
        normalized = [value / norm for value in values]
        vector_json = _json_text(normalized, "[]")
        now = _now_precise()

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            source = conn.execute(
                "SELECT content_hash FROM humanize_reply_examples WHERE id = ?",
                (clean_entity_id,),
            ).fetchone()
            if source is None:
                raise KeyError("embedding source not found")
            content_hash = str(source["content_hash"])
            conn.execute(
                """
                INSERT INTO humanize_embeddings (
                    entity_type, entity_id, provider_id, model, dimension,
                    generation, content_hash, vector_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_id, provider_id, model, generation)
                DO UPDATE SET dimension = excluded.dimension,
                              content_hash = excluded.content_hash,
                              vector_json = excluded.vector_json,
                              updated_at = excluded.updated_at
                """,
                (
                    clean_type,
                    clean_entity_id,
                    clean_provider,
                    clean_model,
                    clean_dimension,
                    clean_generation,
                    content_hash,
                    vector_json,
                    now,
                ),
            )
            conn.commit()
            return {
                "entity_type": clean_type,
                "entity_id": clean_entity_id,
                "provider_id": clean_provider,
                "model": clean_model,
                "dimension": clean_dimension,
                "generation": clean_generation,
                "content_hash": content_hash,
                "updated_at": now,
            }

        return await self._run(operation)

    async def list_embeddings(
        self,
        entity_type: str = "",
        provider_id: str = "",
        model: str = "",
        generation: str | int = "",
        entity_ids: Sequence[int] = (),
    ) -> list[dict[str, Any]]:
        """List persisted vectors for an exact Provider generation.

        Args:
            entity_type: Optional ``example`` filter.
            provider_id: Optional exact Provider identifier.
            model: Optional exact model identifier.
            generation: Optional exact generation fingerprint.
            entity_ids: Optional bounded source identifier set.

        Returns:
            Matching rows with decoded vectors.

        Raises:
            ValueError: If the entity type is unsupported.
        """
        clean_type = str(entity_type or "").strip().lower()
        if clean_type and clean_type != "example":
            raise ValueError("unsupported embedding entity type")
        clauses: list[str] = [
            "(entity_type = 'example' AND EXISTS ("
            "SELECT 1 FROM humanize_reply_examples source_example "
            "WHERE source_example.id = humanize_embeddings.entity_id "
            "AND source_example.content_hash = humanize_embeddings.content_hash "
            "AND source_example.status = 'approved' "
            "AND source_example.enabled = 1))"
        ]
        params: list[Any] = []
        if clean_type:
            clauses.append("entity_type = ?")
            params.append(clean_type)
        clean_provider = str(provider_id or "").strip()[:160]
        if clean_provider:
            clauses.append("provider_id = ?")
            params.append(clean_provider)
        clean_model = str(model or "").strip()[:240]
        if clean_model:
            clauses.append("model = ?")
            params.append(clean_model)
        clean_generation = str(generation or "").strip()[:160]
        if clean_generation:
            clauses.append("generation = ?")
            params.append(clean_generation)
        clean_ids = tuple(
            dict.fromkeys(int(value) for value in entity_ids if int(value) > 0)
        )[:10_000]
        if clean_ids:
            placeholders = ",".join("?" for _ in clean_ids)
            clauses.append(f"entity_id IN ({placeholders})")
            params.extend(clean_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT entity_type, entity_id, provider_id, model, dimension, "
                "generation, content_hash, vector_json, updated_at "
                f"FROM humanize_embeddings {where} "
                "ORDER BY generation DESC, entity_type, entity_id",
                params,
            ).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                vector_value = _json_value(str(item.pop("vector_json")), [])
                item["vector"] = vector_value if isinstance(vector_value, list) else []
                result.append(item)
            return result

        return await self._run(operation)
