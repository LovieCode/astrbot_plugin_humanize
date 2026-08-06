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
_LEGACY_ACTION_PATTERN = re.compile(r"action\s*:\s*(?P<value>.*)", re.IGNORECASE)

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

    def parse(self, raw_output: str) -> ProtocolDecision:
        raw = raw_output or ""
        if not raw.strip():
            raise ProtocolValidationError("empty_output", "LLM returned no final text")

        action_value = self._extract_action(raw)
        if action_value is None:
            raise ProtocolValidationError(
                "missing_action",
                "Response must contain an <Action> tag",
            )
        try:
            action = Action(action_value)
        except ValueError as exc:
            raise ProtocolValidationError(
                "invalid_action",
                f"Unsupported Action value: {action_value or '<empty>'}",
            ) from exc

        unknown_terms = self._extract_unknown_terms(raw)

        image_cache = self._extract_image_cache(raw)

        messages = self._extract_messages(raw)

        if action is Action.REPLY and not messages:
            # 协议提示词要求 Reply 必须带 Messages；解析器宽容：没有消息就不发送。
            pass
        if action is Action.NO_REPLY:
            if not self._config.no_reply_enabled:
                raise ProtocolValidationError(
                    "no_reply_disabled", "No Reply is disabled by plugin configuration"
                )
            if messages:
                raise ProtocolValidationError(
                    "no_reply_has_text", "No Reply requires an empty response body"
                )
            messages = ()

        return ProtocolDecision(
            action=action,
            messages=messages if action is Action.REPLY else (),
            unknown_terms=unknown_terms,
            image_cache=image_cache,
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
            Parsed unknown terms, or () when the tag is absent or invalid.
        """
        match = _UNKNOWN_TERMS_TAG_RE.search(raw)
        if match is None:
            return ()
        payload_raw = match.group("value").strip()
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

    # ---------- 修复辅助（新版宽松格式） ----------

    @staticmethod
    def extract_repair_body(raw_output: str) -> str | None:
        """Extract the untouched body when a control header can be repaired safely.

        Args:
            raw_output: Original malformed model output.

        Returns:
            The exact original body, or None when a partial header cannot be split
            safely from user-visible text.
        """
        candidate = ProtocolParser.extract_repair_candidate(raw_output)
        return candidate[0] if candidate is not None else None

    @staticmethod
    def extract_repair_candidate(raw_output: str) -> tuple[str, str] | None:
        """Extract an untouched body and a non-conflicting required action.

        Args:
            raw_output: Original malformed model output.

        Returns:
            The exact original body and required Action value, or None when a
            recognizable Action conflicts with the body or the split is unsafe.
        """
        raw = raw_output or ""
        action_match = _ACTION_TAG_RE.search(raw)
        if action_match is not None:
            action_value = action_match.group("value").strip()
            try:
                action = Action(action_value)
            except ValueError:
                return None
            # No Reply 冲突（带正文）无需修复：直接返回 None 让上层 block
            if action is Action.NO_REPLY:
                return None
            # 去掉已识别的 Action / UnknownTerms 标签，其余为 body
            body = raw[: action_match.start()] + raw[action_match.end() :]
            unknown_match = _UNKNOWN_TERMS_TAG_RE.search(body)
            if unknown_match is not None:
                body = body[: unknown_match.start()] + body[unknown_match.end() :]
            return body.strip(), action.value
        legacy = _LEGACY_ACTION_PATTERN.search(raw)
        if legacy is not None:
            action_value = legacy.group("value").strip()
            try:
                action = Action(action_value)
            except ValueError:
                return None
            if action is Action.NO_REPLY:
                return None
            body = raw[: legacy.start()] + raw[legacy.end() :]
            legacy_unknown = re.search(r"(?im)^\s*unknownterms\s*:\s*[^\r\n]*", body)
            if legacy_unknown is not None:
                body = body[: legacy_unknown.start()] + body[legacy_unknown.end() :]
            return body.strip(), action.value
        return None

    @staticmethod
    def compose_repaired_response(repair_output: str, original_body: str) -> str:
        """Combine a repair header with the untouched original body.

        The repair output needs at least an Action tag (UnknownTerms optional);
        the original body (Messages/ImageCache/whatever) is appended as-is.

        Args:
            repair_output: Model output expected to contain a valid Action tag.
            original_body: Original body extracted before the repair call.

        Returns:
            A full response suitable for normal protocol validation.

        Raises:
            ProtocolValidationError: If the repair has no Action tag or carries
                its own visible body.
        """
        action_match = _ACTION_TAG_RE.search(repair_output or "")
        if action_match is None:
            raise ProtocolValidationError(
                "missing_action", "Protocol repair must contain an Action tag"
            )
        action_value = action_match.group("value").strip()
        try:
            Action(action_value)
        except ValueError as exc:
            raise ProtocolValidationError(
                "invalid_action", f"Unsupported Action value: {action_value}"
            ) from exc

        unknown_match = _UNKNOWN_TERMS_TAG_RE.search(repair_output or "")
        unknown_line = (
            unknown_match.group(0)
            if unknown_match is not None
            else "<UnknownTerms>[]</UnknownTerms>"
        )

        # 重组：Action + UnknownTerms + 原 body
        lines = [
            action_match.group(0),
            unknown_line,
        ]
        if original_body.strip():
            lines.append(original_body.strip())
        return "\n".join(lines)
