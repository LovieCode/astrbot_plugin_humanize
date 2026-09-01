"""Tests for memory decay, contradiction penalty, merge and related links."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingManagementAdapter,
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)
from astrbot_plugin_humanize.humanize.openviking.decay import (
    apply_contradiction_penalty,
    decay_factor,
    decayed_confidence,
)
from tests.test_openviking_adapter import _candidate, _payload

_SCOPE = ("private_user", "b" * 64, "c" * 64)


def _candidate_for(
    memory_key: str,
    content: str,
    *,
    confidence: float = 0.9,
    occurred_at: str = "2026-07-17T00:00:00+00:00",
    structured: dict | None = None,
) -> dict:
    candidate = dict(_candidate(content=content, confidence=confidence))
    candidate["memory_key"] = memory_key
    candidate["occurred_at"] = occurred_at
    if structured is not None:
        candidate["structured_value"] = structured
    return candidate


def _write(
    adapter: OpenVikingMemoryAdapter,
    candidate: dict,
    *,
    commit: str = "a",
    evidence: list | None = None,
    related_uris: tuple[str, ...] = (),
    penalty: float = 0.5,
):
    adapter.commit_turn(_payload(commit=commit))
    return adapter.upsert_memory(
        candidate,
        evidence=evidence
        or [
            {
                "quote": str(candidate["content"]),
                "occurred_at": str(candidate["occurred_at"]),
                "source_complete": True,
            }
        ],
        source_commit_ids=(commit * 64,),
        related_uris=related_uris,
        contradiction_penalty=penalty,
    )


def _workspace(tmp_path: Path):
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    adapter = OpenVikingMemoryAdapter(workspace)
    adapter.initialize()
    return workspace, adapter


def test_decay_factor_halves_after_one_half_life() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    old = (now - timedelta(days=120)).isoformat()
    assert decay_factor(old, now=now, half_life_days=120) == 0.5
    # 缺失/非法时间不衰减。
    assert decay_factor("", now=now, half_life_days=120) == 1.0
    assert decay_factor("not-a-time", now=now, half_life_days=120) == 1.0
    # 未来时间不放大。
    future = (now + timedelta(days=1)).isoformat()
    assert decay_factor(future, now=now, half_life_days=120) == 1.0


def test_decayed_confidence_and_contradiction_penalty_bounds() -> None:
    now = datetime(2026, 9, 1, tzinfo=UTC)
    old = (now - timedelta(days=240)).isoformat()
    decayed = decayed_confidence(0.9, old, now=now, half_life_days=120)
    assert abs(decayed - 0.225) < 1e-6
    assert 0.0 <= decayed <= 1.0
    # 矛盾惩罚有下限。
    assert apply_contradiction_penalty(0.8, 0.5) == 0.4
    assert apply_contradiction_penalty(0.01, 0.5) == 0.05
    assert apply_contradiction_penalty(0.8, 0.0) == 0.05


def test_upsert_merges_structured_value_and_evidence(tmp_path: Path) -> None:
    workspace, adapter = _workspace(tmp_path)
    management = OpenVikingManagementAdapter(adapter, workspace)
    _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户喜欢无糖乌龙茶",
            confidence=0.9,
            structured={"like": "无糖乌龙茶", "origin": "2026"},
        ),
        commit="a",
    )
    replaced = _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户现在喜欢黑咖啡",
            confidence=0.95,
            structured={"like": "黑咖啡", "price": "中杯 18 元"},
        ),
        commit="e",
        evidence=[
            {
                "quote": "用户现在喜欢黑咖啡",
                "occurred_at": "2026-07-17T00:00:00+00:00",
                "source_complete": True,
            }
        ],
    )
    assert replaced.operation == "replace"
    assert replaced.version == 2

    detail = management.get_memory_detail(replaced.memory_uri.rsplit("/", 1)[-1])
    assert detail is not None
    # 结构化字段级合并：新值生效、旧字段保留、证据链累积。
    assert detail["structured_value"] == {
        "like": "黑咖啡",
        "origin": "2026",
        "price": "中杯 18 元",
    }
    quotes = {str(item["quote"]) for item in detail["evidence"]}
    assert "用户喜欢无糖乌龙茶" in quotes
    assert "用户现在喜欢黑咖啡" in quotes


def test_upsert_contradiction_penalizes_kept_memory(tmp_path: Path) -> None:
    workspace, adapter = _workspace(tmp_path)
    management = OpenVikingManagementAdapter(adapter, workspace)
    _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户喜欢乌龙茶",
            confidence=0.9,
            structured={"like": "乌龙茶"},
        ),
    )
    # 新证据置信度更低、内容不同且结构化同 key 冲突：旧记忆保留但被证伪一次。
    contradicted = _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户其实讨厌乌龙茶",
            confidence=0.4,
            occurred_at="2026-08-20",
            structured={"like": "讨厌乌龙茶"},
        ),
        commit="e",
    )
    assert contradicted.operation == "keep"
    detail = management.get_memory_detail(contradicted.memory_uri.rsplit("/", 1)[-1])
    assert detail is not None
    assert detail["content"] == "用户喜欢乌龙茶"
    assert detail["confidence"] == 0.45  # 0.9 × 0.5 反例惩罚


def test_upsert_rephrasing_without_conflict_does_not_penalize(
    tmp_path: Path,
) -> None:
    """同义改写/补充没有同 key 结构化冲突 → 不触发反例惩罚。"""
    workspace, adapter = _workspace(tmp_path)
    management = OpenVikingManagementAdapter(adapter, workspace)
    _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户喜欢乌龙茶",
            confidence=0.9,
            structured={"like": "乌龙茶"},
        ),
    )
    # 内容变了但结构化没冲突（补充了另一个字段）→ keep 且不惩罚。
    kept = _write(
        adapter,
        _candidate_for(
            "preference:tea",
            "用户喜欢乌龙茶和咖啡",
            confidence=0.6,
            structured={"like": "乌龙茶", "note": "也喝咖啡"},
        ),
        commit="e",
    )
    assert kept.operation == "keep"
    detail = management.get_memory_detail(kept.memory_uri.rsplit("/", 1)[-1])
    assert detail is not None
    assert detail["confidence"] == 0.9  # 未被打折
    assert detail["structured_value"]["note"] == "也喝咖啡"  # 字段级合并仍生效


def test_upsert_related_links_same_batch_memories(tmp_path: Path) -> None:
    workspace, adapter = _workspace(tmp_path)
    management = OpenVikingManagementAdapter(adapter, workspace)
    first = _write(
        adapter,
        _candidate_for("preference:hiking", "小红喜欢爬山", confidence=0.9),
        commit="a",
    )
    second_uri = adapter.memory_uri_for(
        agent_id="default",
        scope_type=_SCOPE[0],
        scope_hash=_SCOPE[1],
        subject_hash=_SCOPE[2],
        memory_type="preference",
        memory_key="preference:shoes",
    )
    second = _write(
        adapter,
        _candidate_for("preference:shoes", "小红买了登山鞋", confidence=0.85),
        commit="e",
        related_uris=(first.memory_uri,),
    )
    assert second.operation == "create"
    detail = management.get_memory_detail(second.memory_uri.rsplit("/", 1)[-1])
    assert detail is not None
    assert any(str(item.get("uri")) == first.memory_uri for item in detail["related"])
    assert second_uri == second.memory_uri


def test_recall_excludes_decayed_memories_and_boosts_related(
    tmp_path: Path,
) -> None:
    workspace, adapter = _workspace(tmp_path)
    hike_uri = adapter.memory_uri_for(
        agent_id="default",
        scope_type=_SCOPE[0],
        scope_hash=_SCOPE[1],
        subject_hash=_SCOPE[2],
        memory_type="preference",
        memory_key="preference:hike",
    )
    shoe_uri = adapter.memory_uri_for(
        agent_id="default",
        scope_type=_SCOPE[0],
        scope_hash=_SCOPE[1],
        subject_hash=_SCOPE[2],
        memory_type="preference",
        memory_key="preference:shoes",
    )
    # 老记忆：置信度 0.9，半衰期 30 天，距今约 123 天 → 有效置信度 ~0.05，
    # 低于遗忘边界 0.5 → 不被召回。
    _write(
        adapter,
        _candidate_for(
            "preference:old",
            "用户旧偏好已过期",
            confidence=0.9,
            occurred_at="2026-05-01T00:00:00+00:00",
        ),
    )
    _write(
        adapter,
        _candidate_for(
            "preference:hike",
            "小红喜欢爬山",
            confidence=0.9,
            occurred_at="2026-09-01T00:00:00+00:00",
        ),
        commit="e",
        related_uris=(shoe_uri,),
    )
    _write(
        adapter,
        _candidate_for(
            "preference:shoes",
            "小红买了登山鞋",
            confidence=0.9,
            occurred_at="2026-09-01T00:00:00+00:00",
        ),
        commit="f",
        related_uris=(hike_uri,),
    )

    recall = OpenVikingRecallAdapter(
        workspace,
        None,
        decay_half_life_days=30.0,
        decay_min_confidence=0.5,
        related_boost=0.15,
    )
    filters = (
        {"scope_type": _SCOPE[0], "scope_hash": _SCOPE[1], "subject_hash": _SCOPE[2]},
    )

    async def scenario() -> None:
        result = await recall.recall(
            query="小红喜欢爬山吗",
            agent_id="default",
            scope_filters=filters,
            limit=5,
            threshold=0.0,
            max_chars=2_000,
        )
        assert result.included is True
        # 老记忆被遗忘，新鲜记忆在。
        assert "小红喜欢爬山" in result.content
        assert "旧偏好" not in result.content
        # 一跳加成：登山鞋因与爬山同批共现而浮现。
        assert "登山鞋" in result.content

        # 反向检索同样能经关联带出爬山。
        other = await recall.recall(
            query="登山鞋",
            agent_id="default",
            scope_filters=filters,
            limit=5,
            threshold=0.0,
            max_chars=2_000,
        )
        assert "小红喜欢爬山" in other.content

    asyncio.run(scenario())


def test_management_detail_reports_decayed_confidence(tmp_path: Path) -> None:
    workspace, adapter = _workspace(tmp_path)
    _write(
        adapter,
        _candidate_for(
            "preference:old",
            "很久以前的偏好",
            confidence=0.9,
            occurred_at="2026-01-01T00:00:00+00:00",
        ),
    )
    management = OpenVikingManagementAdapter(
        adapter, workspace, decay_half_life_days=30.0
    )
    detail = management.get_memory_detail(
        adapter.memory_uri_for(
            agent_id="default",
            scope_type=_SCOPE[0],
            scope_hash=_SCOPE[1],
            subject_hash=_SCOPE[2],
            memory_type="preference",
            memory_key="preference:old",
        ).rsplit("/", 1)[-1]
    )
    assert detail is not None
    assert detail["confidence"] == 0.9
    assert 0.0 < detail["decayed_confidence"] < 0.9
