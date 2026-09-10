"""Durable, capped cache for images received by the plugin.

Received images are materialized once, copied into the plugin data directory
and indexed in ``humanize.db``. Component paths are rewritten so AstrBot core
reuses the cached copy instead of its own temporary files, and later turns can
re-read the image by path (resident tool) until the entry is evicted.

Entries carry a kind: regular images are LRU-capped; stickers are kept
long-term under a separate, larger cap and may store a transcription keyed by
content hash so the same sticker is never transcribed twice.

References handed back to us by a model are normalized before use: models
sometimes rewrite an absolute path into the mount point of a container they
believe they run in (``/workspace/<session-id>/AstrBot/data/...``), or wrap it
in quotes and punctuation. :func:`reference_candidates` expands such a value
into the plausible local paths, and every candidate still has to stay inside
the cache directory before it is read.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import PluginConfig
from .ports import RepositoryPort

logger = logging.getLogger("astrbot")

# 模型侧可能出现的容器挂载点前缀：/workspace/<会话或容器id>/...
# 大小写按模型输出放宽（/Workspace/ 也认）；这里与 astrbot_plugin-paint 的
# 同名逻辑是一份语义契约，改动必须两边同步。
_SANDBOX_PREFIX = re.compile(r"^/workspace/[^/]+/", re.IGNORECASE)
_QUOTE_CHARS = "\"'`“”‘’"
_TRAILING_CHARS = "，。；、,;）)】]"


def _unwrap_reference(raw: object) -> str:
    """Strip quotes, backticks and sentence punctuation a model may add.

    模型会写出 ``"路径"，`` 这种引号与句末标点混用的形态，也会只给半边引号，
    所以要反复剥到稳定为止；剥过头的风险由调用方兜底——原值本身同样会作为候选。
    """
    text = str(raw or "").strip()
    while True:
        while text and text[-1] in _TRAILING_CHARS:
            text = text[:-1].strip()
        if len(text) >= 2 and text[0] in _QUOTE_CHARS and text[-1] in _QUOTE_CHARS:
            text = text[1:-1].strip()
            continue
        if text and text[0] in _QUOTE_CHARS:
            text = text[1:].strip()
            continue
        if text and text[-1] in _QUOTE_CHARS:
            text = text[:-1].strip()
            continue
        return text


def _file_uri_to_path(text: str) -> str:
    """Convert a ``file://`` reference to a local path, else return ``text``."""
    if not text.lower().startswith("file:"):
        return text
    parsed = urlsplit(text)
    path = unquote(parsed.path or "")
    host = parsed.netloc or ""
    if host and host.lower() != "localhost":
        if len(host) == 2 and host[1] == ":" and host[0].isalpha():
            return f"{host}{path}"
        return f"//{host}{path}"
    if len(path) >= 3 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
        path = path[1:]
    return path


# 消息里的标注格式是「[图片：…（图片路径 <path>）]」，模型有时会整段照抄进来。
_ANNOTATION_PATH = re.compile(r"图片路径\s*([^（）()\s]+)")


def _embedded_references(text: str) -> list[str]:
    """Return paths found inside a copied annotation, in order."""
    found: list[str] = []
    for match in _ANNOTATION_PATH.finditer(text):
        value = match.group(1).strip()
        if value and value not in found:
            found.append(value)
    return found


def _sandbox_remainder(text: str) -> str:
    """Return the part of ``text`` after a container mount prefix, else ``''``."""
    match = _SANDBOX_PREFIX.match(text)
    if not match:
        return ""
    return text[match.end() :]


def _rebase_candidates(remainder: str, astrbot_root: Path) -> list[str]:
    """Map the tail of a container path back onto the AstrBot install root."""
    segments = [segment for segment in remainder.split("/") if segment]
    if not segments:
        return []
    candidates: list[str] = []
    root_name = astrbot_root.name.lower()
    for index, segment in enumerate(segments):
        if segment.lower() == root_name:
            candidates.append(str(astrbot_root.joinpath(*segments[index + 1 :])))
            break
    else:
        candidates.append(str(astrbot_root.joinpath(*segments)))
    for index, segment in enumerate(segments):
        if segment == "data":
            candidates.append(str(astrbot_root.joinpath(*segments[index:])))
            break
    return candidates


