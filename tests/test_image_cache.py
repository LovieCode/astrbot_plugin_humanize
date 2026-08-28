"""Tests for the image cache store.

Focus on the fail-open contract: `store()` documents that any failure yields
`cached=False`, and callers rely on that to decide whether to point the
message component at the cached copy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot_plugin_humanize.humanize.image_cache import ImageCacheStore


class _ConfigStub:
    """Minimal stand-in for PluginConfig (frozen dataclass, no tmp override)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.image_cache_enabled = True
        self.image_cache_max_entries = 10

    def data_path(self) -> Path:
        return self._root


class _RepositoryStub:
    """Repository port stub whose indexing step can be made to fail."""

    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.fail_upsert = fail_upsert
        self.entries: list[dict[str, Any]] = []

    async def upsert_image_cache_entry(self, **kwargs: Any) -> None:
        if self.fail_upsert:
            raise RuntimeError("image cache index unavailable")
        self.entries.append(dict(kwargs))

    async def list_image_cache_entries(self) -> list[dict[str, Any]]:
        return list(self.entries)

    async def delete_image_cache_entries(self, hashes: Any) -> None:
        drop = set(hashes)
        self.entries = [e for e in self.entries if e["file_hash"] not in drop]


def _store(tmp_path: Path, repository: _RepositoryStub) -> ImageCacheStore:
    config = _ConfigStub(tmp_path / "data")
    return ImageCacheStore(config, repository)


def test_store_reports_cached_on_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        result = await store.store(str(source))

        assert result.cached is True
        assert result.file_path != str(source)
        assert Path(result.file_path).exists()
        assert store.is_cache_path(result.file_path)
        assert len(repository.entries) == 1

    asyncio.run(scenario())


def test_store_reports_uncached_when_index_write_fails(tmp_path: Path) -> None:
    """A file that never reached the index must not be reported as cached.

    Regression guard: the indexing failure used to fall through and return
    `cached=True` with the cache path. `_evict()` reclaims disk space purely
    from index rows, so such a file is never tracked and never cleaned up,
    while callers would still rewrite the message component to point at it.
    """

    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub(fail_upsert=True)
        store = _store(tmp_path, repository)

        result = await store.store(str(source))

        assert result.cached is False
        # Callers keep using the original path when nothing was cached.
        assert result.file_path == str(source)
        assert result.file_hash == ""
        assert repository.entries == []

    asyncio.run(scenario())


def test_store_reports_uncached_when_copy_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        result = await store.store(str(tmp_path / "missing.png"))

        assert result.cached is False
        assert result.file_path == str(tmp_path / "missing.png")

    asyncio.run(scenario())


def test_store_is_noop_when_disabled(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)
        store._config.image_cache_enabled = False

        result = await store.store(str(source))

        assert result.cached is False
        assert result.file_path == str(source)
        assert repository.entries == []

    asyncio.run(scenario())
