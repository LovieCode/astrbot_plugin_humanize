from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_humanize.humanize.openviking import (
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
    valid_from: str = "",
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
        "valid_from": valid_from,
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
            _payload(commit="3", conversation_hash="3"),
            _candidate(
                memory_key="preference:future",
                content="不应召回的未来记忆",
                conversation_hash="3",
                valid_from="2999-01-01T00:00:00+00:00",
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
    assert 'type="preference"' in result.content
    assert "&lt;无糖乌龙茶&gt;&amp;清香型" in result.content
    assert "viking://" not in result.content
    assert re.search(r"[0-9a-f]{64}", result.content) is None
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


@pytest.mark.asyncio
async def test_recall_falls_back_to_exact_session_when_no_memory_exists(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    commit = adapter.commit_turn(_payload())

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="下一步怎么安排",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        conversation_hash="d" * 64,
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.item_count == 1
    assert result.candidate_count == 1
    assert result.source_refs == (
        "viking://agent/default/sessions/private_user/"
        f"{'b' * 64}/{'d' * 64}/commits/{commit.commit_id}",
    )
    assert 'type="session"' in result.content
    assert "User: 我喜欢无糖乌龙茶" in result.content
    assert "viking://" not in result.content
    assert re.search(r"[0-9a-f]{64}", result.content) is None


@pytest.mark.asyncio
async def test_session_fallback_ignores_semantic_recall_threshold(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    adapter.commit_turn(_payload())

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="下一步怎么安排",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        conversation_hash="d" * 64,
        limit=5,
        threshold=0.85,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.item_count == 1
    assert result.reason == "matched"


@pytest.mark.asyncio
async def test_session_fallback_keeps_threshold_floor_after_rerank(
    tmp_path: Path,
) -> None:
    class LowScoreRerank:
        embedding_enabled = False
        rerank_enabled = True

        async def rerank(
            self,
            query: str,
            documents: tuple[str, ...],
        ) -> tuple[SimpleNamespace, ...]:
            del query
            return tuple(
                SimpleNamespace(index=index, score=0.01)
                for index, _ in enumerate(documents)
            )

    adapter, workspace = _adapter(tmp_path)
    adapter.commit_turn(_payload())

    result = await OpenVikingRecallAdapter(
        workspace,
        LowScoreRerank(),  # type: ignore[arg-type]
    ).recall(
        query="下一步怎么安排",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        conversation_hash="d" * 64,
        limit=5,
        threshold=0.85,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.item_count == 1
    assert result.reason == "matched"


@pytest.mark.asyncio
async def test_session_fallback_rechecks_conversation_and_subject(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    adapter.commit_turn(_payload())
    recall = OpenVikingRecallAdapter(workspace)

    for conversation_hash, subject_hash in (("e" * 64, "c" * 64), ("d" * 64, "f" * 64)):
        result = await recall.recall(
            query="继续聊",
            agent_id="default",
            scope_filters=(
                {
                    "scope_type": "private_user",
                    "scope_hash": "b" * 64,
                    "subject_hash": subject_hash,
                },
            ),
            conversation_hash=conversation_hash,
            limit=5,
            threshold=0.2,
            max_chars=2_500,
        )

        assert result.included is False
        assert result.reason == "no_match"


@pytest.mark.asyncio
async def test_session_fallback_ignores_corrupt_commits_and_never_overrides_memory(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    commit = adapter.commit_turn(_payload())
    commit_path = next((workspace.root / "sessions").rglob(f"{commit.commit_id}.json"))
    record = json.loads(commit_path.read_text(encoding="utf-8"))
    record["action"] = "unexpected"
    commit_path.write_text(json.dumps(record), encoding="utf-8")

    recall = OpenVikingRecallAdapter(workspace)
    filters = (
        {
            "scope_type": "private_user",
            "scope_hash": "b" * 64,
            "subject_hash": "c" * 64,
        },
    )
    corrupt_result = await recall.recall(
        query="继续聊",
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert corrupt_result.included is False
    assert corrupt_result.reason == "no_match"

    clean_commit = adapter.commit_turn(_payload(commit="e", conversation_hash="e"))
    adapter.upsert_memory(
        _candidate(
            memory_key="preference:tea",
            content="用户喜欢无糖乌龙茶",
            conversation_hash="e",
        ),
        evidence=[],
        source_commit_ids=(clean_commit.commit_id,),
    )
    memory_result = await recall.recall(
        query="茶偏好",
        agent_id="default",
        scope_filters=filters,
        conversation_hash="e" * 64,
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert memory_result.included is True
    assert memory_result.source_refs[0].startswith("viking://agent/default/memories/")


@pytest.mark.asyncio
async def test_session_fallback_survives_unrelated_durable_memory(
    tmp_path: Path,
) -> None:
    adapter, workspace = _adapter(tmp_path)
    session_commit = adapter.commit_turn(_payload())
    other_commit = adapter.commit_turn(_payload(commit="e", conversation_hash="e"))
    adapter.upsert_memory(
        _candidate(
            memory_key="preference:coffee",
            content="用户喜欢咖啡",
            conversation_hash="e",
        ),
        evidence=[],
        source_commit_ids=(other_commit.commit_id,),
    )

    result = await OpenVikingRecallAdapter(workspace).recall(
        query="继续聊",
        agent_id="default",
        scope_filters=(
            {
                "scope_type": "private_user",
                "scope_hash": "b" * 64,
                "subject_hash": "c" * 64,
            },
        ),
        conversation_hash="d" * 64,
        limit=5,
        threshold=0.2,
        max_chars=2_500,
    )

    assert result.included is True
    assert result.source_refs == (
        "viking://agent/default/sessions/private_user/"
        f"{'b' * 64}/{'d' * 64}/commits/{session_commit.commit_id}",
    )


def test_render_truncation_fallback_never_exceeds_max_chars() -> None:
    """The truncated fallback must re-check the budget, not assume it fits."""
    adapter = OpenVikingRecallAdapter(SimpleNamespace())
    row = {
        "memory_type": "preference",
        "memory_key": "key",
        "content": "x" * 400,
        "uri": "viking://mem",
    }
    # max_chars=120: Notice + tag overhead alone exceed the budget, so even
    # the halved truncated content cannot fit and the render must omit.
    content, used = adapter._render([row], 120)
    assert content == ""
    assert used == []

    # With max_chars=400 the full row (~470) does not fit but the truncated
    # fallback (~300) does, so exactly one truncated memory is rendered.
    content, used = adapter._render([row], 400)
    assert used == [row]
    assert len(content) <= 400
    assert 'type="truncated"' in content


@pytest.mark.asyncio
async def test_session_history_search_filters_time_query_and_returns_ref(
    tmp_path: Path,
) -> None:
    """History search keeps rows inside the time window and surfaces context_ref."""
    adapter, workspace = _adapter(tmp_path)
    adapter.commit_turn(_payload())  # occurred_at = 2026-07-17T00:00:00+00:00

    searcher = OpenVikingRecallAdapter(workspace)
    filters = (
        {
            "scope_type": "private_user",
            "scope_hash": "b" * 64,
            "subject_hash": "c" * 64,
        },
    )

    in_window = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        since=datetime(2026, 7, 16, tzinfo=UTC),
        until=datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert in_window.included is True
    assert len(in_window.rows) == 1
    row = in_window.rows[0]
    assert row["action"] == "Reply"
    assert "无糖乌龙茶" in str(row["content"])
    assert row["context_ref"] == ""  # 无 context_ref 的裸提交不泄露假 ref
    assert str(row["updated_at"]).startswith("2026-07-17")

    before = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        until=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert before.included is False

    matched = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        query="乌龙茶",
    )
    assert matched.included is True

    miss = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        query="完全无关的检索词",
    )
    assert miss.included is False

    limited = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="e" * 64,
    )
    assert limited.included is False
    assert limited.reason == "no_match"


@pytest.mark.asyncio
async def test_recall_time_window_filters_memory_and_session_rows(
    tmp_path: Path,
) -> None:
    """recall() honouring since/until drops candidates outside the range."""
    adapter, workspace = _adapter(tmp_path)
    commit = adapter.commit_turn(_payload())  # 2026-07-17
    adapter.upsert_memory(
        _candidate(memory_key="preference:tea", content="用户喜欢无糖乌龙茶"),
        evidence=[],
        source_commit_ids=(commit.commit_id,),
    )

    async def recall(**kwargs):
        return await OpenVikingRecallAdapter(workspace).recall(
            query="乌龙茶",
            agent_id="default",
            scope_filters=(
                {
                    "scope_type": "private_user",
                    "scope_hash": "b" * 64,
                    "subject_hash": "c" * 64,
                },
            ),
            conversation_hash="d" * 64,
            limit=5,
            threshold=0.0,
            max_chars=2_500,
            **kwargs,
        )

    excluded = await recall(
        since=datetime(2026, 7, 18, tzinfo=UTC),
        until=datetime(2026, 7, 30, tzinfo=UTC),
    )
    assert excluded.included is False

    included = await recall(
        since=datetime(2026, 7, 1, tzinfo=UTC),
        until=datetime(2026, 7, 30, tzinfo=UTC),
    )
    assert included.included is True
    assert "无糖乌龙茶" in included.content

    unfiltered = await recall()
    assert unfiltered.included is True


@pytest.mark.asyncio
async def test_history_search_reads_observed_l2_rows_with_sender(
    tmp_path: Path,
) -> None:
    """context_l2 is the primary archive corpus: chatters, senders, transcribed images."""
    adapter, workspace = _adapter(tmp_path)
    adapter.commit_turn(_payload())  # legacy real turn, commits corpus only

    session_dir = (
        workspace.root
        / "sessions"
        / "default"
        / "private_user"
        / ("b" * 64)
        / ("d" * 64)
        / "context_l2"
    )
    session_dir.mkdir(parents=True)
    record = {
        "version": 1,
        "action": "Observed",
        "context_ref": "ctx-2A2B3C4D",
        "created_at": "2026-07-18T12:00:00+00:00",
        "sender_name": "小红",
        "bot_name": "",
        "l0": "小红: 看我家猫的照片",
        "messages": [
            {
                "role": "user",
                "content": "看我家猫的照片\n[图片 1: 一只橘猫趴在键盘上]",
            }
        ],
        "source_complete": True,
        "turn_ref": "",
        "message_id": "msg-observed-1",
    }
    (session_dir / "ctx-2A2B3C4D.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )

    searcher = OpenVikingRecallAdapter(workspace)
    filters = (
        {
            "scope_type": "private_user",
            "scope_hash": "b" * 64,
            "subject_hash": "c" * 64,
        },
    )

    # 图片转述文字是检索面：query 命中转述而不是用户文本本身。
    by_caption = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        query="橘猫",
    )
    assert by_caption.included is True
    row = by_caption.rows[0]
    assert row["action"] == "Observed"
    assert row["sender_name"] == "小红"
    assert row["context_ref"] == "ctx-2A2B3C4D"
    assert "橘猫趴在键盘上" in str(row["content"])

    by_sender = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        sender="小",
    )
    assert by_sender.included is True
    assert all(row["sender_name"] == "小红" for row in by_sender.rows)

    other_sender = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
        sender="小明",
    )
    # 旁观命中被 sender 过滤排除；legacy commit 行无发送者，同样不入选。
    assert other_sender.included is False

    combined = await searcher.search_session_history(
        agent_id="default",
        scope_filters=filters,
        conversation_hash="d" * 64,
    )
    assert combined.included is True
    actions = {str(row["action"]) for row in combined.rows}
    assert {"Reply", "Observed"} <= actions
