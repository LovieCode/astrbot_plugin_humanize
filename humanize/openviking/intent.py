"""Intent analyzer ported from OpenViking retrieval.

This module is a pure-algorithm port of OpenViking's ``intent_analyzer``: it
turns the current user message plus bounded session context into a typed query
plan (memory type + query + priority) used by the type-quota recall path.

No OpenViking service, config, or prompt-rendering dependencies are used. The
LLM completion is injected by the caller (the plugin's configured extraction
provider), so this module stays a plain prompt-builder and JSON parser.

The original module is AGPL-3.0 (c) 2026 Beijing Volcano Engine Technology
Co., Ltd.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from string import Template
from typing import Any

logger = logging.getLogger("astrbot")

DEFAULT_MAX_RECENT_MESSAGES = 5
DEFAULT_MAX_SUMMARY_CHARS = 30_000

# Plugin memory types (subset of OpenViking's TYPE_ORDER that this plugin
# actually writes).
PLUGIN_MEMORY_TYPES = ("preference", "entity", "event")

_INTENT_PROMPT_TEMPLATE = Template(
    """\
你是记忆检索规划器。根据用户当前消息和最近的会话上下文，判断需要在哪些类型的长期记忆中检索相关信息，并为每种类型生成一个检索查询。

记忆类型说明：
- preference: 用户的喜好、偏好、习惯、个人资料（如喜欢的食物、作息、性格特征）
- entity: 具体的人、地点、物品、组织等实体及其属性
- event: 已发生的事件、经历、对话内容（如一起做过什么、说过什么）

如果某个类型的长期记忆与当前消息无关，不要输出该类型的查询。如果没有相关类型，输出空列表。

输出严格 JSON（不要输出其他内容）：
{"reasoning": "简短分析", "queries": [{"query": "检索查询", "context_type": "preference|entity|event", "intent": "意图描述", "priority": 1-5}]}

会话摘要：
$compression_summary

最近消息：
$recent_messages

当前消息：$current_message
"""
)


@dataclass(frozen=True, slots=True)
class TypedQuery:
    """One typed retrieval query from intent analysis."""

    query: str
    context_type: str
    intent: str = ""
    priority: int = 3


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Result of intent analysis: multiple typed queries."""

    queries: list[TypedQuery] = field(default_factory=list)
    reasoning: str = ""
    session_context: str = ""


def _parse_json_from_response(response: str) -> dict[str, Any] | None:
    """Parse a JSON object from an LLM response robustly.

    Tries strict JSON first, then extracts the first balanced ``{...}`` block,
    and finally repairs common trailing-comma issues.

    Args:
        response: Raw LLM completion text.

    Returns:
        Parsed dictionary or ``None`` when no object is found.
    """
    if not response:
        return None
    text = response.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Extract the first balanced {...} block.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                block = text[start : index + 1]
                break
    else:
        block = text[start:]
    try:
        parsed = json.loads(block)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    # Repair trailing commas (common LLM mistake).
    repaired = re.sub(r",\s*([}\]])", r"\1", block)
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        return None
    return None


def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to keep prompts bounded.

    Args:
        text: Input text.
        max_chars: Maximum length.

    Returns:
        Truncated text with a marker when shortened.
    """
    if not text or len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 15)] + "\n...(truncated)"


class IntentAnalyzer:
    """Generate typed retrieval queries from the current message."""

    def __init__(
        self,
        completion: Callable[[str], Awaitable[str]],
        *,
        max_recent_messages: int = DEFAULT_MAX_RECENT_MESSAGES,
        max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
    ) -> None:
        """Initialize the analyzer.

        Args:
            completion: Async callable that takes one prompt and returns the
                LLM completion text.
            max_recent_messages: Number of recent messages included in context.
            max_summary_chars: Maximum compression summary characters.
        """
        self._completion = completion
        self._max_recent_messages = max_recent_messages
        self._max_summary_chars = max_summary_chars

    async def analyze(
        self,
        *,
        current_message: str,
        compression_summary: str = "",
        recent_messages: list[dict[str, str]] | None = None,
    ) -> QueryPlan:
        """Analyze the current message and build a typed query plan.

        Args:
            current_message: Current unwrapped user text.
            compression_summary: Optional session summary.
            recent_messages: Optional list of ``{"role": ..., "content": ...}``.

        Returns:
            Query plan with typed queries. Empty queries on parse failure.
        """
        if not (current_message and current_message.strip()):
            return QueryPlan(queries=[], reasoning="empty_message")
        summary = _truncate_text(compression_summary or "", self._max_summary_chars)
        recent = (recent_messages or [])[-self._max_recent_messages :]
        recent_text = (
            "\n".join(
                f"[{str(item.get('role') or '')}]: {str(item.get('content') or '')}"
                for item in recent
                if item.get("content")
            )
            if recent
            else "None"
        )
        prompt = _INTENT_PROMPT_TEMPLATE.substitute(
            compression_summary=summary or "None",
            recent_messages=recent_text,
            current_message=current_message.strip()[:2_000],
        )
        try:
            response = await self._completion(prompt)
        except Exception as exc:
            logger.warning(
                "[Humanize] intent analysis LLM degraded: %s", type(exc).__name__
            )
            return QueryPlan(queries=[], reasoning="llm_error")

        parsed = _parse_json_from_response(response)
        if not parsed:
            logger.warning("[Humanize] intent analysis JSON parse failed")
            return QueryPlan(queries=[], reasoning="parse_error")

        queries: list[TypedQuery] = []
        for raw in (
            parsed.get("queries", []) if isinstance(parsed.get("queries"), list) else []
        ):
            if not isinstance(raw, dict):
                continue
            context_type = str(raw.get("context_type") or "").strip()
            query = str(raw.get("query") or "").strip()
            if context_type not in PLUGIN_MEMORY_TYPES or not query:
                continue
            try:
                priority = int(raw.get("priority", 3))
            except (TypeError, ValueError):
                priority = 3
            queries.append(
                TypedQuery(
                    query=query[:500],
                    context_type=context_type,
                    intent=str(raw.get("intent") or "")[:200],
                    priority=max(1, min(priority, 5)),
                )
            )
        logger.info(
            "[Humanize] intent analysis produced %d typed queries",
            len(queries),
        )
        return QueryPlan(
            queries=queries,
            reasoning=str(parsed.get("reasoning") or "")[:500],
            session_context=(
                f"Session summary: {summary}" if summary else "No context"
            ),
        )
