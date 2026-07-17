from __future__ import annotations

from pathlib import Path

import pytest
from humanize.openviking import (
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)


def _payload(
    *,
    commit: str = "a",
    agent_id: str = "default",
    scope_hash: str = "b",
    subject_hash: str = "c",
    conversation_hash: str = "d",
) -> dict[str, object]:
    return {
        "idempotency_key": commit * 64,
        "agent_id": agent_id,
        "scope_type": "private_user",
        "scope_hash": scope_hash * 64,
        "subject_hash": subject_hash * 64,
        "conversation_hash": conversation_hash * 64,
        "user_text": "我喜欢无糖乌龙茶",
        "assistant_messages": ["记住了"],
        "action": "Reply",
        "occurred_at": "2026-07-17T00:00:00+00:00",
        "source_complete": True,
    }


def _candidate(
    *,
    memory_key: str,
    content: str,
    status: str = "active",
    agent_id: str = "default",
    scope_hash: str = "b",
    subject_hash: str = "c",
    conversation_hash: str = "d",
    valid_until: str = "",
) -> dict[str, object]:
    return {
        "abstract": "茶偏好",
        "agent_id": agent_id,
        "confidence": 0.9,
        "content": content,
        "conversation_hash": conversation_hash * 64,
        "importance": 0.8,
        "memory_key": memory_key,
        "memory_type": "preference",
        "occurred_at": "2026-07-17T00:00:00+00:00",
        "overview": "用户的饮品偏好",
        "scope_hash": scope_hash * 64,
        "scope_type": "private_user",
        "status": status,
        "structured_value": {"like": "无糖乌龙茶"},
        "subject_hash": subject_hash * 64,
        "valid_until": valid_until,
    }


def _adapter(tmp_path: Path) -> tuple[OpenVikingMemoryAdapter, OpenVikingWorkspace]:
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    adapter = OpenVikingMemoryAdapter(workspace)
    adapter.initialize()
    return adapter, workspace


@pytest.mark.asyncio
async def test_recall_filters_scope_agent_subject_status_and_expiry(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    records = [
        (
            _payload(commit="a"),
            _candidate(
                memory_key="preference:tea",
                content="用户喜欢<无糖乌龙茶>&清香型",
            ),
        ),
        (
            _payload(commit="e", conversation_hash="e"),
            _candidate(
                memory_key="preference:candidate",
                content="不应召回的候选记忆",
                status="candidate",
                conversation_hash="e",
            ),
        ),
        (
            _payload(commit="f", conversation_hash="f"),
            _candidate(
                memory_key="preference:expired",
                content="不应召回的过期记忆",
                conversation_hash="f",
                valid_until="2000-01-01T00:00:00+00:00",
            ),
        ),
        (
            _payload(commit="1", subject_hash="9", conversation_hash="1"),
            _candidate(
                memory_key="preference:other-subject",
                content="不应召回的其他用户记忆",
                subject_hash="9",
                conversation_hash="1",
            ),
        ),
        (
            _payload(
                commit="2",
                agent_id="other-agent",
                conversation_hash="2",
            ),
            _candidate(
                memory_key="preference:other-agent",
                content="不应召回的其他 Agent 记忆",
                agent_id="other-agent",
                conversation_hash="2",
            ),
        ),
    ]
    for payload, candidate in records:
        commit = adapter.commit_turn(payload)
        adapter.upsert_memory(
            candidate,
            evidence=[],
            source_commit_ids=(commit.commit_id,),
        )

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="茶偏好",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.item_count == 1
    assert result.candidate_count == 1
    assert result.source_refs[0].startswith("viking://agent/default/memories/")
    assert "&lt;无糖乌龙茶&gt;&amp;清香型" in result.content
    assert "不应召回" not in result.content


@pytest.mark.asyncio
async def test_recall_rechecks_identity_after_file_read(tmp_path: Path) -> None:
    adapter, workspace = _adapter(tmp_path)
    commit = adapter.commit_turn(_payload())
    adapter.upsert_memory(
        _candidate(memory_key="preference:tea", content="用户喜欢无糖乌龙茶"),
        evidence=[],
        source_commit_ids=(commit.commit_id,),
    )
    memory_path = next((workspace.root / "memories").rglob("*.md"))
    raw = memory_path.read_text(encoding="utf-8")
    memory_path.write_text(
        raw.replace(
            '"scope_hash": "' + "b" * 64 + '"',
            '"scope_hash": "' + "f" * 64 + '"',
        ),
        encoding="utf-8",
    )

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="茶偏好",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is False
    assert result.item_count == 0
    assert result.candidate_count == 0


@pytest.mark.asyncio
async def test_recall_degrades_when_optional_providers_fail(tmp_path: Path) -> None:
    class FailingProviders:
        embedding_enabled = True
        rerank_enabled = True

        async def embed(self, texts: tuple[str, ...]):
            del texts
            raise RuntimeError("embedding unavailable")

        async def rerank(self, query: str, documents: tuple[str, ...]):
            del query, documents
            raise RuntimeError("rerank unavailable")

    adapter, workspace = _adapter(tmp_path)
    commit = adapter.commit_turn(_payload())
    adapter.upsert_memory(
        _candidate(memory_key="preference:tea", content="用户喜欢无糖乌龙茶"),
        evidence=[],
        source_commit_ids=(commit.commit_id,),
    )

    result = await OpenVikingRecallAdapter(
        workspace,
        FailingProviders(),  # type: ignore[arg-type]
    ).recall(
        query="茶偏好",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.reason == "matched"


@pytest.mark.asyncio
async def test_recall_rejects_invalid_scope_filter(tmp_path: Path) -> None:
    _, workspace = _adapter(tmp_path)

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="茶偏好",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "../raw-user",
                "subject_hash": "c" * 64,
            },
        ),
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is False
    assert result.reason == "source_error"
