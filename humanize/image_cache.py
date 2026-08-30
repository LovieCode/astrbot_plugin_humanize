"""Durable, capped cache for images received by the plugin.

Received images are materialized once, copied into the plugin data directory
and indexed in ``humanize.db``. Component paths are rewritten so AstrBot core
reuses the cached copy instead of its own temporary files, and later turns can
re-read the image by path (resident tool) until the entry is evicted.

Entries carry a kind: regular images are LRU-capped; stickers are kept
long-term under a separate, larger cap and may store a transcription keyed by
content hash so the same sticker is never transcribed twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import PluginConfig
from .ports import RepositoryPort

logger = logging.getLogger("astrbot")


@dataclass(frozen=True, slots=True)
class CachedImage:
    """One cached image with its durable path."""

    file_path: str
    file_hash: str
    cached: bool


class ImageCacheStore:
    """Store received images under the plugin data directory with LRU eviction."""

    def __init__(self, config: PluginConfig, repository: RepositoryPort) -> None:
        self._config = config
        self._repository = repository
        self._root = config.data_path() / "image_cache"

    @property
    def root(self) -> Path:
        """Absolute cache directory (used to validate tool read paths)."""
        return self._root

    @property
    def enabled(self) -> bool:
        """Whether the cache is enabled and sized above zero."""
        return self._config.image_cache_enabled

    def is_cache_path(self, path: str) -> bool:
        """Return True when ``path`` resolves inside the cache directory."""
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self._root.resolve())
            return True
        except (ValueError, OSError):
            return False

    async def store(
        self,
        source_path: str,
        *,
        message_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        kind: str = "image",
    ) -> CachedImage:
        """Copy one materialized image into the cache and index it.

        Args:
            source_path: Local path produced by ``Image.convert_to_file_path``.
            message_id: Message the image arrived with (provenance only).
            scope_type: Conversation scope for auditing.
            scope_id: Conversation scope identifier for auditing.
            kind: Entry kind, ``'image'`` (LRU-evicted) or ``'sticker'``
                (kept long-term under its own cap, carries the transcription).

        Returns:
            A :class:`CachedImage`. On any failure the original path is
            returned with ``cached=False`` so callers fail open.
        """
        if not self.enabled or not source_path:
            return CachedImage(file_path=source_path, file_hash="", cached=False)
        try:
            result = await asyncio.to_thread(self._store_sync, source_path)
        except Exception:
            logger.exception("[Humanize] failed to cache image %s", source_path)
            return CachedImage(file_path=source_path, file_hash="", cached=False)
        try:
            await self._repository.upsert_image_cache_entry(
                file_hash=result[1],
                file_path=str(result[0]),
                message_id=message_id,
                scope_type=scope_type,
                scope_id=scope_id,
                file_size=result[2],
                kind=kind,
            )
            await self._evict()
        except Exception:
            # The file is on disk but has no index row. `_evict()` only cleans
            # files it can see through the index, so reporting cached=True here
            # would hand callers a path that nothing tracks or ever reclaims.
            # Fall back to the original path and report the cache as unused,
            # matching this method's documented fail-open contract.
            logger.exception("[Humanize] failed to index cached image")
            return CachedImage(file_path=source_path, file_hash="", cached=False)
        return CachedImage(file_path=str(result[0]), file_hash=result[1], cached=True)

    def _store_sync(self, source_path: str) -> tuple[Path, str, int]:
        source = Path(source_path)
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{digest}{source.suffix.lower()}"
        if not target.exists():
            target.write_bytes(data)
        return target, digest, len(data)

    async def _evict(self) -> None:
        # 两类各自限长：普通图按 LRU 出，表情包长期保留但受独立上限约束。
        for kind, max_entries in (
            ("image", self._config.image_cache_max_entries),
            ("sticker", self._config.image_cache_max_sticker_entries),
        ):
            entries = await self._repository.list_image_cache_entries(kind=kind)
            overflow = len(entries) - max_entries
            if overflow <= 0:
                continue
            evicted = entries[:overflow]
            await self._repository.delete_image_cache_entries(
                [str(entry["file_hash"]) for entry in evicted]
            )
            for entry in evicted:
                try:
                    path = Path(str(entry["file_path"]))
                    if self.is_cache_path(str(path)):
                        path.unlink(missing_ok=True)
                except OSError:
                    logger.debug(
                        "[Humanize] failed to unlink evicted image %s",
                        entry.get("file_path"),
                    )
            logger.debug(
                "[Humanize] evicted %s %s entries from image cache",
                overflow,
                kind,
            )

    async def read(self, path: str) -> bytes | None:
        """Read one cached image by path, restricted to the cache directory.

        Args:
            path: Image path previously produced by :meth:`store`.

        Returns:
            Image bytes, or None when the path is outside the cache, missing,
            or the cache is disabled.
        """
        if not self.enabled or not path:
            return None
        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self._root.resolve())
        except (ValueError, OSError):
            return None
        if not resolved.is_file():
            return None
        try:
            data = await asyncio.to_thread(resolved.read_bytes)
        except OSError:
            return None
        # 命中即刷新 LRU 时间戳；失败不影响读取（fail-open）。
        touch = getattr(self._repository, "touch_image_cache_entry", None)
        if callable(touch):
            try:
                await touch(file_path=path)
            except Exception:
                logger.debug(
                    "[Humanize] failed to touch image cache entry %s",
                    path,
                    exc_info=True,
                )
        return data
