"""Controlled filesystem workspace for the embedded OpenViking core."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..vendor.openviking_core import UPSTREAM_COMMIT, UPSTREAM_TAG

_FORMAT_VERSION = 1
_LOCK_POLL_SECONDS = 0.05
_LOCK_TIMEOUT_SECONDS = 5.0
_STALE_LOCK_SECONDS = 300.0


class WorkspacePathError(ValueError):
    """Raised when a requested workspace path escapes the controlled root."""


class WorkspaceVersionError(RuntimeError):
    """Raised when a workspace requires an explicit migration."""


class WorkspaceTransaction:
    """Perform validated workspace operations while its file lock is held."""

    def __init__(self, workspace: OpenVikingWorkspace) -> None:
        """Bind a transaction view to its owning workspace.

        Args:
            workspace: Locked workspace owner.
        """
        self._workspace = workspace

    def is_file(self, relative_path: str | Path) -> bool:
        """Return whether a validated transaction path is a file.

        Args:
            relative_path: Path relative to the workspace root.

        Returns:
            Whether the path currently points to a file.
        """
        return self._workspace.resolve_path(relative_path).is_file()

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Read one validated file while the transaction lock is held.

        Args:
            relative_path: Path relative to the workspace root.

        Returns:
            Complete file content.
        """
        return self._workspace.resolve_path(relative_path).read_bytes()

    def atomic_write(self, relative_path: str | Path, data: str | bytes) -> Path:
        """Atomically replace one validated file without taking another lock.

        Args:
            relative_path: Destination relative to the workspace root.
            data: UTF-8 text or raw bytes to persist.

        Returns:
            Absolute destination path.

        Raises:
            TypeError: If ``data`` is not text or bytes.
        """
        if isinstance(data, str):
            payload = data.encode("utf-8")
        elif isinstance(data, bytes):
            payload = data
        else:
            raise TypeError("workspace data must be str or bytes")
        destination = self._workspace.resolve_path(relative_path)
        self._workspace._write_bytes_unlocked(destination, payload)
        return destination

    def list_files(
        self, relative_directory: str | Path, *, suffix: str = ""
    ) -> tuple[Path, ...]:
        """List direct child files in one validated directory.

        Args:
            relative_directory: Directory relative to the workspace root.
            suffix: Optional filename suffix filter.

        Returns:
            Sorted absolute child paths.
        """
        directory = self._workspace.resolve_path(relative_directory)
        if not directory.is_dir():
            return ()
        return tuple(
            sorted(
                path
                for path in directory.iterdir()
                if path.is_file() and (not suffix or path.name.endswith(suffix))
            )
        )

    def list_files_recursive(
        self,
        relative_directory: str | Path,
        *,
        suffix: str = "",
        limit: int = 10_000,
    ) -> tuple[Path, ...]:
        """List validated workspace-relative files below one directory.

        Args:
            relative_directory: Directory relative to the workspace root.
            suffix: Optional filename suffix filter.
            limit: Maximum number of files returned.

        Returns:
            Sorted relative paths safe to pass to other transaction methods.

        Raises:
            ValueError: If ``limit`` is not positive.
            WorkspacePathError: If a discovered path escapes the workspace.
        """
        if limit <= 0:
            raise ValueError("workspace recursive file limit must be positive")
        directory = self._workspace.resolve_path(relative_directory)
        if not directory.is_dir():
            return ()
        files: list[Path] = []
        for path in directory.rglob("*"):
            relative_path = path.relative_to(self._workspace.root)
            validated = self._workspace.resolve_path(relative_path)
            if validated.is_file() and (not suffix or validated.name.endswith(suffix)):
                files.append(relative_path)
                if len(files) >= limit:
                    break
        return tuple(sorted(files))


@dataclass(frozen=True, slots=True)
class WorkspaceManifest:
    """Persisted format and upstream source identity for one workspace."""

    format_version: int
    upstream_tag: str
    upstream_commit: str
    created_at: str
    recovered_from: str = ""

    def to_dict(self) -> dict[str, str | int]:
        """Return a JSON-compatible manifest payload.

        Returns:
            Manifest fields suitable for JSON serialization.
        """
        return asdict(self)


