"""LLM digest for the managed context-window rolling summary.

The compaction path always writes a deterministic transcript first
(``[sender · time] body （ctx-XXXXXXXX）`` per evicted turn). This module
implements the second PLAN stage: an asynchronous Provider pass that
rewrites those lines into a shorter digest while keeping the same
line shape and whitelisted context references, so the model can still
read back the full record behind any summarized fact.
"""

from __future__ import annotations

import re
from typing import Any

_REF_PATTERN = re.compile(r"ctx-[A-Z2-7]{8}")
_LINE_PREFIX_PATTERN = re.compile(r"^\[[^\[\]\n]+\]\s")
_MAX_LINES = 200

_SYSTEM_PROMPT = (
    "你是对话记录压缩器。把给定的历史聊天逐行摘要成更短的记录，只输出摘要本身。"
)
_PROMPT_TEMPLATE = """把下面这段较早的聊天记录压缩成一份更短的逐行摘要。

要求：
1. 每行保持原格式：[发送者 · 时间] 内容。
2. 保留重要信息：人名、时间、约定、计划、观点、承诺、分歧、明确的事实；合并重复的闲聊（如刷屏、表情、寒暄可以合并成一行）。
3. 时间和发送者必须保留；一行可以概括多轮相近内容。
4. 在重要信息后面用括号标注对应的引用，例如“（ctx-2A2B3C4D）”。只能使用输入里出现过的引用，不得编造。
5. 总长度不超过 {max_chars} 字，行数尽量少。
6. 只输出摘要行本身，不要输出任何解释、标题或额外说明。

聊天记录：
{content}"""


class ContextSummarizer:
    """Rewrite the deterministic compaction summary through a Provider."""

    def __init__(
        self,
        provider_bridge: Any,
        *,
        max_chars: int = 2_400,
    ) -> None:
        """Store the bridge and bounds without contacting the Provider.

        Args:
            provider_bridge: ``OpenVikingProviderBridge``-like object with an
                ``async complete(prompt, system_prompt)`` method.
            max_chars: Hard character cap applied to the validated digest.
        """
        self._bridge = provider_bridge
        self._max_chars = max(200, int(max_chars))

    async def digest(self, text: str) -> str | None:
        """Produce one validated digest, or ``None`` on any failure.

        The Provider output is untrusted: lines without the ``[...]``
        prefix are dropped, context references are restricted to the ones
        present in the input, and the result is bounded by both a line
        count and a character cap (dropping oldest lines first).

        Args:
            text: Current deterministic summary text (non-empty).

        Returns:
            Sanitized digest text, or ``None`` when the Provider fails or
            produces nothing usable. Never raises.
        """
        source = str(text or "").strip()
        if not source:
            return None
        allowed_refs = set(_REF_PATTERN.findall(source))
        prompt = _PROMPT_TEMPLATE.format(max_chars=self._max_chars, content=source)
        try:
            raw = await self._bridge.complete(prompt, system_prompt=_SYSTEM_PROMPT)
        except Exception:
            return None
        return self.sanitize(raw, allowed_refs)

    def sanitize(self, raw: str, allowed_refs: set[str]) -> str | None:
        """Validate untrusted completion text into bounded digest lines.

        Args:
            raw: Provider completion text.
            allowed_refs: Context references that may appear in the output.

        Returns:
            Sanitized digest, or ``None`` when nothing usable remains.
        """
        lines: list[str] = []
        for raw_line in str(raw or "").splitlines():
            line = " ".join(raw_line.split())
            if not line or not _LINE_PREFIX_PATTERN.match(line):
                continue
            line = self._filter_refs(line, allowed_refs)
            if line:
                lines.append(line)
        lines = lines[:_MAX_LINES]
        while lines and len("\n".join(lines)) > self._max_chars:
            lines.pop(0)
        if not lines:
            return None
        return "\n".join(lines)

    @staticmethod
    def _filter_refs(line: str, allowed_refs: set[str]) -> str:
        """Strip context references that are not in the whitelist."""
        if not allowed_refs:
            return _REF_PATTERN.sub("", line).replace("（）", "").replace("()", "")

        def _replace(match: re.Match[str]) -> str:
            return match.group(0) if match.group(0) in allowed_refs else ""

        return _REF_PATTERN.sub(_replace, line).replace("（）", "").replace("()", "")
