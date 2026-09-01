from __future__ import annotations

import json
import math
import re
from typing import Any

from ..config import PluginConfig
from ..domain.errors import ProtocolValidationError
from ..domain.models import Action, ImageCache, ProtocolDecision, UnknownTerm

_TAG_RE = None  # 保留占位：标签由各自独立正则提取
_ACTION_TAG_RE = re.compile(
    r"<\s*Action\s*>(?P<value>.*?)<\s*/\s*Action\s*>", re.IGNORECASE | re.DOTALL
)
_UNKNOWN_TERMS_TAG_RE = re.compile(
    r"<\s*UnknownTerms\s*>(?P<value>[\s\S]*?)<\s*/\s*UnknownTerms\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MESSAGES_TAG_RE = re.compile(
    r"<\s*Messages\s*>(?P<inner>[\s\S]*?)<\s*/\s*Messages\s*>",
    re.IGNORECASE | re.DOTALL,
)
_MESSAGE_TAG_RE = re.compile(
    r"<\s*Message\s*>(?P<body>[\s\S]*?)<\s*/\s*Message\s*>", re.IGNORECASE | re.DOTALL
)
_REPLY_BLOCK_TAG_RE = re.compile(
    r"<\s*Reply\s*>(?P<inner>[\s\S]*?)<\s*/\s*Reply\s*>", re.IGNORECASE | re.DOTALL
)
_IMAGECACHE_TAG_RE = re.compile(
    r"<\s*ImageCache\s*>(?P<value>[\s\S]*?)<\s*/\s*ImageCache\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Wait N：<Action>Wait N</Action>，N 为 1..MAX_WAIT_SECONDS 的整数秒。
# 群聊回合的常驻动作（话没说完/不便插话时暂不回应，稍后补一次决定）；
# 仅在调用方明确允许时合法（私聊与关闭主动参与的群保持禁用）。
MAX_WAIT_SECONDS = 29
_WAIT_ACTION_RE = re.compile(r"^\s*Wait\s+(?P<seconds>\d{1,3})\s*$", re.IGNORECASE)

_CONTROL_MARKERS = ("action:", "unknownterms:", "<action", "<unknownterms")
_STRUCTURED_REPLY_LINE_PATTERN = re.compile(
    r"^\s*(?:"
    r"#{1,6}(?:\s|$)|[-+*]\s+|>\s?|(?:\d+|[A-Za-z])[.)]\s+|\||"
    r"\[[A-Za-z][A-Za-z0-9 _-]{1,}\]|(?:\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?)\b|"
    r"(?:https?://|www\.)"
    r")"
)


class ProtocolParser:
    def __init__(self, config: PluginConfig) -> None:
        self._config = config

    def parse(self, raw_output: str, *, allow_wait: bool = False) -> ProtocolDecision:
        """Validate one model output into a protocol decision.

        Args:
            raw_output: Full model output.
            allow_wait: Whether the ``Wait N`` action is legal for this turn
                (group turns with a landing spot for the delayed re-check).
                Turns that could never re-evaluate keep it disabled.

        Returns:
            Parsed decision; ``wait_seconds`` is set only for ``Wait``.

        Raises:
            ProtocolValidationError: On any contract violation.
        """
        raw = raw_output or ""
        if not raw.strip():
            raise ProtocolValidationError("empty_output", "LLM returned no final text")

        action_value = self._extract_action(raw)
        if action_value is None:
            raise ProtocolValidationError(
                "missing_action",
                "Response must contain an <Action> tag",
            )
        wait_match = _WAIT_ACTION_RE.match(action_value)
        try:
            action = Action(action_value)
        except ValueError:
            action = None
        wait_seconds = 0
        if action is Action.WAIT or wait_match is not None:
            # ``Wait`` 只在允许延迟重查的回合合法；缺秒数或超出上限都是格式错误。
            if wait_match is None:
                raise ProtocolValidationError(
                    "invalid_wait_seconds",
                    "Wait requires a second count",
                )
            if not allow_wait:
                raise ProtocolValidationError(
                    "wait_not_allowed",
                    "Wait is not allowed in this turn",
                )
            seconds = int(wait_match.group("seconds"))
            if not 1 <= seconds <= MAX_WAIT_SECONDS:
                raise ProtocolValidationError(
                    "invalid_wait_seconds",
                    f"Wait seconds must be within 1..{MAX_WAIT_SECONDS}",
                )
            action = Action.WAIT
            wait_seconds = seconds
        elif action is None:
            raise ProtocolValidationError(
                "invalid_action",
                f"Unsupported Action value: {action_value or '<empty>'}",
            )

        unknown_terms = self._extract_unknown_terms(raw)

        image_cache = self._extract_image_cache(raw)

        messages = self._extract_messages(raw)

        if action is Action.REPLY and not messages:
            # 新协议要求 Reply 必填 <Messages>；缺失视为格式错误直接阻断。
            raise ProtocolValidationError(
                "missing_messages",
                "Reply requires at least one <Message> in a <Messages> block",
            )
        over_limit = False
        if (
            action is Action.REPLY
            and len(messages) > self._config.max_messages_per_reply
        ):
            # 超限不硬失败：保留前 N 条发送，协议日志可解释。
            messages = messages[: self._config.max_messages_per_reply]
            over_limit = True
        no_reply_reason = ""
        if action is Action.NO_REPLY:
            if not self._config.no_reply_enabled:
                raise ProtocolValidationError(
                    "no_reply_disabled", "No Reply is disabled by plugin configuration"
                )
            # 新协议：No Reply 时 <Messages> 写不回复原因，仅供日志与追踪展示。
            no_reply_reason = "\n".join(messages)[:500].strip()
            messages = ()

        return ProtocolDecision(
            action=action,
            messages=messages if action is Action.REPLY else (),
            unknown_terms=unknown_terms,
            image_cache=image_cache,
            no_reply_reason=no_reply_reason,
            messages_over_limit=over_limit,
            wait_seconds=wait_seconds,
        )

    # ---------- 标签提取（位置不限、可缺省） ----------

    def _extract_action(self, raw: str) -> str | None:
        """Extract the Action value from anywhere in the output.

        Args:
            raw: Full model output.

        Returns:
            Normalized action value, or None when no Action tag is present.
        """
        match = _ACTION_TAG_RE.search(raw)
        if match is not None:
            return match.group("value").strip()
        return None

    def _extract_unknown_terms(self, raw: str) -> tuple[UnknownTerm, ...]:
        """Extract UnknownTerms from anywhere; missing tag defaults to empty.

        Args:
            raw: Full model output.

        Returns:
            Parsed unknown terms, or () when the tag is absent or empty
            （有标签但没写 JSON 按空处理；内容解析失败仍判协议错）.
        """
        match = _UNKNOWN_TERMS_TAG_RE.search(raw)
        if match is None:
            return ()
        payload_raw = match.group("value").strip()
        if not payload_raw:
            # 有标签但没写内容：等价于没有陌生词，不判协议错误。
            return ()
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            raise ProtocolValidationError(
                "invalid_unknown_terms_json", str(exc)
            ) from exc
        return self._parse_unknown_terms(payload)

    def _parse_unknown_terms(self, payload: Any) -> tuple[UnknownTerm, ...]:
        if not isinstance(payload, list):
            raise ProtocolValidationError(
                "invalid_unknown_terms", "UnknownTerms must be a JSON array"
            )
        result: list[UnknownTerm] = []
        for item in payload[:16]:
            if not isinstance(item, dict) or set(item) - {
                "word",
                "guess",
                "confidence",
                "reason",
            }:
                raise ProtocolValidationError(
                    "invalid_unknown_terms",
                    "UnknownTerms items may contain only word, guess, confidence, reason",
                )
            raw_word = item.get("word")
            if not isinstance(raw_word, str):
                raise ProtocolValidationError(
                    "invalid_unknown_terms", "UnknownTerms word must be a string"
                )
            word = raw_word.strip()
            if not word or len(word) > 128:
                raise ProtocolValidationError(
                    "invalid_unknown_terms", "UnknownTerms word is empty or too long"
                )
            guess = str(item.get("guess") or "").strip()[:600]
            if not guess:
                raise ProtocolValidationError(
                    "invalid_unknown_terms", "UnknownTerms guess must not be empty"
                )
            reason = str(item.get("reason") or "").strip()[:600]
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                raise ProtocolValidationError(
                    "invalid_unknown_terms", "UnknownTerms confidence must be a number"
                ) from None
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ProtocolValidationError(
                    "invalid_unknown_terms",
                    "UnknownTerms confidence must be between 0 and 1",
                )
            result.append(
                UnknownTerm(
                    word=word,
                    guess=guess,
                    confidence=confidence,
                    reason=reason,
                )
            )
        return tuple(result)

    def _extract_image_cache(self, raw: str) -> tuple[ImageCache, ...]:
        """Extract plain-text image transcriptions from anywhere in the output.

        The cache is free-form text (combined image meaning + brief content);
        invalid or absent cache data is discarded without blocking the reply.

        Args:
            raw: Full model output.

        Returns:
            Parsed plain-text image cache entries.
        """
        matches = list(_IMAGECACHE_TAG_RE.finditer(raw))[:16]
        result: list[ImageCache] = []
        seen: set[str] = set()
        for match in matches:
            text = match.group("value").strip()
            if not text or text in seen:
                continue
            clean = text[:600]
            if not clean:
                continue
            seen.add(clean)
            result.append(ImageCache(text=clean))
        return tuple(result)

    def _extract_messages(self, raw: str) -> tuple[str, ...]:
        """Extract outbound messages from Messages/Reply containers anywhere.

        Message bodies are taken verbatim (tags inside are not parsed).
        Missing containers yield no messages (nothing is sent).

        Args:
            raw: Full model output.

        Returns:
            Outbound message bodies in order.
        """
        messages: list[str] = []
        for match in _MESSAGES_TAG_RE.finditer(raw):
            for message_match in _MESSAGE_TAG_RE.finditer(match.group("inner")):
                body = message_match.group("body")
                if body.strip():
                    messages.append(body)
        if messages:
            return tuple(messages)
        # 兼容旧 <Reply> 容器
        for match in _REPLY_BLOCK_TAG_RE.finditer(raw):
            for message_match in _MESSAGE_TAG_RE.finditer(match.group("inner")):
                body = message_match.group("body")
                if body.strip():
                    messages.append(body)
        if messages:
            return tuple(messages)
        return ()
