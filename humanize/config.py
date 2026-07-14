from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _as_string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip() for item in value if str(item).strip())
    )


@dataclass(frozen=True, slots=True)
class PluginConfig:
    enabled: bool = True
    default_rule_enabled: bool = True
    admin_name: str = "管理员"
    admin_qq_ids: tuple[str, ...] = ()
    max_message_chars: int = 10
    max_reply_messages: int = 12
    split_long_messages: bool = True
    protocol_enabled: bool = True
    protocol_version: int = 1
    protocol_max_output_chars: int = 20_000
    protocol_raw_log_chars: int = 4_000
    protocol_log_retention_days: int = 7
    max_xml_nodes: int = 128
    max_xml_depth: int = 8
    max_unknown_terms: int = 8
    no_reply_enabled: bool = True
    jargon_enabled: bool = True
    min_confidence_for_injection: float = 0.75
    max_injected_jargons: int = 5
    max_injection_tokens: int = 256
    max_evidence_per_entry: int = 20
    max_term_chars: int = 32

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PluginConfig:
        data = raw or {}
        return cls(
            enabled=_as_bool(data.get("enabled"), True),
            default_rule_enabled=_as_bool(data.get("default_rule_enabled"), True),
            admin_name=str(data.get("admin_name") or "管理员").strip() or "管理员",
            admin_qq_ids=_as_string_list(data.get("admin_qq_ids")),
            max_message_chars=_as_int(data.get("max_message_chars"), 10, 1, 200),
            max_reply_messages=_as_int(data.get("max_reply_messages"), 12, 1, 50),
            split_long_messages=_as_bool(data.get("split_long_messages"), True),
            protocol_enabled=_as_bool(data.get("protocol_enabled"), True),
            protocol_version=_as_int(data.get("protocol_version"), 1, 1, 99),
            protocol_max_output_chars=_as_int(
                data.get("protocol_max_output_chars"), 20_000, 1_000, 100_000
            ),
            protocol_raw_log_chars=_as_int(
                data.get("protocol_raw_log_chars"), 4_000, 256, 20_000
            ),
            protocol_log_retention_days=_as_int(
                data.get("protocol_log_retention_days"), 7, 1, 365
            ),
            max_xml_nodes=_as_int(data.get("max_xml_nodes"), 128, 16, 1_024),
            max_xml_depth=_as_int(data.get("max_xml_depth"), 8, 3, 32),
            max_unknown_terms=_as_int(data.get("max_unknown_terms"), 8, 0, 50),
            no_reply_enabled=_as_bool(data.get("no_reply_enabled"), True),
            jargon_enabled=_as_bool(data.get("jargon_enabled"), True),
            min_confidence_for_injection=_as_float(
                data.get("min_confidence_for_injection"), 0.75, 0.0, 1.0
            ),
            max_injected_jargons=_as_int(data.get("max_injected_jargons"), 5, 0, 50),
            max_injection_tokens=_as_int(
                data.get("max_injection_tokens"), 256, 0, 4_096
            ),
            max_evidence_per_entry=_as_int(
                data.get("max_evidence_per_entry"), 20, 1, 500
            ),
            max_term_chars=_as_int(data.get("max_term_chars"), 32, 2, 128),
        )

    @property
    def injection_char_budget(self) -> int:
        return self.max_injection_tokens * 4

    def data_path(self) -> Path:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        return Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_humanize"

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "default_rule_enabled": self.default_rule_enabled,
            "admin_name": self.admin_name,
            "admin_qq_ids": list(self.admin_qq_ids),
            "max_message_chars": self.max_message_chars,
            "max_reply_messages": self.max_reply_messages,
            "split_long_messages": self.split_long_messages,
            "protocol_enabled": self.protocol_enabled,
            "protocol_version": self.protocol_version,
            "protocol_log_retention_days": self.protocol_log_retention_days,
            "no_reply_enabled": self.no_reply_enabled,
            "jargon_enabled": self.jargon_enabled,
            "min_confidence_for_injection": self.min_confidence_for_injection,
            "max_injected_jargons": self.max_injected_jargons,
        }
