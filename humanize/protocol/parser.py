from __future__ import annotations

import json
import math
import re
from typing import Any

from ..config import PluginConfig
from ..domain.errors import ProtocolValidationError
from ..domain.models import Action, ImageCache, ProtocolDecision, UnknownTerm

_RESPONSE_PATTERN = re.compile(
    r"\A"
    r"(?P<action>[^\r\n]*)(?:\r\n|\r|\n)"
    r"(?P<unknown_terms>[^\r\n]*)"
    r"(?:(?:\r\n|\r|\n)(?P<body>[\s\S]*))?"
    r"\Z"
)
_CONTROL_MARKERS = ("action:", "unknownterms:", "<action", "<unknownterms")
_ACTION_TAG_PATTERN = re.compile(
    r"<\s*action\s*>\s*(?P<value>.*?)\s*<\s*/\s*action\s*>",
    re.IGNORECASE,
)
_LEGACY_ACTION_PATTERN = re.compile(r"action\s*:\s*(?P<value>.*)", re.IGNORECASE)
_REPLY_BLOCK_PATTERN = re.compile(r"\A<Reply>(?P<inner>[\s\S]*)</Reply>\Z")
_MESSAGE_PATTERN = re.compile(r"<Message>(?P<body>[\s\S]*?)</Message>")
_PROTOCOL_BODY_MARKER_PATTERN = re.compile(
    r"<\s*/?\s*(?:Action|UnknownTerms|ImageCache|Reply|Message)\b",
    re.IGNORECASE,
)
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

        match = _RESPONSE_PATTERN.fullmatch(raw)
        if match is None:
            raise ProtocolValidationError(
                "invalid_control_header",
                "Response must start with Action and UnknownTerms tags",
            )
        action_line = match.group("action")
        unknown_terms_line = match.group("unknown_terms")
        if not action_line.startswith("<Action>") or not action_line.endswith(
            "</Action>"
        ):
            raise ProtocolValidationError(
                "missing_action", "First line must be an Action tag"
            )
        if not unknown_terms_line.startswith(
            "<UnknownTerms>"
        ) or not unknown_terms_line.endswith("</UnknownTerms>"):
            raise ProtocolValidationError(
                "missing_unknown_terms",
                "Second line must be an UnknownTerms tag",
            )

        action_value = action_line[len("<Action>") : -len("</Action>")].strip()
        try:
            action = Action(action_value)
        except ValueError as exc:
            raise ProtocolValidationError(
                "invalid_action",
                f"Unsupported Action value: {action_value or '<empty>'}",
            ) from exc

        unknown_terms_raw = unknown_terms_line[
            len("<UnknownTerms>") : -len("</UnknownTerms>")
        ].strip()
        try:
            unknown_terms_payload = json.loads(unknown_terms_raw)
        except json.JSONDecodeError as exc:
            raise ProtocolValidationError(
                "invalid_unknown_terms_json", str(exc)
            ) from exc
        unknown_terms = self._parse_unknown_terms(unknown_terms_payload)
        image_cache, reply_text = self._extract_image_cache(match.group("body") or "")
        messages = self._parse_reply_body(reply_text)

        if action is Action.REPLY and not messages:
            raise ProtocolValidationError(
                "reply_missing_text",
                "Reply action requires response text after the control header",
            )
        if action is Action.NO_REPLY:
            if not self._config.no_reply_enabled:
                raise ProtocolValidationError(
                    "no_reply_disabled", "No Reply is disabled by plugin configuration"
                )
            if messages:
                raise ProtocolValidationError(
                    "no_reply_has_text", "No Reply requires an empty response body"
                )

        return ProtocolDecision(
            action=action,
            messages=messages if action is Action.REPLY else (),
            unknown_terms=unknown_terms,
            image_cache=image_cache,
        )

    def _extract_image_cache(
        self, reply_body: str
    ) -> tuple[tuple[ImageCache, ...], str]:
        """Remove the optional same-turn image cache line from the visible body.

        Args:
            reply_body: Text following the required Action and UnknownTerms lines.

        Returns:
            Parsed bounded image cache and the remaining visible reply body. Invalid
            cache data is discarded instead of blocking a valid user-visible reply.
        """
        if not reply_body.startswith("<ImageCache>"):
            return (), reply_body
        line_match = re.fullmatch(
            r"(?P<line>[^\r\n]*)(?:\r\n|\r|\n|$)(?P<remaining>[\s\S]*)",
            reply_body,
        )
        if line_match is None:
            return (), ""
        first_line = line_match.group("line")
        remaining = line_match.group("remaining")
        if not first_line.endswith("</ImageCache>"):
            return (), remaining
        payload = first_line[len("<ImageCache>") : -len("</ImageCache>")].strip()
        try:
            raw_items = json.loads(payload)
        except json.JSONDecodeError:
            return (), remaining
        if not isinstance(raw_items, list) or len(raw_items) > 16:
            return (), remaining
        result: list[ImageCache] = []
        seen: set[int] = set()
        for item in raw_items:
            if not isinstance(item, dict) or set(item) - {
                "index",
                "description",
                "ocr",
                "objects",
            }:
                return (), remaining
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                return (), remaining
            description = item.get("description", "")
            ocr = item.get("ocr", "")
            objects = item.get("objects", [])
            if (
                index <= 0
                or index > 16
                or index in seen
                or not isinstance(description, str)
                or not isinstance(ocr, str)
                or not isinstance(objects, list)
                or len(objects) > 16
                or not all(isinstance(value, str) for value in objects)
            ):
                return (), remaining
            clean_description = description.strip()[:600]
            clean_ocr = ocr.strip()[:600]
            clean_objects = tuple(
                value.strip()[:80] for value in objects if value.strip()
            )
            if not (clean_description or clean_ocr or clean_objects):
                return (), remaining
            seen.add(index)
            result.append(
                ImageCache(
                    index=index,
                    description=clean_description,
                    ocr=clean_ocr,
                    objects=clean_objects,
                )
            )
        return tuple(result), remaining

    def _parse_reply_body(self, reply_body: str) -> tuple[str, ...]:
        """Parse one plain body or a Reply block with Message children.

        Args:
            reply_body: Text after the two control header lines.

        Returns:
            Outbound message bodies in their original order.

        Raises:
            ProtocolValidationError: If a Reply block contains malformed or empty
                Message children.
        """
        block = _REPLY_BLOCK_PATTERN.fullmatch(reply_body.strip())
        if block is None:
            if _PROTOCOL_BODY_MARKER_PATTERN.search(reply_body):
                raise ProtocolValidationError(
                    "invalid_reply_block",
                    "Protocol control tags must not appear in a plain reply body",
                )
            return self._parse_plain_reply_body(reply_body)

        inner = block.group("inner")
        messages: list[str] = []
        cursor = 0
        for message_match in _MESSAGE_PATTERN.finditer(inner):
            if inner[cursor : message_match.start()].strip():
                raise ProtocolValidationError(
                    "invalid_reply_block", "Reply may contain only Message tags"
                )
            message = message_match.group("body")
            if not message.strip():
                raise ProtocolValidationError(
                    "empty_message", "Reply contains an empty Message"
                )
            if _PROTOCOL_BODY_MARKER_PATTERN.search(message):
                raise ProtocolValidationError(
                    "invalid_reply_block",
                    "Message text must not contain protocol control tags",
                )
            messages.append(message)
            cursor = message_match.end()
        if inner[cursor:].strip():
            raise ProtocolValidationError(
                "invalid_reply_block", "Reply may contain only Message tags"
            )
        return tuple(messages)

    def _parse_plain_reply_body(self, reply_body: str) -> tuple[str, ...]:
        """Preserve formatted text and recover short untagged chat messages.

        A model occasionally uses a newline as a message boundary even though the
        protocol requires ``Reply/Message`` tags. Only consecutive short lines
        without recognizable formatting are safe to recover as separate outbound
        messages. Everything else stays intact as one message.

        Args:
            reply_body: Plain text following the control header.

        Returns:
            One preserved body or recovered short chat messages.
        """
        if not reply_body.strip():
            return ()
        lines = reply_body.splitlines()
        if len(lines) < 2 or any(not line.strip() for line in lines):
            return (reply_body,)
        messages = tuple(line.strip() for line in lines)
        if (
            any(len(message) > self._config.max_message_chars for message in messages)
            or any("```" in line or "~~~" in line for line in lines)
            or any(
                line.lstrip().startswith(("{", "[", "<"))
                or _STRUCTURED_REPLY_LINE_PATTERN.match(line)
                for line in lines
            )
        ):
            return (reply_body,)
        return messages

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
        match = _RESPONSE_PATTERN.fullmatch(raw)
        if match is not None:
            body = match.group("body") or ""
            action_line = match.group("action").strip()
            unknown_terms_line = match.group("unknown_terms").strip()
            looks_like_header = (
                "action" in action_line.casefold()
                or "reply" in action_line.casefold()
                or action_line.startswith("<")
                or unknown_terms_line.casefold().startswith(
                    ("unknownterms", "<unknownterms")
                )
            )
            if not looks_like_header:
                required_action = (
                    Action.REPLY.value if raw.strip() else Action.NO_REPLY.value
                )
                return raw, required_action
            action_value = ""
            action_match = _ACTION_TAG_PATTERN.fullmatch(action_line)
            if action_match is None:
                action_match = _LEGACY_ACTION_PATTERN.fullmatch(action_line)
            if action_match is not None:
                normalized_action = " ".join(
                    action_match.group("value").strip().casefold().split()
                )
                action_value = {
                    "reply": Action.REPLY.value,
                    "no reply": Action.NO_REPLY.value,
                }.get(normalized_action, "")
                if not action_value:
                    return None
            elif (
                "action" in action_line.casefold()
                or "reply" in action_line.casefold()
                or action_line.startswith("<")
            ):
                return None

            if action_value:
                if action_value == Action.REPLY.value and not body.strip():
                    return None
                if action_value == Action.NO_REPLY.value and body.strip():
                    return None
                return body, action_value
            required_action = (
                Action.REPLY.value if body.strip() else Action.NO_REPLY.value
            )
            return body, required_action

        first_lines = re.split(r"\r\n|\r|\n", raw, maxsplit=3)[:3]
        if any(
            line.lstrip().lower().startswith(_CONTROL_MARKERS) for line in first_lines
        ):
            return None
        required_action = Action.REPLY.value if raw.strip() else Action.NO_REPLY.value
        return raw, required_action

    @staticmethod
    def compose_repaired_response(repair_output: str, original_body: str) -> str:
        """Combine a strict header-only repair with the untouched original body.

        Args:
            repair_output: Model output expected to contain exactly two header lines.
            original_body: Original body extracted before the repair call.

        Returns:
            A full response suitable for normal protocol validation.

        Raises:
            ProtocolValidationError: If the repair contains a malformed header or any
                response body.
        """
        match = _RESPONSE_PATTERN.fullmatch(repair_output or "")
        if match is None:
            raise ProtocolValidationError(
                "invalid_repair_header",
                "Protocol repair must contain exactly two control header lines",
            )
        if match.group("body") not in {None, ""}:
            raise ProtocolValidationError(
                "repair_has_body", "Protocol repair must not contain response text"
            )

        action_line = match.group("action")
        unknown_terms_line = match.group("unknown_terms")
        if not action_line.startswith("<Action>") or not action_line.endswith(
            "</Action>"
        ):
            raise ProtocolValidationError(
                "missing_action", "First repair line must be an Action tag"
            )
        if not unknown_terms_line.startswith(
            "<UnknownTerms>"
        ) or not unknown_terms_line.endswith("</UnknownTerms>"):
            raise ProtocolValidationError(
                "missing_unknown_terms",
                "Second repair line must be an UnknownTerms tag",
            )

        header = f"{action_line}\n{unknown_terms_line}"
        return f"{header}\n{original_body}" if original_body else header

    def _parse_unknown_terms(self, payload: Any) -> tuple[UnknownTerm, ...]:
        if not isinstance(payload, list):
            raise ProtocolValidationError(
                "invalid_unknown_terms", "UnknownTerms must be a JSON array"
            )
        if len(payload) > self._config.max_unknown_terms:
            raise ProtocolValidationError(
                "too_many_unknown_terms", "UnknownTerms exceeds the configured limit"
            )

        result: list[UnknownTerm] = []
        required_fields = {"word", "guess", "confidence", "reason"}
        for item in payload:
            if not isinstance(item, dict) or set(item) != required_fields:
                raise ProtocolValidationError(
                    "invalid_unknown_term",
                    "Each unknown term requires word, guess, confidence, and reason",
                )
            if not all(
                isinstance(item[field], str) for field in ("word", "guess", "reason")
            ):
                raise ProtocolValidationError(
                    "invalid_unknown_term",
                    "Unknown term word, guess, and reason must be strings",
                )
            word = item["word"].strip()
            guess = item["guess"].strip()
            reason = item["reason"].strip()
            confidence_raw = item["confidence"]
            if not word or not guess or not reason:
                raise ProtocolValidationError(
                    "empty_unknown_term_field",
                    "Unknown term text fields must not be empty",
                )
            if isinstance(confidence_raw, bool):
                raise ProtocolValidationError(
                    "invalid_confidence", "Confidence must be a number from 0 to 1"
                )
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError) as exc:
                raise ProtocolValidationError(
                    "invalid_confidence", "Confidence must be a number from 0 to 1"
                ) from exc
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ProtocolValidationError(
                    "invalid_confidence",
                    "Confidence must be a finite number from 0 to 1",
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
