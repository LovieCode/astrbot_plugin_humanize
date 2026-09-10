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
        self._astrbot_root = root.parent
        self.image_cache_enabled = True
        self.image_cache_max_entries = 10
        self.image_cache_max_sticker_entries = 10

    def data_path(self) -> Path:
        return self._root

    def astrbot_root(self) -> Path:
        return self._astrbot_root


class _RepositoryStub:
    """Repository port stub whose indexing step can be made to fail."""

    def __init__(self, *, fail_upsert: bool = False) -> None:
        self.fail_upsert = fail_upsert
        self.entries: list[dict[str, Any]] = []

    async def upsert_image_cache_entry(self, **kwargs: Any) -> None:
        if self.fail_upsert:
            raise RuntimeError("image cache index unavailable")
        self.entries.append(dict(kwargs))

    async def list_image_cache_entries(
        self, *, limit: int = 0, kind: str = ""
    ) -> list[dict[str, Any]]:
        selected = [
            dict(entry)
            for entry in self.entries
            if not kind or entry.get("kind") == kind
        ]
        if limit > 0:
            return selected[:limit]
        return selected

    async def delete_image_cache_entries(self, hashes: Any) -> None:
        drop = set(hashes)
        self.entries = [e for e in self.entries if e["file_hash"] not in drop]


def _store(tmp_path: Path, repository: _RepositoryStub) -> ImageCacheStore:
    # 仿真实布局：<root>/AstrBot/data 是插件数据目录，AstrBot 根即 <root>/AstrBot，
    # 这样容器形态的 "/workspace/<id>/AstrBot/..." 才有可重基的目标。
    config = _ConfigStub(tmp_path / "AstrBot" / "data")
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


def test_store_passes_kind_and_summary_to_index(tmp_path: Path) -> None:
    """表情包 kind 与段 summary 随索引行落库，供后续转述回退使用。"""

    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        result = await store.store(str(source), kind="sticker", summary="[动画表情]")

        assert result.cached is True
        assert repository.entries[0]["kind"] == "sticker"
        assert repository.entries[0]["summary"] == "[动画表情]"

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


def test_read_touches_lru_timestamp(tmp_path: Path) -> None:
    """A successful read must refresh the entry so eviction stays LRU.

    Regression guard: `last_hit_at` used to be written only on upsert, so
    eviction actually ordered by insert time (FIFO) and the resident image
    tool kept evicting the images it was actively re-reading.
    """

    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        result = await store.store(str(source))
        assert result.cached is True

        repository.touches: list[str] = []  # type: ignore[attr-defined]

        async def touch(*, file_path: str) -> None:  # type: ignore[no-untyped-def]
            repository.touches.append(file_path)  # type: ignore[attr-defined]

        repository.touch_image_cache_entry = touch  # type: ignore[method-assign]

        data = await store.read(result.file_path)
        assert data == b"image-bytes"
        assert repository.touches == [result.file_path]  # type: ignore[attr-defined]

        # 读取失败/路径不存在时不产生 touch。
        repository.touches.clear()  # type: ignore[attr-defined]
        assert await store.read(str(tmp_path / "missing.png")) is None
        assert repository.touches == []  # type: ignore[attr-defined]

    asyncio.run(scenario())


def test_sticker_survives_image_lru_eviction(tmp_path: Path) -> None:
    """普通图按 LRU 淘汰时，表情包（kind='sticker'）不受影响地长期保留。"""

    async def scenario() -> None:
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)
        store._config.image_cache_max_entries = 1

        photo_a = tmp_path / "photo-a.png"
        photo_a.write_bytes(b"photo-a")
        sticker = tmp_path / "sticker.png"
        sticker.write_bytes(b"sticker-bytes")
        photo_b = tmp_path / "photo-b.png"
        photo_b.write_bytes(b"photo-b")

        first = await store.store(str(photo_a))
        kept = await store.store(str(sticker), kind="sticker")
        second = await store.store(str(photo_b))

        assert first.cached and kept.cached and second.cached
        assert not Path(first.file_path).exists(), "最旧的普通图应被淘汰"
        assert Path(second.file_path).exists()
        assert Path(kept.file_path).exists(), "表情包不参与普通图 LRU 淘汰"
        kinds = {entry["file_hash"]: entry["kind"] for entry in repository.entries}
        assert kinds[kept.file_hash] == "sticker"

    asyncio.run(scenario())


