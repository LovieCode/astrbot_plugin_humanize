from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from humanize.openviking import OpenVikingMemoryAdapter, OpenVikingWorkspace


def _payload(*, commit: str = "a", action: str = "Reply") -> dict[str, object]:
    return {
        "idempotency_key": commit * 64,
        "agent_id": "default",
        "scope_type": "private_user",
        "scope_hash": "b" * 64,
        "subject_hash": "c" * 64,
        "conversation_hash": "d" * 64,
        "user_text": "我喜欢无糖乌龙茶",
        "assistant_messages": ["记住了"] if action == "Reply" else [],
        "action": action,
        "occurred_at": "2026-07-17T00:00:00+00:00",
        "source_complete": True,
    }


def _adapter(tmp_path: Path) -> OpenVikingMemoryAdapter:
    adapter = OpenVikingMemoryAdapter(OpenVikingWorkspace(tmp_path / "plugin-data"))
    adapter.initialize()
    return adapter


def _candidate(
    *, content: str = "用户喜欢无糖乌龙茶", confidence: float = 0.9
) -> dict[str, object]:
    return {
        "agent_id": "default",
        "scope_type": "private_user",
        "scope_hash": "b" * 64,
        "subject_hash": "c" * 64,
        "conversation_hash": "d" * 64,
        "memory_type": "preference",
        "memory_key": "preference:tea",
        "content": content,
        "structured_value": {"like": "无糖乌龙茶"},
        "confidence": confidence,
        "importance": 0.8,
        "status": "active",
        "occurred_at": "2026-07-17T00:00:00+00:00",
        "valid_until": "",
    }


def test_session_commit_archives_l0_l1_l2_and_anonymized_identity(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)

    result = adapter.commit_turn(_payload())
    workspace_root = tmp_path / "plugin-data" / "openviking"
    session_root = (
        workspace_root
        / "sessions"
        / "default"
        / "private_user"
        / ("b" * 64)
        / ("d" * 64)
    )
    message_records = [
        json.loads(line)
        for line in (session_root / "messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    commit = json.loads(
        (session_root / "commits" / f"{'a' * 64}.json").read_text(encoding="utf-8")
    )
    meta = json.loads((session_root / ".meta.json").read_text(encoding="utf-8"))

    assert result.duplicate is False
    assert result.message_count == 2
    assert result.commit_count == 1
    assert [record["role"] for record in message_records] == ["user", "assistant"]
    assert message_records[0]["peer_id"] == "c" * 64
    assert commit["l0"] == "我喜欢无糖乌龙茶"
    assert "Assistant: 记住了" in commit["l1"]
    assert commit["l2_uri"].endswith("/messages.jsonl")
    assert meta["scope_hash"] == "b" * 64
    assert "sender_id" not in json.dumps(meta)


def test_session_commit_retry_is_idempotent(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    first = adapter.commit_turn(_payload())
    retried = adapter.commit_turn(_payload())

    assert first.duplicate is False
    assert retried.duplicate is True
    assert retried.message_count == 2
    assert retried.commit_count == 1


def test_session_commit_serializes_concurrent_turns(tmp_path: Path) -> None:
    first = _adapter(tmp_path)
    second = OpenVikingMemoryAdapter(OpenVikingWorkspace(tmp_path / "plugin-data"))
    second.initialize()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: item[0].commit_turn(item[1]),
                [(first, _payload(commit="a")), (second, _payload(commit="e"))],
            )
        )

    assert sorted(result.commit_count for result in results) == [1, 2]
    latest = max(results, key=lambda result: result.commit_count)
    session_root = (
        tmp_path
        / "plugin-data"
        / "openviking"
        / "sessions"
        / "default"
        / "private_user"
        / ("b" * 64)
        / ("d" * 64)
    )
    assert (
        len((session_root / "messages.jsonl").read_text(encoding="utf-8").splitlines())
        == 4
    )
    assert latest.message_count == 4
    assert (
        json.loads((session_root / ".meta.json").read_text(encoding="utf-8"))[
            "commit_count"
        ]
        == 2
    )