class OpenVikingWorkspace:
    """Own atomic and path-safe access to embedded OpenViking files."""

    MANIFEST_NAME = ".workspace.json"
    LOCK_NAME = ".workspace.lock"

    def __init__(self, data_root: Path) -> None:
        """Bind the workspace to the plugin's controlled data directory.

        Args:
            data_root: Humanize plugin data directory from ``PluginConfig.data_path``.

        Raises:
            WorkspacePathError: If the resolved workspace is outside ``data_root``.
        """
        self._data_root = Path(data_root).resolve(strict=False)
        self._root = (self._data_root / "openviking").resolve(strict=False)
        if (
            not self._root.is_relative_to(self._data_root)
            or self._root == self._data_root
        ):
            raise WorkspacePathError(
                "OpenViking workspace must be inside plugin data root"
            )
        self._process_lock = threading.RLock()

    @property
    def root(self) -> Path:
        """Return the controlled workspace root.

        Returns:
            Absolute workspace root path.
        """
        return self._root

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Resolve and validate one workspace-relative path.

        Args:
            relative_path: Relative file or directory path inside the workspace.

        Returns:
            Absolute normalized path inside the workspace.

        Raises:
            WorkspacePathError: If the path is empty, absolute, or escapes the root.
        """
        relative = Path(relative_path)
        if not relative.parts or relative.is_absolute():
            raise WorkspacePathError("workspace path must be relative and non-empty")
        candidate = (self._root / relative).resolve(strict=False)
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise WorkspacePathError("workspace path escapes controlled root")
        return candidate

    def initialize(self) -> WorkspaceManifest:
        """Create or validate workspace structure and recover a corrupt manifest.

        Returns:
            Valid active workspace manifest.

        Raises:
            WorkspaceVersionError: If the format or upstream source needs migration.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        with self._locked():
            manifest_path = self.resolve_path(self.MANIFEST_NAME)
            manifest: WorkspaceManifest | None = None
            recovered_from = ""

            if manifest_path.is_file():
                payload: object = None
                try:
                    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    payload = None

                if isinstance(payload, dict):
                    raw_version = payload.get("format_version")
                    if type(raw_version) is int and raw_version != _FORMAT_VERSION:
                        raise WorkspaceVersionError(
                            f"unsupported OpenViking workspace version: {raw_version}"
                        )
                    upstream_tag = payload.get("upstream_tag")
                    upstream_commit = payload.get("upstream_commit")
                    complete_source = isinstance(upstream_tag, str) and isinstance(
                        upstream_commit, str
                    )
                    if (
                        raw_version == _FORMAT_VERSION
                        and complete_source
                        and (
                            upstream_tag != UPSTREAM_TAG
                            or upstream_commit != UPSTREAM_COMMIT
                        )
                    ):
                        raise WorkspaceVersionError(
                            "OpenViking workspace source version requires migration"
                        )
                    created_at = payload.get("created_at")
                    valid_created_at = False
                    if isinstance(created_at, str) and created_at:
                        try:
                            datetime.fromisoformat(created_at)
                            valid_created_at = True
                        except ValueError:
                            pass
                    if (
                        raw_version == _FORMAT_VERSION
                        and complete_source
                        and valid_created_at
                    ):
                        manifest = WorkspaceManifest(
                            format_version=raw_version,
                            upstream_tag=UPSTREAM_TAG,
                            upstream_commit=UPSTREAM_COMMIT,
                            created_at=created_at,
                            recovered_from=str(payload.get("recovered_from") or ""),
                        )

                if manifest is None:
                    recovered_from = (
                        f"{self.MANIFEST_NAME}.corrupt-"
                        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
                    )
                    os.replace(manifest_path, self.resolve_path(recovered_from))

            if manifest is None:
                manifest = WorkspaceManifest(
                    format_version=_FORMAT_VERSION,
                    upstream_tag=UPSTREAM_TAG,
                    upstream_commit=UPSTREAM_COMMIT,
                    created_at=datetime.now(UTC).isoformat(),
                    recovered_from=recovered_from,
                )
                payload = json.dumps(
                    manifest.to_dict(), ensure_ascii=True, indent=2, sort_keys=True
                )
                self._write_bytes_unlocked(manifest_path, f"{payload}\n".encode())

            self.resolve_path("sessions").mkdir(parents=True, exist_ok=True)
            self.resolve_path("memories").mkdir(parents=True, exist_ok=True)
            return manifest

    def atomic_write(self, relative_path: str | Path, data: str | bytes) -> Path:
        """Atomically replace one file while holding the workspace lock.

        Args:
            relative_path: Destination path relative to the workspace root.
            data: UTF-8 text or raw bytes to persist.

        Returns:
            Absolute destination path.

        Raises:
            WorkspacePathError: If the destination escapes the workspace.
            TypeError: If ``data`` is not text or bytes.
            TimeoutError: If another process keeps the workspace lock too long.
        """
        if isinstance(data, str):
            payload = data.encode("utf-8")
        elif isinstance(data, bytes):
            payload = data
        else:
            raise TypeError("workspace data must be str or bytes")

        destination = self.resolve_path(relative_path)
        with self._locked():
            self._write_bytes_unlocked(destination, payload)
        return destination

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Read one validated workspace file.

        Args:
            relative_path: Source path relative to the workspace root.

        Returns:
            Complete file content.

        Raises:
            WorkspacePathError: If the source escapes the workspace.
            OSError: If the file cannot be read.
        """
        return self.resolve_path(relative_path).read_bytes()

    @contextmanager
    def transaction(self) -> Iterator[WorkspaceTransaction]:
        """Hold the cross-instance lock for a related set of file operations.

        Yields:
            Restricted transaction view using the same path and atomic-write rules.

        Raises:
            TimeoutError: If another process keeps the workspace lock too long.
        """
        with self._locked():
            yield WorkspaceTransaction(self)

    def _write_bytes_unlocked(self, destination: Path, payload: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = self.resolve_path(destination.relative_to(self._root))
        temp_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(
                temp_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._root.mkdir(parents=True, exist_ok=True)
        lock_path = self.resolve_path(self.LOCK_NAME)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        lock_token = uuid.uuid4().hex

        with self._process_lock:
            while True:
                try:
                    descriptor = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                        0o600,
                    )
                    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                        stream.write(f"pid={os.getpid()} token={lock_token}\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    break
                except FileExistsError:
                    try:
                        stale = time.time() - lock_path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if stale > _STALE_LOCK_SECONDS:
                        try:
                            lock_path.unlink()
                        except FileNotFoundError:
                            pass
                        continue
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "OpenViking workspace lock timed out"
                        ) from None
                    time.sleep(_LOCK_POLL_SECONDS)

            try:
                yield
            finally:
                try:
                    active_lock = lock_path.read_text(encoding="ascii")
                except (FileNotFoundError, OSError, UnicodeDecodeError):
                    active_lock = ""
                if f"token={lock_token}" in active_lock:
                    lock_path.unlink(missing_ok=True)