def test_read_normalizes_container_rewritten_path(tmp_path: Path) -> None:
    """模型把缓存路径改写成容器挂载点后，仍要能读回同一张图。

    回归守卫：真实事故里模型把
    ``/home/lovie/AstrBot/data/plugin_data/astrbot_plugin_humanize/image_cache/x.jpg``
    写成 ``/workspace/<会话id>/AstrBot/data/plugin_data/.../image_cache/x.jpg``
    （它以为自己在容器里），宿主机上并不存在 ``/workspace``，于是常驻读图
    工具和下游生图插件都拿不到这张图。
    """

    async def scenario() -> None:
        source = tmp_path / "source.png"
        source.write_bytes(b"image-bytes")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        result = await store.store(str(source))
        assert result.cached is True
        astrbot_root = tmp_path / "AstrBot"
        relative = Path(result.file_path).relative_to(astrbot_root).as_posix()
        rewritten = f"/workspace/8db647f94920/AstrBot/{relative}"

        assert store.resolve_path(rewritten) == result.file_path
        assert store.is_cache_path(rewritten) is True
        assert await store.read(rewritten) == b"image-bytes"

        # 模型常见的其余包装形态：反引号/引号包裹、行尾标点、file:// URI、纯文件名。
        # 另外覆盖两种重写边界：前缀大小写不同（/Workspace/），以及容器 id 段直接
        # 被 data 段顶掉（/workspace/data/plugin_data/…）——此时不能把 data 当成 id
        # 剥掉，否则重基锚点就丢了。
        for raw in (
            f"`{rewritten}`",
            f'"{rewritten}"',
            f"{rewritten}，",
            Path(result.file_path).as_uri(),
            Path(result.file_path).name,
            f"/Workspace/8db647f94920/AstrBot/{relative}",
            f"/workspace/{relative}",
            # 引号与句末标点混用
            f'"{rewritten}"，',
            # 模型把整段标注一起传进来
            f"[图片：一只猫（图片路径 {rewritten}）]",
        ):
            assert await store.read(raw) == b"image-bytes", raw

    asyncio.run(scenario())


def test_read_rejects_references_outside_cache(tmp_path: Path) -> None:
    """归一化只做路径猜测，绝不允许把缓存目录之外的文件读进来。"""

    async def scenario() -> None:
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"not-cached")
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)

        outside_posix = outside.as_posix()
        for raw in (
            str(outside),
            f"file://{outside_posix}",
            "/workspace/8db647f94920/AstrBot/../outside.png",
            "/workspace/8db647f94920/AstrBot/AstrBot/../../outside.png",
            "/workspace/8db647f94920/AstrBot/data/plugin_data/astrbot_plugin_humanize/image_cache/missing.png",
        ):
            assert store.resolve_path(raw) == "", raw
            assert store.is_cache_path(raw) is False, raw
            assert await store.read(raw) is None, raw

        # 有效的缓存图不会被误判
        source = tmp_path / "cached.png"
        source.write_bytes(b"image-bytes")
        stored = await store.store(str(source))
        assert store.is_cache_path(stored.file_path) is True

    asyncio.run(scenario())


def test_sticker_cap_evicts_oldest_sticker_only(tmp_path: Path) -> None:
    """表情包也有独立上限：超出时只淘汰最旧的表情包，普通图不受牵连。"""

    async def scenario() -> None:
        repository = _RepositoryStub()
        store = _store(tmp_path, repository)
        store._config.image_cache_max_sticker_entries = 1

        photo = tmp_path / "photo.png"
        photo.write_bytes(b"photo")
        sticker_a = tmp_path / "sticker-a.png"
        sticker_a.write_bytes(b"sticker-a")
        sticker_b = tmp_path / "sticker-b.png"
        sticker_b.write_bytes(b"sticker-b")

        kept_photo = await store.store(str(photo))
        old_sticker = await store.store(str(sticker_a), kind="sticker")
        new_sticker = await store.store(str(sticker_b), kind="sticker")

        assert kept_photo.cached and old_sticker.cached and new_sticker.cached
        assert not Path(old_sticker.file_path).exists(), "最旧的表情包应被淘汰"
        assert Path(new_sticker.file_path).exists()
        assert Path(kept_photo.file_path).exists(), "普通图不应被表情包淘汰影响"
        hashes = {entry["file_hash"] for entry in repository.entries}
        assert new_sticker.file_hash in hashes and kept_photo.file_hash in hashes
        assert old_sticker.file_hash not in hashes

    asyncio.run(scenario())
