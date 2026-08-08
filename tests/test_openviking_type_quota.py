from __future__ import annotations

from typing import Any

import pytest
from humanize.openviking.intent import IntentAnalyzer, QueryPlan, TypedQuery
from humanize.openviking.type_quota import (
    DEFAULT_QUOTAS,
    ORIGIN_ORDER,
    _extract_event_summary,
    _origin_for_scope,
    normalize_penalties,
    normalize_quotas,
    select_type_quota,
    type_char_budgets,
)


def _row(
    *,
    uri: str,
    score: float,
    memory_type: str,
    content: str,
    abstract: str = "",
    scope_type: str = "private_user",
) -> dict[str, Any]:
    return {
        "uri": uri,
        "score": score,
        "memory_type": memory_type,
        "content": content,
        "abstract": abstract or content[:60],
        "scope_type": scope_type,
    }


# ---------- type_quota ----------


def test_normalize_quotas_merges_and_clamps() -> None:
    assert normalize_quotas(None) == DEFAULT_QUOTAS
    assert normalize_quotas({"event": 3, "entity": -5, "unknown": 99}) == {
        "event": 3,
        "entity": 0,
        "preference": 3,
    }
    assert normalize_quotas({"event": "bad"})["event"] == 0


def test_normalize_penalties_defaults_and_clamps() -> None:
    defaults = normalize_penalties()
    assert defaults["event"] == 0.1
    assert 0.0 <= defaults["preference"] <= 1.0
    assert normalize_penalties({"event": 5})["event"] == 1.0
    assert normalize_penalties(0.5) == dict.fromkeys(
        ("event", "entity", "preference"), 0.5
    )


def test_origin_for_scope_mapping() -> None:
    assert _origin_for_scope("global") == "self"
    assert _origin_for_scope("private_user") == "actor_peer"
    assert _origin_for_scope("group_member") == "actor_peer"
    assert _origin_for_scope("group") == "other_peer"
    assert _origin_for_scope("unknown") == "other_peer"


def test_type_char_budgets_events_capped() -> None:
    budgets = type_char_budgets(1000)
    assert budgets["event"] == 750
    assert budgets["entity"] == 1000
    assert budgets["preference"] == 1000


def test_select_type_quota_groups_by_type_and_applies_quota() -> None:
    rows = [
        _row(
            uri="viking://.../preferences/a",
            score=0.9,
            memory_type="preference",
            content="喜欢喝茶",
        ),
        _row(
            uri="viking://.../preferences/b",
            score=0.8,
            memory_type="preference",
            content="喜欢喝咖啡",
        ),
        _row(
            uri="viking://.../preferences/c",
            score=0.7,
            memory_type="preference",
            content="喜欢喝可乐",
        ),
        _row(
            uri="viking://.../entities/d",
            score=0.6,
            memory_type="entity",
            content="小明是程序员",
        ),
        _row(
            uri="viking://.../events/e",
            score=0.5,
            memory_type="event",
            content="昨天一起爬山",
        ),
    ]
    result = select_type_quota(
        rows, quotas={"preferences": 2, "entities": 1, "events": 1}
    )
    by_type: dict[str, list[str]] = {}
    for entry in result.entries:
        by_type.setdefault(entry.memory_type, []).append(entry.uri)
    # preferences quota 2 -> only top-2 by score
    assert len(by_type.get("preference", [])) == 2
    assert by_type["preference"][0].endswith("/preferences/a")
    assert len(by_type.get("entity", [])) == 1
    assert len(by_type.get("event", [])) == 1
    assert result.stats["returned"] == 4
    assert result.stats["searched"]["preference"] == 3


def test_select_type_quota_degrades_full_to_summary_and_uri() -> None:
    rows = [
        _row(
            uri="viking://.../events/e1",
            score=0.9,
            memory_type="event",
            content="Summary: 一起爬山\n2026-07-17 ChatLog:\n- 好累\n- 山顶风景好",
            abstract="爬山",
        ),
        _row(
            uri="viking://.../events/e2",
            score=0.8,
            memory_type="event",
            content="没有摘要格式的普通内容，但足够长",
            abstract="普通事件",
        ),
    ]
    result = select_type_quota(rows, quotas={"events": 2}, max_chars=300)
    modes = {entry.mode for entry in result.entries}
    # Event summary extraction should kick in; the second event may degrade to uri when budget is tight.
    assert "summary" in modes or "full" in modes
    assert all(entry.mode in {"full", "summary", "uri"} for entry in result.entries)


