from __future__ import annotations

import json
import math
from typing import Any

from ..config import PluginConfig
from ..domain.errors import ProtocolValidationError
from ..domain.models import Action, ProtocolDecision, UnknownTerm


class ProtocolParser:
    def __init__(self, config: PluginConfig) -> None:
        self._config = config

    def parse(self, raw_output: str) -> ProtocolDecision:
        raw = raw_output or ""
        if not raw.strip():
            raise ProtocolValidationError("empty_output", "LLM returned no final text")

        normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        if len(lines) < 3 or lines[2] != "---":
            raise ProtocolValidationError(
                "invalid_control_header",
                "Response must start with Action, UnknownTerms, and --- lines",
            )
        if not lines[0].startswith("Action: "):
            raise ProtocolValidationError(
                "missing_action", "First line must start with Action: "
            )
        if not lines[1].startswith("UnknownTerms: "):
            raise ProtocolValidationError(
                "missing_unknown_terms",
                "Second line must start with UnknownTerms: ",
            )

        action_value = lines[0].removeprefix("Action: ").strip()
        try:
            action = Action(action_value)
        except ValueError as exc:
            raise ProtocolValidationError(
                "invalid_action",
                f"Unsupported Action value: {action_value or '<empty>'}",
            ) from exc

        unknown_terms_raw = lines[1].removeprefix("UnknownTerms: ").strip()
        try:
            unknown_terms_payload = json.loads(unknown_terms_raw)
        except json.JSONDecodeError as exc:
            raise ProtocolValidationError(
                "invalid_unknown_terms_json", str(exc)
            ) from exc
        unknown_terms = self._parse_unknown_terms(unknown_terms_payload)
        reply_text = "\n".join(lines[3:])

        if action is Action.REPLY and not reply_text.strip():
            raise ProtocolValidationError(
                "reply_missing_text", "Reply action requires response text after ---"
            )
        if action is Action.NO_REPLY:
            if not self._config.no_reply_enabled:
                raise ProtocolValidationError(
                    "no_reply_disabled", "No Reply is disabled by plugin configuration"
                )
            if reply_text.strip():
                raise ProtocolValidationError(
                    "no_reply_has_text", "No Reply requires an empty response body"
                )

        return ProtocolDecision(
            action=action,
            messages=(reply_text,) if action is Action.REPLY else (),
            unknown_terms=unknown_terms,
        )

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
