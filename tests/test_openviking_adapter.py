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