def reference_candidates(
    raw: object,
    *,
    astrbot_root: Path,
    bare_roots: Sequence[Path] = (),
) -> list[str]:
    """Expand a model-supplied image reference into plausible local paths.

    Args:
        raw: Value as reported by the model, possibly rewritten into a
            container mount path (``/workspace/<id>/AstrBot/...``), wrapped in
            quotes or trailing punctuation, or a bare file name.
        astrbot_root: AstrBot install root used to rebase container paths.
        bare_roots: Directories searched when the reference has no directory
            part at all.

    Returns:
        Candidate paths in preference order, the value as given first. Every
        candidate is only a guess: callers must still validate it (the image
        cache additionally requires the path to stay inside its own root).
    """
    text = _unwrap_reference(raw)
    if not text:
        return []
    ordered: list[str] = []

    def push(value: object) -> None:
        candidate = str(value)
        if candidate and candidate not in ordered:
            ordered.append(candidate)

    def expand(value: str) -> None:
        local = _file_uri_to_path(value)
        push(local)
        remainder = _sandbox_remainder(local)
        if remainder:
            push(remainder)
            # 重基时同时按「剥掉前缀后的剩余段」和「完整路径」各试一次：模型可能把
            # id 段和 data 段并到一起（/workspace/data/plugin_data/…），只剥一层会
            # 把 data 锚点吃掉。
            for candidate in _rebase_candidates(remainder, astrbot_root):
                push(candidate)
            for candidate in _rebase_candidates(local, astrbot_root):
                push(candidate)
        if "/" not in local and "\\" not in local:
            for root in bare_roots:
                push(Path(root) / local)

    # 原值本身可能就可用（例如文件名里真的带引号），优先试它，去包装的结果排后面。
    push(str(raw or "").strip())
    push(text)
    expand(text)
    # 模型可能把整段「[图片：…（图片路径 …）]」标注一起传进来，把其中的路径也展开。
    for embedded in _embedded_references(text):
        expand(embedded)
    return ordered


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
        self._astrbot_root = self._resolve_astrbot_root(config)

    @staticmethod
    def _resolve_astrbot_root(config: PluginConfig) -> Path:
        """Read the AstrBot root from config, falling back to the data path."""
        provider = getattr(config, "astrbot_root", None)
        if callable(provider):
            try:
                root = Path(str(provider()))
                if str(root):
                    return root
            except Exception:
                logger.debug(
                    "[Humanize] failed to resolve the AstrBot root", exc_info=True
                )
        data_path = Path(config.data_path())
        parents = data_path.parents
        return parents[2] if len(parents) > 2 else data_path

    @property
    def root(self) -> Path:
        """Absolute cache directory (used to validate tool read paths)."""
        return self._root

    @property
    def enabled(self) -> bool:
        """Whether the cache is enabled and sized above zero."""
        return self._config.image_cache_enabled

    def _candidates(self, path: str) -> list[Path]:
        """Expand one reference into the local paths it may really mean."""
        if not path:
            return []
        return [
            Path(item)
            for item in reference_candidates(
                path,
                astrbot_root=self._astrbot_root,
                bare_roots=(self._root,),
            )
        ]

    def _inside_cache(self, candidate: Path) -> bool:
        """Return True when ``candidate`` resolves inside the cache directory."""
        try:
            candidate.resolve().relative_to(self._root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def is_cache_path(self, path: str) -> bool:
        """Return True when ``path`` resolves inside the cache directory.

        Container-style rewrites (``/workspace/<id>/AstrBot/...``) and
        ``file://`` references are recognized as pointing at the cache when
        their normalized candidate lands inside it.
        """
        return any(
            self._inside_cache(candidate) for candidate in self._candidates(path)
        )

    def resolve_path(self, path: str) -> str:
        """Return the cached file path a model-supplied reference means.

        Args:
            path: Reference as reported by the model; may be rewritten into a
                container mount path, a ``file://`` URI, or a quoted value.

        Returns:
            The candidate path that exists inside the cache directory, or an
            empty string when nothing usable matches.
        """
        for candidate in self._candidates(path):
            if not self._inside_cache(candidate):
                continue
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
        return ""

    async def store(
        self,
        source_path: str,
        *,
        message_id: str = "",
        scope_type: str = "",
        scope_id: str = "",
        kind: str = "image",
        summary: str = "",
    ) -> CachedImage:
        """Copy one materialized image into the cache and index it.

        Args:
            source_path: Local path produced by ``Image.convert_to_file_path``.
            message_id: Message the image arrived with (provenance only).
            scope_type: Conversation scope for auditing.
            scope_id: Conversation scope identifier for auditing.
            kind: Entry kind, ``'image'`` (LRU-evicted) or ``'sticker'``
                (kept long-term under its own cap, carries the transcription).
            summary: Raw segment summary (sticker name) for sticker entries;
                passed to the transcription prompt on later reads.

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
                summary=summary,
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
                raw_path = str(entry.get("file_path") or "")
                # 校验和删除必须落在同一个路径上：先归一化出缓存内的真实文件，
                # 再删它；否则校验通过的是候选路径、被删的却是原始字符串。
                target = self.resolve_path(raw_path)
                if not target:
                    continue
                try:
                    Path(target).unlink(missing_ok=True)
                except OSError:
                    logger.debug(
                        "[Humanize] failed to unlink evicted image %s",
                        raw_path,
                    )
            logger.debug(
                "[Humanize] evicted %s %s entries from image cache",
                overflow,
                kind,
            )

    async def read(self, path: str) -> bytes | None:
        """Read one cached image by path, restricted to the cache directory.

        Args:
            path: Image path previously produced by :meth:`store`, or a
                model-supplied rewrite of it (see :meth:`resolve_path`).

        Returns:
            Image bytes, or None when no candidate stays inside the cache,
            exists, or the cache is disabled.
        """
        if not self.enabled or not path:
            return None
        target = self.resolve_path(path)
        if not target:
            return None
        try:
            data = await asyncio.to_thread(Path(target).read_bytes)
        except OSError:
            return None
        # 命中即刷新 LRU 时间戳；失败不影响读取（fail-open）。
        touch = getattr(self._repository, "touch_image_cache_entry", None)
        if callable(touch):
            try:
                await touch(file_path=target)
            except Exception:
                logger.debug(
                    "[Humanize] failed to touch image cache entry %s",
                    target,
                    exc_info=True,
                )
        return data
