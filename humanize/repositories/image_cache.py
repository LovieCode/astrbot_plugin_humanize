"""image cache persistence for the Humanize repository."""

from __future__ import annotations

import sqlite3
from typing import Any

from .base import _now

__all__ = ["ImageCacheRepository"]

# summary 来自平台上报的原始段数据（不可信输入）：只应是一段短名称，
# 单行化、去控制字符并限长，避免换行/注入内容进入转述提示词或落库。
_MAX_SUMMARY_CHARS = 64


def _sanitize_summary(value: Any) -> str:
    text = str(value or "").strip()
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return text[:_MAX_SUMMARY_CHARS]


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
        kind: str = "image",
        summary: str = "",
    ) -> None:
        """Index one cached image, upgrading sticker identity on conflict.

        ``kind`` is sticky: once an entry is a sticker it stays a sticker
        (a later quoted re-store must not downgrade it), while a direct
        sticker send upgrades an entry first seen as a plain image. The
        sticker observation's ``summary`` (sticker name) is kept alongside;
        an empty summary never clears a previously stored name.
        """

        def operation(conn: sqlite3.Connection) -> None:
            now = _now()
            conn.execute(
                """
                INSERT INTO humanize_image_cache (
                    file_hash, file_path, message_id, scope_type, scope_id,
                    file_size, kind, summary, transcription, transcribed_at,
                    created_at, last_hit_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path = excluded.file_path,
                    message_id = excluded.message_id,
                    last_hit_at = excluded.last_hit_at,
                    kind = CASE WHEN humanize_image_cache.kind = 'sticker'
                                     OR excluded.kind = 'sticker'
                                THEN 'sticker' ELSE 'image' END,
                    summary = CASE WHEN excluded.kind = 'sticker'
                                        AND excluded.summary != ''
                                   THEN excluded.summary
                                   ELSE humanize_image_cache.summary END
                """,
                (
                    file_hash,
                    file_path,
                    message_id,
                    scope_type,
                    scope_id,
                    max(0, int(file_size)),
                    kind,
                    _sanitize_summary(summary),
                    now,
                    now,
                ),
            )
            conn.commit()

        await self._run(operation)

    async def get_image_cache_entry(
        self,
        *,
        file_hash: str = "",
        file_path: str = "",
    ) -> dict[str, Any] | None:
        """Fetch one cache entry's identity and transcription metadata.

        Args:
            file_hash: Content hash; takes precedence when both keys are given.
            file_path: Cached file path, used by the resident read tool.

        Returns:
            The entry dict (file_hash, file_path, kind, summary,
            transcription, transcribed_at) or None when absent.
        """
        if not file_hash and not file_path:
            return None

        def operation(conn: sqlite3.Connection) -> dict[str, Any] | None:
            if file_hash:
                row = conn.execute(
                    "SELECT file_hash, file_path, kind, summary, transcription, "
                    "transcribed_at FROM humanize_image_cache WHERE file_hash = ?",
                    (file_hash,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT file_hash, file_path, kind, summary, transcription, "
                    "transcribed_at FROM humanize_image_cache WHERE file_path = ?",
                    (file_path,),
                ).fetchone()
            return dict(row) if row is not None else None

        return await self._run(operation)

    async def save_image_transcription(
        self,
        *,
        file_hash: str,
        kind: str,
        transcription: str,
    ) -> None:
        """Persist one sticker transcription under its content hash."""

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE humanize_image_cache SET kind = ?, transcription = ?, "
                "transcribed_at = ? WHERE file_hash = ?",
                (kind, transcription, _now(), file_hash),
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

    async def list_image_cache_entries(
        self, *, limit: int = 0, kind: str = ""
    ) -> list[dict[str, Any]]:
        """Return cache entries ordered by least recently used first.

        Args:
            limit: When positive, cap the number of returned rows.
            kind: When non-empty, restrict to one entry kind
                ('image' or 'sticker'); empty returns all kinds.
        """

        def operation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            sql = (
                "SELECT id, file_hash, file_path, message_id, scope_type, "
                "scope_id, file_size, kind, summary, transcription, "
                "transcribed_at, created_at, last_hit_at "
                "FROM humanize_image_cache"
            )
            params: list[Any] = []
            if kind:
                sql += " WHERE kind = ?"
                params.append(kind)
            sql += " ORDER BY last_hit_at ASC, id ASC"
            if limit > 0:
                sql += " LIMIT ?"
                rows = conn.execute(sql, (*params, limit)).fetchall()
            else:
                rows = conn.execute(sql, tuple(params)).fetchall()
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
