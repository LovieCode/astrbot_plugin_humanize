from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingWorkspace,
    WorkspacePathError,
)
from astrbot_plugin_humanize.humanize.openviking.workspace import WorkspaceVersionError
from astrbot_plugin_humanize.humanize.vendor.openviking_core import (
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
)


def test_workspace_initializes_once_with_pinned_manifest(tmp_path: Path) -> None:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")

    created = workspace.initialize()
    loaded = workspace.initialize()
    payload = json.loads(
        (workspace.root / workspace.MANIFEST_NAME).read_text(encoding="utf-8")
    )

    assert created == loaded
    assert payload["format_version"] == 1
    assert payload["upstream_tag"] == UPSTREAM_TAG
    assert payload["upstream_commit"] == UPSTREAM_COMMIT
    assert (workspace.root / "sessions").is_dir()
    assert (workspace.root / "memories").is_dir()


def test_workspace_atomic_write_rejects_path_escape(tmp_path: Path) -> None:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    workspace.initialize()

    destination = workspace.atomic_write("sessions/demo/messages.jsonl", "hello\n")

    assert destination.read_text(encoding="utf-8") == "hello\n"
    assert workspace.read_bytes("sessions/demo/messages.jsonl") == b"hello\n"
    with pytest.raises(WorkspacePathError):
        workspace.atomic_write("../outside.txt", b"blocked")
    with pytest.raises(WorkspacePathError):
        workspace.atomic_write((tmp_path / "absolute.txt").resolve(), b"blocked")
    assert not (tmp_path / "outside.txt").exists()
    assert not (tmp_path / "absolute.txt").exists()


@pytest.mark.parametrize("corrupt_content", ["{broken", '{"format_version": 1}'])
def test_workspace_recovers_corrupt_manifest_without_deleting_it(
    tmp_path: Path,
    corrupt_content: str,
) -> None:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    workspace.root.mkdir(parents=True)
    manifest_path = workspace.root / workspace.MANIFEST_NAME
    manifest_path.write_text(corrupt_content, encoding="utf-8")

    manifest = workspace.initialize()
    backups = list(workspace.root.glob(f"{workspace.MANIFEST_NAME}.corrupt-*"))

    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt_content
    assert manifest.recovered_from == backups[0].name
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["format_version"] == 1


def test_workspace_does_not_overwrite_unknown_version(tmp_path: Path) -> None:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    workspace.root.mkdir(parents=True)
    manifest_path = workspace.root / workspace.MANIFEST_NAME
    original = json.dumps(
        {
            "format_version": 99,
            "upstream_tag": UPSTREAM_TAG,
            "upstream_commit": UPSTREAM_COMMIT,
            "created_at": "2026-07-17T00:00:00+00:00",
        }
    )
    manifest_path.write_text(original, encoding="utf-8")

    with pytest.raises(WorkspaceVersionError, match="unsupported"):
        workspace.initialize()

    assert manifest_path.read_text(encoding="utf-8") == original


def test_workspace_does_not_overwrite_different_upstream_source(
    tmp_path: Path,
) -> None:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    workspace.root.mkdir(parents=True)
    manifest_path = workspace.root / workspace.MANIFEST_NAME
    original = json.dumps(
        {
            "format_version": 1,
            "upstream_tag": "v0.4.8",
            "upstream_commit": "older",
            "created_at": "2026-07-17T00:00:00+00:00",
        }
    )
    manifest_path.write_text(original, encoding="utf-8")

    with pytest.raises(WorkspaceVersionError, match="source version"):
        workspace.initialize()

    assert manifest_path.read_text(encoding="utf-8") == original


def test_workspace_serializes_concurrent_atomic_writes(tmp_path: Path) -> None:
    first = OpenVikingWorkspace(tmp_path / "plugin-data")
    second = OpenVikingWorkspace(tmp_path / "plugin-data")
    first.initialize()
    payloads = [f"payload-{index}-".encode() * 4096 for index in range(8)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda item: item[0].atomic_write("memories/shared.md", item[1]),
                [
                    (first if index % 2 == 0 else second, payload)
                    for index, payload in enumerate(payloads)
                ],
            )
        )

    assert first.read_bytes("memories/shared.md") in payloads
    assert not list(first.root.rglob("*.tmp"))
    assert not (first.root / first.LOCK_NAME).exists()


def test_workspace_transaction_serializes_related_read_modify_write(
    tmp_path: Path,
) -> None:
    first = OpenVikingWorkspace(tmp_path / "plugin-data")
    second = OpenVikingWorkspace(tmp_path / "plugin-data")
    first.initialize()
    first.atomic_write("sessions/counter.txt", "0")

    def increment(workspace: OpenVikingWorkspace) -> None:
        with workspace.transaction() as transaction:
            current = int(transaction.read_bytes("sessions/counter.txt"))
            transaction.atomic_write("sessions/counter.txt", str(current + 1))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                increment,
                [first if index % 2 == 0 else second for index in range(12)],
            )
        )

    assert first.read_bytes("sessions/counter.txt") == b"12"