def test_no_reply_commit_archives_only_user_experience(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    result = adapter.commit_turn(_payload(action="No Reply"))

    assert result.message_count == 1


def test_session_commit_rejects_unhashed_or_path_like_identity(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    invalid_hash = _payload()
    invalid_hash["scope_hash"] = "../raw-user"
    invalid_agent = _payload()
    invalid_agent["agent_id"] = "../agent"

    with pytest.raises(ValueError, match="scope hash"):
        adapter.commit_turn(invalid_hash)
    with pytest.raises(ValueError, match="agent_id"):
        adapter.commit_turn(invalid_agent)
    assert not (tmp_path / "raw-user").exists()


def test_memory_upsert_creates_levels_diff_and_session_link(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload())

    result = adapter.upsert_memory(
        _candidate(),
        evidence=[
            {
                "quote": "我喜欢无糖乌龙茶",
                "occurred_at": "2026-07-17T00:00:00+00:00",
                "source_complete": True,
            }
        ],
        source_commit_ids=("a" * 64,),
    )
    memory_path = next(
        (tmp_path / "plugin-data" / "openviking" / "memories").rglob("*.md")
    )
    raw_memory = memory_path.read_text(encoding="utf-8")
    diff = json.loads(
        (
            tmp_path
            / "plugin-data"
            / "openviking"
            / "memory_diffs"
            / f"{result.operation_id}.json"
        ).read_text(encoding="utf-8")
    )

    assert result.operation == "create"
    assert result.version == 1
    assert '"abstract": "用户喜欢无糖乌龙茶"' in raw_memory
    assert '"overview"' in raw_memory
    assert '"link_type": "derived_from"' in raw_memory
    assert diff["before"] == ""
    assert diff["after"] == "用户喜欢无糖乌龙茶"
    assert diff["source_commit_ids"] == ["a" * 64]


def test_memory_upsert_replace_and_retry_are_idempotent(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload(commit="a"))
    adapter.commit_turn(_payload(commit="e"))
    adapter.upsert_memory(
        _candidate(),
        evidence=[],
        source_commit_ids=("a" * 64,),
    )

    replacement = adapter.upsert_memory(
        _candidate(content="用户现在喜欢黑咖啡", confidence=0.95),
        evidence=[],
        source_commit_ids=("e" * 64,),
    )
    retried = adapter.upsert_memory(
        _candidate(content="用户现在喜欢黑咖啡", confidence=0.95),
        evidence=[],
        source_commit_ids=("e" * 64,),
    )

    assert replacement.operation == "replace"
    assert replacement.version == 2
    assert retried.duplicate is True
    assert retried.version == 2
    memory_path = next(
        (tmp_path / "plugin-data" / "openviking" / "memories").rglob("*.md")
    )
    assert "用户现在喜欢黑咖啡" in memory_path.read_text(encoding="utf-8")


def test_lower_confidence_memory_keeps_content_but_records_diff(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload(commit="a"))
    adapter.commit_turn(_payload(commit="e"))
    adapter.upsert_memory(
        _candidate(confidence=0.9),
        evidence=[],
        source_commit_ids=("a" * 64,),
    )

    low_confidence = _candidate(content="低置信度冲突", confidence=0.2)
    low_confidence.update(
        {
            "abstract": "错误摘要",
            "importance": 0.1,
            "overview": "错误概览",
            "status": "candidate",
            "structured_value": {"like": "低置信度冲突"},
            "valid_until": "2026-07-18T00:00:00+00:00",
        }
    )
    kept = adapter.upsert_memory(
        low_confidence,
        evidence=[],
        source_commit_ids=("e" * 64,),
    )

    assert kept.operation == "keep"
    assert kept.version == 2
    memory_path = next(
        (tmp_path / "plugin-data" / "openviking" / "memories").rglob("*.md")
    )
    content = memory_path.read_text(encoding="utf-8")
    assert "用户喜欢无糖乌龙茶" in content
    assert "低置信度冲突" not in content
    assert '"confidence": 0.9' in content
    assert '"importance": 0.8' in content
    assert '"status": "active"' in content
    assert '"like": "无糖乌龙茶"' in content
    assert '"valid_until": ""' in content
    assert "错误摘要" not in content
    assert "错误概览" not in content


def test_memory_upsert_recovers_missing_diff_without_new_version(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload())
    first = adapter.upsert_memory(
        _candidate(),
        evidence=[],
        source_commit_ids=("a" * 64,),
    )
    diff_path = (
        tmp_path
        / "plugin-data"
        / "openviking"
        / "memory_diffs"
        / f"{first.operation_id}.json"
    )
    diff_path.unlink()

    recovered = adapter.upsert_memory(
        _candidate(),
        evidence=[],
        source_commit_ids=("a" * 64,),
    )
    restored_diff = json.loads(diff_path.read_text(encoding="utf-8"))

    assert recovered.duplicate is True
    assert recovered.operation == "create"
    assert recovered.version == first.version == 1
    assert restored_diff["operation_id"] == first.operation_id
    assert restored_diff["version"] == 1


def test_memory_upsert_rejects_missing_source_commit(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    with pytest.raises(ValueError, match="source commit does not exist"):
        adapter.upsert_memory(
            _candidate(),
            evidence=[],
            source_commit_ids=("a" * 64,),
        )


def test_trusted_migration_can_replace_higher_confidence_memory(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload(commit="a"))
    adapter.commit_turn(_payload(commit="e"))
    adapter.upsert_memory(
        _candidate(confidence=0.9),
        evidence=[],
        source_commit_ids=("a" * 64,),
    )

    replaced = adapter.upsert_memory(
        _candidate(content="迁移后的当前快照", confidence=0.2),
        evidence=[],
        source_commit_ids=("e" * 64,),
        force_replace=True,
    )

    memory_path = next(
        (tmp_path / "plugin-data" / "openviking" / "memories").rglob("*.md")
    )
    content = memory_path.read_text(encoding="utf-8")
    assert replaced.operation == "replace"
    assert "迁移后的当前快照" in content
    assert '"confidence": 0.2' in content
