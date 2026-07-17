from __future__ import annotations

import copy
import json
from pathlib import Path

from humanize.openviking import (
    OpenVikingMemoryAdapter,
    OpenVikingMigrationAdapter,
    OpenVikingWorkspace,
)


def _legacy_record() -> dict[str, object]:
    return {
        "id": 7,
        "scope_type": "private_user",
        "scope_hash": "b" * 64,
        "subject_hash": "c" * 64,
        "agent_id": "default",
        "memory_type": "preference",
        "memory_key": "preference:tea",
        "canonical_text": "用户喜欢无糖乌龙茶",
        "structured_value": {"like": "无糖乌龙茶"},
        "status": "active",
        "confidence": 0.9,
        "importance": 0.8,
        "valid_from": "",
        "valid_until": "",
        "revision": 2,
        "created_at": "2026-07-16T00:00:00+00:00",
        "updated_at": "2026-07-17T00:00:00+00:00",
        "aliases": ["乌龙茶", "无糖茶"],
        "evidence": [
            {
                "request_id": "raw-request-must-not-migrate",
                "excerpt": "我喜欢无糖乌龙茶",
                "observed_at": "2026-07-16T00:00:00+00:00",
                "source_complete": True,
            },
            {
                "request_id": "raw-request-two",
                "excerpt": "一直都喝无糖的",
                "observed_at": "2026-07-17T00:00:00+00:00",
                "source_complete": True,
            },
        ],
        "revisions": [
            {
                "revision": 2,
                "action": "activate",
                "actor": "web_admin",
                "reason": "人工确认",
                "snapshot": {
                    "memory_key": "preference:tea",
                    "canonical_text": "用户喜欢无糖乌龙茶",
                    "status": "active",
                    "confidence": 0.9,
                },
                "created_at": "2026-07-17T00:00:00+00:00",
            },
            {
                "revision": 1,
                "action": "create",
                "actor": "memory_extractor",
                "reason": "规则提取",
                "snapshot": {
                    "memory_key": "preference:tea",
                    "canonical_text": "用户喜欢无糖乌龙茶",
                    "status": "candidate",
                    "confidence": 0.9,
                },
                "created_at": "2026-07-16T00:00:00+00:00",
            },
        ],
    }


def _migration(
    tmp_path: Path,
) -> tuple[OpenVikingMigrationAdapter, OpenVikingWorkspace]:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    adapter = OpenVikingMemoryAdapter(workspace)
    adapter.initialize()
    return OpenVikingMigrationAdapter(adapter, workspace), workspace


def test_migration_dry_run_validates_without_writing_memory(tmp_path: Path) -> None:
    migration, workspace = _migration(tmp_path)

    result = migration.migrate([_legacy_record()], dry_run=True)

    assert result.total == 1
    assert result.validated == 1
    assert result.verified == 1
    assert not list((workspace.root / "memories").rglob("*.md"))
    assert not list((workspace.root / "sessions").rglob("*.json"))


def test_migration_writes_history_manifest_and_is_idempotent(tmp_path: Path) -> None:
    migration, workspace = _migration(tmp_path)

    first = migration.migrate([_legacy_record()])
    retried = migration.migrate([_legacy_record()])

    manifest_path = next((workspace.root / "memories").rglob("manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    memory_path = next((workspace.root / "memories").rglob("*.md"))
    raw_memory = memory_path.read_text(encoding="utf-8")
    workspace_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in workspace.root.rglob("*")
        if path.is_file() and path.name != ".workspace.lock"
    )

    assert first.migrated == 1
    assert first.verified == 1
    assert retried.duplicates == 1
    assert retried.items[0].version == first.items[0].version == 1
    assert manifest["evidence_count"] == 2
    assert manifest["revision_count"] == 2
    assert manifest["aliases"] == ["乌龙茶", "无糖茶"]
    assert '"link_type": "evolved_from"' in raw_memory
    assert '"migration_source"' in raw_memory
    assert "raw-request-must-not-migrate" not in workspace_text
    assert "raw-request-two" not in workspace_text


def test_migration_repairs_missing_history_page_without_new_version(
    tmp_path: Path,
) -> None:
    migration, workspace = _migration(tmp_path)
    first = migration.migrate([_legacy_record()])
    manifest_path = next((workspace.root / "memories").rglob("manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing_page = workspace.root / manifest["evidence_pages"][0]
    missing_page.unlink()

    repaired = migration.migrate([_legacy_record()])

    assert repaired.migrated == 1
    assert repaired.items[0].version == first.items[0].version == 1
    assert missing_page.is_file()


def test_migration_repairs_tampered_history_pages_without_new_version(
    tmp_path: Path,
) -> None:
    migration, workspace = _migration(tmp_path)
    first = migration.migrate([_legacy_record()])
    manifest_path = next((workspace.root / "memories").rglob("manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence_page = workspace.root / manifest["evidence_pages"][0]
    revision_page = workspace.root / manifest["revision_pages"][0]
    evidence = json.loads(evidence_page.read_text(encoding="utf-8"))
    revision = json.loads(revision_page.read_text(encoding="utf-8"))
    evidence["quote"] = "tampered evidence"
    revision["reason"] = "tampered revision"
    evidence_page.write_text(json.dumps(evidence), encoding="utf-8")
    revision_page.write_text(json.dumps(revision), encoding="utf-8")

    repaired = migration.migrate([_legacy_record()])

    assert repaired.migrated == 1
    assert repaired.items[0].version == first.items[0].version == 1
    assert "tampered evidence" not in evidence_page.read_text(encoding="utf-8")
    assert "tampered revision" not in revision_page.read_text(encoding="utf-8")


def test_migration_force_replaces_changed_current_snapshot(tmp_path: Path) -> None:
    migration, workspace = _migration(tmp_path)
    migration.migrate([_legacy_record()])
    updated = copy.deepcopy(_legacy_record())
    updated["canonical_text"] = "用户现在喜欢黑咖啡"
    updated["structured_value"] = {"like": "黑咖啡"}
    updated["confidence"] = 0.2
    updated["revision"] = 3
    revisions = list(updated["revisions"])
    revisions.append(
        {
            "revision": 3,
            "action": "update",
            "actor": "web_admin",
            "reason": "当前快照",
            "snapshot": {
                "memory_key": "preference:tea",
                "canonical_text": "用户现在喜欢黑咖啡",
                "status": "active",
                "confidence": 0.2,
            },
            "created_at": "2026-07-18T00:00:00+00:00",
        }
    )
    updated["revisions"] = revisions
    updated["updated_at"] = "2026-07-18T00:00:00+00:00"

    result = migration.migrate([updated])

    raw_memory = next((workspace.root / "memories").rglob("*.md")).read_text(
        encoding="utf-8"
    )
    assert result.migrated == 1
    assert result.items[0].version == 2
    assert "用户现在喜欢黑咖啡" in raw_memory
    assert '"confidence": 0.2' in raw_memory
    assert '"source_revision": 3' in raw_memory


def test_migration_reports_invalid_identity_without_partial_files(
    tmp_path: Path,
) -> None:
    migration, workspace = _migration(tmp_path)
    invalid = _legacy_record()
    invalid["scope_hash"] = "../raw-user"

    result = migration.migrate([invalid])

    assert result.failed == 1
    assert result.verified == 0
    assert result.items[0].error == "ValueError"
    assert not list((workspace.root / "memories").rglob("*.md"))

