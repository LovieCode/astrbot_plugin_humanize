from __future__ import annotations

import json
from pathlib import Path

import pytest
from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingManagementAdapter,
    OpenVikingMemoryAdapter,
    OpenVikingWorkspace,
)


def _management(
    tmp_path: Path,
) -> tuple[OpenVikingManagementAdapter, OpenVikingWorkspace]:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    memory = OpenVikingMemoryAdapter(workspace)
    memory.initialize()
    return OpenVikingManagementAdapter(memory, workspace), workspace


def _create_payload() -> dict[str, object]:
    return {
        "action": "create",
        "agent_id": "default",
        "confidence": 0.9,
        "content": "用户喜欢无糖乌龙茶",
        "importance": 0.8,
        "memory_key": "preference:tea",
        "scope_hash": "b" * 64,
        "scope_type": "private_user",
        "status": "active",
        "structured_value": {"like": "无糖乌龙茶"},
        "subject_hash": "c" * 64,
        "type": "preference",
    }


def test_management_create_list_detail_and_overview(tmp_path: Path) -> None:
    management, _ = _management(tmp_path)

    created = management.apply_memory_action(_create_payload())
    listing = management.list_memories(
        scope_type="private_user",
        scope_hash="b" * 64,
        status="active",
        search="乌龙茶",
        page=1,
        page_size=20,
    )
    detail = management.get_memory_detail(str(created["id"]))
    overview = management.get_overview()

    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]
    assert "_path" not in listing["items"][0]
    assert "evidence" not in listing["items"][0]
    assert detail is not None
    assert detail["content"] == "用户喜欢无糖乌龙茶"
    assert detail["evidence"][0]["quote"] == "用户喜欢无糖乌龙茶"
    assert detail["audit"][0]["action"] == "create"
    assert overview["memories"]["by_status"]["active"] == 1
    assert overview["memories"]["total"] == 1


def test_management_update_and_reject_preserve_memory_file(tmp_path: Path) -> None:
    management, workspace = _management(tmp_path)
    created = management.apply_memory_action(_create_payload())

    updated = management.apply_memory_action(
        {
            "action": "update",
            "id": created["id"],
            "revision": created["version"],
            "content": "用户现在喜欢黑咖啡",
            "confidence": 0.7,
            "importance": 0.6,
            "reason": "人工修正",
        }
    )
    rejected = management.apply_memory_action(
        {
            "action": "reject",
            "id": updated["id"],
            "revision": updated["version"],
            "reason": "不再有效",
        }
    )

    assert updated["content"] == "用户现在喜欢黑咖啡"
    assert rejected["status"] == "rejected"
    assert len(list((workspace.root / "memories").rglob("*.md"))) == 1
    assert management.list_memories(status="active")["total"] == 0
    assert management.list_memories(status="rejected")["total"] == 1


def test_management_audit_is_content_free_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    management, workspace = _management(tmp_path)
    payload = _create_payload()

    first = management.apply_memory_action(payload)
    retried = management.apply_memory_action(payload)
    audit_paths = list((workspace.root / "memory_admin").glob("*.json"))
    audit_text = audit_paths[0].read_text(encoding="utf-8")
    audit = json.loads(audit_text)

    assert retried["id"] == first["id"]
    assert retried["version"] == first["version"]
    assert len(audit_paths) == 1
    assert "用户喜欢无糖乌龙茶" not in audit_text
    assert audit["after_hash"]
    assert audit["before_hash"]


def test_management_rejects_revision_conflict_and_identity_change(
    tmp_path: Path,
) -> None:
    management, _ = _management(tmp_path)
    created = management.apply_memory_action(_create_payload())

    with pytest.raises(ValueError, match="revision conflict"):
        management.apply_memory_action(
            {"action": "update", "id": created["id"], "revision": 999}
        )
    with pytest.raises(ValueError, match="identity is immutable"):
        management.apply_memory_action(
            {
                "action": "update",
                "id": created["id"],
                "revision": created["version"],
                "memory_key": "preference:coffee",
            }
        )


def test_management_reject_without_agent_id_uses_existing_identity(
    tmp_path: Path,
) -> None:
    """WebUI reject 请求只传 action/id/revision/reason，agent_id 应从
    existing 记忆补全，而不是 fallback 成 default 导致身份不匹配报错。"""
    management, _ = _management(tmp_path)
    payload = _create_payload()
    payload["agent_id"] = "agent-" + "a" * 63
    created = management.apply_memory_action(payload)

    # 模拟前端 reject：不传 agent_id / scope_hash / subject_hash 等身份字段
    rejected = management.apply_memory_action(
        {
            "action": "reject",
            "id": created["id"],
            "revision": created["version"],
            "reason": "后台拒绝",
        }
    )

    assert rejected["status"] == "rejected"
    assert rejected["agent_id"] == "agent-" + "a" * 63
    assert management.list_memories(status="rejected")["total"] == 1
