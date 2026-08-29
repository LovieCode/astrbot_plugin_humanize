"""image cache persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from typing import Any

from .base import _now

__all__ = ["ImageCacheRepository"]


class ImageCacheRepository:
    """Domain mixin: LRU image cache index. Files live on disk; only metadata
    is stored here."""

    async def upsert_image_cache_entry(
        self,
        *,
        file_hash: str,
        file_path: str,
        message_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        file_size: int = 0,
    ) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            conn.execute(
                """
                INSERT INTO humanize_image_cache (
                    file_hash, file_path, message_id, scope_type, scope_id,
                    file_size, created_at, last_hit_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path = excluded.file_path,
                    message_id = excluded.message_id,
                    last_hit_at = excluded.last_hit_at
                """,
                (
                    file_hash,
                    file_path,
                    message_id,
                    scope_type,
                    scope_id,
                    max(0, int(file_size)),
                    now,
                    now,
                ),
            )
            conn.commit()

        await self._run(operation)

    async def touch_image_cache_entry(self, *, file_path: str) -> None:
        """Refresh one entry's LRU timestamp after a read hit.

        Without this, eviction degrades to FIFO on insert time and the
        resident image tool would keep evicting actively used images.

        Args:
            file_path: Cached file path exactly as stored in the index.
        """

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE humanize_image_cache SET last_hit_at = ? WHERE file_path = ?",
                (_now(), file_path),
            )
            conn.commit()

        await self._run(operation)

    async def list_image_cache_entries(self, *, limit: int = 0) -> list[dict[str, Any]]:
        """Return cache entries ordered by least recently used first."""

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            sql = (
                "SELECT id, file_hash, file_path, message_id, scope_type, "
                "scope_id, file_size, created_at, last_hit_at "
                "FROM humanize_image_cache ORDER BY last_hit_at ASC, id ASC"
            )
            if limit > 0:
                sql += " LIMIT ?"
                rows = conn.execute(sql, (limit,)).fetchall()
            else:
                rows = conn.execute(sql).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)

    async def delete_image_cache_entries(self, file_hashes: list[str]) -> None:
        if not file_hashes:
            return

        def operation(conn: sqlite3.Connection) -> None:
            placeholders = ",".join("?" for _ in file_hashes)
            conn.execute(
                f"DELETE FROM humanize_image_cache WHERE file_hash IN ({placeholders})",
                tuple(file_hashes),
            )
            conn.commit()

        await self._run(operation)
