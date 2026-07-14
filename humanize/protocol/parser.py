from __future__ import annotations

import math
from xml.etree import ElementTree as ET

from ..config import PluginConfig
from ..domain.errors import ProtocolValidationError
from ..domain.models import Action, ProtocolDecision, UnknownTerm
from .splitter import enforce_message_limits


class ProtocolParser:
    def __init__(self, config: PluginConfig) -> None:
        self._config = config

    def parse(self, raw_output: str) -> ProtocolDecision:
        raw = raw_output or ""
        if not raw.strip():
            raise ProtocolValidationError("empty_output", "LLM returned no final text")
        if len(raw) > self._config.protocol_max_output_chars:
            raise ProtocolValidationError(
                "output_too_long", "LLM output exceeds the protocol size limit"
            )
        upper = raw.upper()
        if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
            raise ProtocolValidationError(
                "forbidden_xml_declaration", "DTD and entity declarations are forbidden"
            )
        if raw.lstrip().startswith("```"):
            raise ProtocolValidationError(
                "markdown_wrapper", "XML must not be wrapped in a Markdown code fence"
            )

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ProtocolValidationError("malformed_xml", str(exc)) from exc

        self._validate_tree_limits(root)
        if root.tag != "AgentResponse":
            raise ProtocolValidationError(
                "invalid_root", "Root node must be AgentResponse"
            )
        if root.attrib != {"version": str(self._config.protocol_version)}:
            raise ProtocolValidationError(
                "invalid_version", "AgentResponse has an invalid protocol version"
            )
        self._require_whitespace(root.text, "AgentResponse contains raw text")

        children = list(root)
        tags = [child.tag for child in children]
        if tags != ["Action", "UnknownTerms", "Reply"]:
            raise ProtocolValidationError(
                "invalid_children",
                "AgentResponse must contain Action, UnknownTerms, and Reply exactly once in order",
            )
        for child in children:
            self._require_whitespace(child.tail, f"Unexpected text after {child.tag}")

        action = self._parse_action(children[0])
        unknown_terms = self._parse_unknown_terms(children[1])
        messages = self._parse_reply(children[2])

        if action is Action.REPLY and not messages:
            raise ProtocolValidationError(
                "reply_missing_message", "Reply action requires at least one Message"
            )
        if action is Action.NO_REPLY:
            if not self._config.no_reply_enabled:
                raise ProtocolValidationError(
                    "no_reply_disabled", "No Reply is disabled by plugin configuration"
                )
            if messages:
                raise ProtocolValidationError(
                    "no_reply_has_message", "No Reply requires an empty Reply node"
                )

        return ProtocolDecision(
            action=action,
            messages=messages,
            unknown_terms=unknown_terms,
        )

    def _parse_action(self, node: ET.Element) -> Action:
        self._require_leaf(node, "Action")
        value = (node.text or "").strip()
        try:
            return Action(value)
        except ValueError as exc:
            raise ProtocolValidationError(
                "invalid_action", f"Unsupported Action value: {value or '<empty>'}"
            ) from exc

    def _parse_unknown_terms(self, node: ET.Element) -> tuple[UnknownTerm, ...]:
        if node.attrib:
            raise ProtocolValidationError(
                "unknown_terms_attributes", "UnknownTerms must not have attributes"
            )
        self._require_whitespace(node.text, "UnknownTerms contains raw text")
        children = list(node)
        if len(children) > self._config.max_unknown_terms:
            raise ProtocolValidationError(
                "too_many_unknown_terms", "UnknownTerms exceeds the configured limit"
            )

        result: list[UnknownTerm] = []
        for child in children:
            if child.tag != "UnknownTerm" or child.attrib:
                raise ProtocolValidationError(
                    "invalid_unknown_term",
                    "UnknownTerms may only contain UnknownTerm nodes",
                )
            self._require_whitespace(child.text, "UnknownTerm contains raw text")
            self._require_whitespace(child.tail, "Unexpected text after UnknownTerm")
            fields = list(child)
            if [field.tag for field in fields] != [
                "Word",
                "Guess",
                "Confidence",
                "Reason",
            ]:
                raise ProtocolValidationError(
                    "invalid_unknown_term_fields",
                    "UnknownTerm requires Word, Guess, Confidence, and Reason in order",
                )
            values: list[str] = []
            for field in fields:
                self._require_leaf(field, field.tag)
                self._require_whitespace(
                    field.tail, f"Unexpected text after {field.tag}"
                )
                value = (field.text or "").strip()
                if not value:
                    raise ProtocolValidationError(
                        "empty_unknown_term_field", f"{field.tag} must not be empty"
                    )
                values.append(value)
            try:
                confidence = float(values[2])
            except ValueError as exc:
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
                    word=values[0],
                    guess=values[1],
                    confidence=confidence,
                    reason=values[3],
                )
            )
        return tuple(result)

    def _parse_reply(self, node: ET.Element) -> tuple[str, ...]:
        if node.attrib:
            raise ProtocolValidationError(
                "reply_attributes", "Reply must not have attributes"
            )
        self._require_whitespace(node.text, "Reply may only contain Message nodes")
        messages: list[str] = []
        for child in list(node):
            if child.tag != "Message":
                raise ProtocolValidationError(
                    "invalid_reply_child", "Reply may only contain Message nodes"
                )
            self._require_leaf(child, "Message")
            self._require_whitespace(child.tail, "Unexpected text after Message")
            messages.append((child.text or "").strip())
        return enforce_message_limits(
            messages,
            max_chars=self._config.max_message_chars,
            max_messages=self._config.max_reply_messages,
            split_long_messages=self._config.split_long_messages,
        )

    def _validate_tree_limits(self, root: ET.Element) -> None:
        stack = [(root, 1)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > self._config.max_xml_nodes:
                raise ProtocolValidationError(
                    "xml_too_many_nodes", "XML node count exceeds the configured limit"
                )
            if depth > self._config.max_xml_depth:
                raise ProtocolValidationError(
                    "xml_too_deep", "XML nesting depth exceeds the configured limit"
                )
            stack.extend((child, depth + 1) for child in list(node))

    @staticmethod
    def _require_leaf(node: ET.Element, name: str) -> None:
        if node.attrib or list(node):
            raise ProtocolValidationError(
                "invalid_leaf", f"{name} must be a plain text node without attributes"
            )

    @staticmethod
    def _require_whitespace(value: str | None, detail: str) -> None:
        if value and value.strip():
            raise ProtocolValidationError("unexpected_text", detail)
