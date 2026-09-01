from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingMemoryAdapter,
    OpenVikingWorkspace,
)


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


def test_session_commit_rejects_unhashed_scope_and_hashes_unsafe_agent(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    invalid_hash = _payload()
    invalid_hash["scope_hash"] = "../raw-user"
    invalid_agent = _payload()
    invalid_agent["agent_id"] = "../agent"

    with pytest.raises(ValueError, match="scope hash"):
        adapter.commit_turn(invalid_hash)
    result = adapter.commit_turn(invalid_agent)
    assert "/../agent/" not in result.session_uri
    assert "/agent-" in result.session_uri
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
    # 旧内容保留；低置信度冲突作为反例把置信度打五折（0.9 → 0.45）。
    assert "用户喜欢无糖乌龙茶" in content
    assert '"confidence": 0.45' in content
    assert '"importance": 0.8' in content
    assert '"status": "active"' in content
    # 矛盾反证只进证据链：冲突 key 保留旧值，避免盘上自相矛盾。
    assert '"like": "无糖乌龙茶"' in content
    assert '"like": "低置信度冲突"' not in content
    assert '"valid_until": ""' in content
    assert "错误摘要" not in content
    assert "错误概览" not in content
    assert '"penalized": true' in content


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


def _write_canonical(
    tmp_path: Path, context_ref: str, turn_ref: str, payload: dict[str, object]
) -> None:
    """Write the context_l2 canonical record the adapter requires for context_ref."""
    canonical_dir = (
        tmp_path
        / "plugin-data"
        / "openviking"
        / "sessions"
        / str(payload["agent_id"])
        / str(payload["scope_type"])
        / str(payload["scope_hash"])
        / str(payload["conversation_hash"])
        / "context_l2"
    )
    canonical_dir.mkdir(parents=True, exist_ok=True)
    (canonical_dir / f"{context_ref}.json").write_text(
        json.dumps(
            {
                "action": payload["action"],
                "context_ref": context_ref,
                "created_at": payload["occurred_at"],
                "l0": " ".join(str(payload["user_text"]).split())[:160],
                "messages": [
                    {"role": "user", "content": payload["user_text"]},
                    {
                        "role": "assistant",
                        "content": payload["assistant_messages"][0],
                    },
                ],
                "source_complete": True,
                "turn_ref": turn_ref,
                "version": 1,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_commit_canonical_then_extraction_resubmit_is_idempotent(
    tmp_path: Path,
) -> None:
    """The canonical path commits with context_ref; the extraction path re-submits
    the same turn without it. The conflict must resolve as idempotent instead of
    failing the extraction job."""
    adapter = _adapter(tmp_path)
    payload = _payload()
    payload["context_ref"] = "ctx-ABCD2345"
    _write_canonical(tmp_path, "ctx-ABCD2345", "a" * 64, payload)
    canonical = adapter.commit_turn(dict(payload))

    payload.pop("context_ref")
    retried = adapter.commit_turn(payload)

    assert canonical.duplicate is False
    assert retried.duplicate is True
    assert retried.commit_count == 1
    conflicts = list(
        (tmp_path / "plugin-data" / "openviking" / "memory_admin" / "conflicts").glob(
            "*.json"
        )
    )
    assert len(conflicts) == 1
    record = json.loads(conflicts[0].read_text(encoding="utf-8"))
    assert record["resolved"] == "idempotent"
    assert record["commit_id"] == "a" * 64


def test_commit_extraction_then_canonical_resubmit_is_idempotent(
    tmp_path: Path,
) -> None:
    """The reverse ordering (extraction path first, canonical path second) must
    also stay idempotent while keeping the richer canonical reference."""
    adapter = _adapter(tmp_path)
    payload = _payload()
    first = adapter.commit_turn(dict(payload))

    payload["context_ref"] = "ctx-ABCD2345"
    _write_canonical(tmp_path, "ctx-ABCD2345", "a" * 64, payload)
    retried = adapter.commit_turn(payload)

    assert first.duplicate is False
    assert retried.duplicate is True
    assert retried.commit_count == 1
    conflicts = list(
        (tmp_path / "plugin-data" / "openviking" / "memory_admin" / "conflicts").glob(
            "*.json"
        )
    )
    assert len(conflicts) == 1
    record = json.loads(conflicts[0].read_text(encoding="utf-8"))
    assert record["resolved"] == "idempotent"


def test_commit_genuine_conflict_is_rejected_and_recorded(tmp_path: Path) -> None:
    """A real collision (same commit id, different turn content) must still fail
    loudly and leave an observability record."""
    adapter = _adapter(tmp_path)
    adapter.commit_turn(_payload())

    conflicting = _payload()
    conflicting["user_text"] = "完全不同的一句话"
    with pytest.raises(RuntimeError, match="conflicting content"):
        adapter.commit_turn(conflicting)

    conflicts = list(
        (tmp_path / "plugin-data" / "openviking" / "memory_admin" / "conflicts").glob(
            "*.json"
        )
    )
    assert len(conflicts) == 1
    record = json.loads(conflicts[0].read_text(encoding="utf-8"))
    assert record["resolved"] == "rejected"
    assert record["existing"]["l0"] != record["incoming"]["l0"]