def test_select_type_quota_respects_min_score_and_drops_over_budget() -> None:
    rows = [
        _row(
            uri="viking://.../preferences/a",
            score=0.05,
            memory_type="preference",
            content="x" * 5000,
        ),
    ]
    result = select_type_quota(
        rows, quotas={"preferences": 1}, min_score=0.2, max_chars=100
    )
    assert result.entries == []
    assert result.stats["returned"] == 0


def test_select_type_quota_origin_counts() -> None:
    rows = [
        _row(
            uri="viking://.../preferences/a",
            score=0.9,
            memory_type="preference",
            content="喜欢茶",
            scope_type="private_user",
        ),
        _row(
            uri="viking://.../preferences/b",
            score=0.8,
            memory_type="preference",
            content="喜欢酒",
            scope_type="group",
        ),
    ]
    result = select_type_quota(rows, quotas={"preferences": 2})
    origins = result.stats["origins"]
    assert origins["actor_peer"] == 1
    assert origins["other_peer"] == 1
    assert origins["self"] == 0
    assert set(origins) == set(ORIGIN_ORDER)


def test_extract_event_summary() -> None:
    content = "Summary: 我们去看电影了\n2026-07-17 ChatLog:\n- 买了爆米花"
    assert _extract_event_summary(content) == "我们去看电影了"
    assert _extract_event_summary("无格式内容", fallback="兜底") == "兜底"


# ---------- intent ----------


def _make_analyzer(response: str) -> IntentAnalyzer:
    async def completion(prompt: str) -> str:
        return response

    return IntentAnalyzer(completion)


@pytest.mark.asyncio
async def test_intent_parses_typed_queries() -> None:
    analyzer = _make_analyzer(
        '{"reasoning":"用户提到吃饭","queries":['
        '{"query":"喜欢吃什么","context_type":"preference","intent":"偏好","priority":4},'
        '{"query":"一起吃过什么","context_type":"event","intent":"经历","priority":2}]}'
    )
    plan = await analyzer.analyze(current_message="我们去吃火锅吧")
    assert isinstance(plan, QueryPlan)
    assert len(plan.queries) == 2
    first = plan.queries[0]
    assert isinstance(first, TypedQuery)
    assert first.query == "喜欢吃什么"
    assert first.context_type == "preference"
    assert first.priority == 4


@pytest.mark.asyncio
async def test_intent_ignores_unsupported_types_and_empty() -> None:
    analyzer = _make_analyzer(
        '{"queries":[{"query":"q1","context_type":"skill","priority":1},'
        '{"query":"","context_type":"preference"}]}'
    )
    plan = await analyzer.analyze(current_message="你好")
    assert plan.queries == []


@pytest.mark.asyncio
async def test_intent_falls_back_on_bad_json() -> None:
    analyzer = _make_analyzer("抱歉，我没法分析")
    plan = await analyzer.analyze(current_message="你好")
    assert plan.queries == []
    assert plan.reasoning == "parse_error"


@pytest.mark.asyncio
async def test_intent_recovers_from_trailing_comma() -> None:
    analyzer = _make_analyzer(
        '{"queries":[{"query":"喜欢什么","context_type":"preference","priority":3,}]}'
    )
    plan = await analyzer.analyze(current_message="你喜欢什么")
    assert len(plan.queries) == 1
    assert plan.queries[0].context_type == "preference"


@pytest.mark.asyncio
async def test_intent_empty_message_short_circuits() -> None:
    analyzer = _make_analyzer('{"queries":[]}')
    plan = await analyzer.analyze(current_message="   ")
    assert plan.queries == []
    assert plan.reasoning == "empty_message"
