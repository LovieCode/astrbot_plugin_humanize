from __future__ import annotations

import re
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


def _as_identifier(value: Any, default: str) -> str:
    candidate = str(value or default).strip()
    if candidate and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", candidate):
        return candidate
    return default


def _as_provider_id(value: Any) -> str:
    candidate = str(value or "").strip()
    if (
        candidate
        and len(candidate) <= 200
        and not any(
            character.isspace() or ord(character) < 32 for character in candidate
        )
    ):
        return candidate
    return ""


def _as_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """Validate an enumerated configuration value."""
    candidate = str(value or "").strip().lower()
    return candidate if candidate in allowed else default


@dataclass(frozen=True, slots=True)
class PluginConfig:
    enabled: bool = True
    default_rule_enabled: bool = True
    admin_name: str = "管理员"
    admin_qq_ids: tuple[str, ...] = ()
    max_message_chars: int = 10
    message_interval_seconds: float = 0.8
    protocol_enabled: bool = True
    protocol_injection_mode: str = "user"
    protocol_repair_retry_enabled: bool = True
    protocol_raw_log_chars: int = 4_000
    protocol_log_retention_days: int = 7
    max_unknown_terms: int = 8
    max_messages_per_reply: int = 5
    no_reply_enabled: bool = True
    jargon_enabled: bool = True
    min_confidence_for_injection: float = 0.75
    max_injected_jargons: int = 5
    max_injection_tokens: int = 256
    max_evidence_per_entry: int = 20
    max_term_chars: int = 32
    memory_enabled: bool = True
    memory_auto_extract_enabled: bool = True
    memory_extraction_provider_id: str = ""
    memory_embedding_provider_id: str = ""
    memory_rerank_provider_id: str = ""
    image_transcription_provider_id: str = ""
    image_cache_enabled: bool = True
    image_cache_max_entries: int = 100
    image_cache_max_sticker_entries: int = 500
    memory_identity_secret_env: str = "HUMANIZE_MEMORY_SECRET"
    memory_recall_timeout_seconds: float = 1.5
    memory_auto_activate_confidence: float = 0.88
    memory_candidate_min_confidence: float = 0.55
    memory_recall_limit: int = 5
    memory_recall_score_threshold: float = 0.2
    memory_recall_max_chars: int = 2_500
    memory_decay_half_life_days: int = 120
    memory_decay_forget_confidence: float = 0.15
    memory_contradiction_penalty: float = 0.5
    memory_related_boost: float = 0.15
    context_summary_enabled: bool = True
    context_summary_max_chars: int = 2_400
    context_summary_timeout_seconds: float = 30.0
    memory_intent_analysis_enabled: bool = False
    memory_extract_batch_turns: int = 8
    memory_extract_idle_seconds: int = 180
    memory_job_max_attempts: int = 5
    reply_examples_enabled: bool = True
    reply_examples_limit: int = 3
    reply_examples_max_chars: int = 2_000
    reply_examples_min_quality: float = 0.7
    reply_examples_recall_score_threshold: float = 0.2
    proactive_window_initial_seconds: int = 10
    proactive_window_max_seconds: int = 300
    proactive_keywords: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PluginConfig:
        data = dict(raw or {})
        for section_name in ("general", "reply_control", "memory", "proactive"):
            section = data.get(section_name)
            if isinstance(section, Mapping):
                data.update(section)

        reply_examples = data.get("reply_examples")
        if isinstance(reply_examples, Mapping):
            data.update(reply_examples)

        protocol_injection_mode = (
            str(data.get("protocol_injection_mode") or "user").strip().lower()
        )
        if protocol_injection_mode not in {"user", "both"}:
            protocol_injection_mode = "user"
        return cls(
            enabled=_as_bool(data.get("enabled"), True),
            default_rule_enabled=_as_bool(data.get("default_rule_enabled"), True),
            admin_name=str(data.get("admin_name") or "管理员").strip() or "管理员",
            admin_qq_ids=_as_string_list(data.get("admin_qq_ids")),
            max_message_chars=_as_int(data.get("max_message_chars"), 10, 1, 200),
            message_interval_seconds=_as_float(
                data.get("message_interval_seconds"), 0.8, 0.0, 10.0
            ),
            protocol_enabled=_as_bool(data.get("protocol_enabled"), True),
            protocol_injection_mode=protocol_injection_mode,
            protocol_repair_retry_enabled=_as_bool(
                data.get("protocol_repair_retry_enabled"), True
            ),
            protocol_raw_log_chars=_as_int(
                data.get("protocol_raw_log_chars"), 4_000, 256, 20_000
            ),
            protocol_log_retention_days=_as_int(
                data.get("protocol_log_retention_days"), 7, 1, 365
            ),
            max_unknown_terms=_as_int(data.get("max_unknown_terms"), 8, 0, 50),
            max_messages_per_reply=_as_int(
                data.get("max_messages_per_reply"), 5, 1, 20
            ),
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
            memory_enabled=_as_bool(data.get("memory_enabled"), True),
            memory_auto_extract_enabled=_as_bool(
                data.get("memory_auto_extract_enabled"), True
            ),
            memory_extraction_provider_id=_as_provider_id(
                data.get("memory_extraction_provider_id")
            ),
            memory_embedding_provider_id=_as_provider_id(
                data.get("memory_embedding_provider_id")
            ),
            memory_rerank_provider_id=_as_provider_id(
                data.get("memory_rerank_provider_id")
            ),
            image_transcription_provider_id=_as_provider_id(
                data.get("image_transcription_provider_id")
            ),
            image_cache_enabled=_as_bool(data.get("image_cache_enabled"), True),
            image_cache_max_entries=_as_int(
                data.get("image_cache_max_entries"), 100, 1, 10_000
            ),
            image_cache_max_sticker_entries=_as_int(
                data.get("image_cache_max_sticker_entries"), 500, 1, 50_000
            ),
            memory_identity_secret_env=_as_identifier(
                data.get("memory_identity_secret_env"), "HUMANIZE_MEMORY_SECRET"
            ),
            memory_recall_timeout_seconds=_as_float(
                data.get("memory_recall_timeout_seconds"), 1.5, 0.2, 10.0
            ),
            memory_auto_activate_confidence=_as_float(
                data.get("memory_auto_activate_confidence"), 0.88, 0.5, 1.0
            ),
            memory_candidate_min_confidence=_as_float(
                data.get("memory_candidate_min_confidence"), 0.55, 0.0, 1.0
            ),
            memory_recall_limit=_as_int(data.get("memory_recall_limit"), 5, 1, 20),
            memory_recall_score_threshold=_as_float(
                data.get("memory_recall_score_threshold"), 0.2, 0.0, 1.0
            ),
            memory_recall_max_chars=_as_int(
                data.get("memory_recall_max_chars"), 2_500, 256, 20_000
            ),
            memory_decay_half_life_days=_as_int(
                data.get("memory_decay_half_life_days"), 120, 7, 3_650
            ),
            memory_decay_forget_confidence=_as_float(
                data.get("memory_decay_forget_confidence"), 0.15, 0.05, 0.5
            ),
            memory_contradiction_penalty=_as_float(
                data.get("memory_contradiction_penalty"), 0.5, 0.05, 1.0
            ),
            memory_related_boost=_as_float(
                data.get("memory_related_boost"), 0.15, 0.0, 0.5
            ),
            context_summary_enabled=_as_bool(data.get("context_summary_enabled"), True),
            context_summary_max_chars=_as_int(
                data.get("context_summary_max_chars"), 2_400, 500, 6_000
            ),
            context_summary_timeout_seconds=_as_float(
                data.get("context_summary_timeout_seconds"), 30.0, 5.0, 120.0
            ),
            memory_intent_analysis_enabled=_as_bool(
                data.get("memory_intent_analysis_enabled"), False
            ),
            memory_extract_batch_turns=_as_int(
                data.get("memory_extract_batch_turns"), 8, 1, 20
            ),
            memory_extract_idle_seconds=_as_int(
                data.get("memory_extract_idle_seconds"), 180, 15, 3_600
            ),
            memory_job_max_attempts=_as_int(
                data.get("memory_job_max_attempts"), 5, 1, 20
            ),
            reply_examples_enabled=_as_bool(data.get("reply_examples_enabled"), True),
            reply_examples_limit=_as_int(data.get("reply_examples_limit"), 3, 0, 10),
            reply_examples_max_chars=_as_int(
                data.get("reply_examples_max_chars"), 2_000, 128, 20_000
            ),
            reply_examples_min_quality=_as_float(
                data.get("reply_examples_min_quality"), 0.7, 0.0, 1.0
            ),
            reply_examples_recall_score_threshold=_as_float(
                data.get("reply_examples_recall_score_threshold"), 0.2, 0.0, 1.0
            ),
            proactive_window_initial_seconds=_as_int(
                data.get("proactive_window_initial_seconds"), 10, 5, 600
            ),
            proactive_window_max_seconds=_as_int(
                data.get("proactive_window_max_seconds"), 300, 30, 3_600
            ),
            proactive_keywords=_as_string_list(data.get("proactive_keywords")),
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
            "message_interval_seconds": self.message_interval_seconds,
            "protocol_enabled": self.protocol_enabled,
            "protocol_injection_mode": self.protocol_injection_mode,
            "protocol_repair_retry_enabled": self.protocol_repair_retry_enabled,
            "protocol_log_retention_days": self.protocol_log_retention_days,
            "max_messages_per_reply": self.max_messages_per_reply,
            "no_reply_enabled": self.no_reply_enabled,
            "jargon_enabled": self.jargon_enabled,
            "min_confidence_for_injection": self.min_confidence_for_injection,
            "max_injected_jargons": self.max_injected_jargons,
            "memory_enabled": self.memory_enabled,
            "memory_auto_extract_enabled": self.memory_auto_extract_enabled,
            "memory_extraction_provider_id": self.memory_extraction_provider_id,
            "memory_embedding_provider_id": self.memory_embedding_provider_id,
            "memory_rerank_provider_id": self.memory_rerank_provider_id,
            "image_transcription_provider_id": self.image_transcription_provider_id,
            "image_cache_enabled": self.image_cache_enabled,
            "image_cache_max_entries": self.image_cache_max_entries,
            "image_cache_max_sticker_entries": self.image_cache_max_sticker_entries,
            "memory_identity_secret_env": self.memory_identity_secret_env,
            "memory_recall_timeout_seconds": self.memory_recall_timeout_seconds,
            "memory_auto_activate_confidence": (self.memory_auto_activate_confidence),
            "memory_candidate_min_confidence": (self.memory_candidate_min_confidence),
            "memory_recall_limit": self.memory_recall_limit,
            "memory_recall_score_threshold": self.memory_recall_score_threshold,
            "memory_recall_max_chars": self.memory_recall_max_chars,
            "memory_decay_half_life_days": self.memory_decay_half_life_days,
            "memory_decay_forget_confidence": self.memory_decay_forget_confidence,
            "memory_contradiction_penalty": self.memory_contradiction_penalty,
            "memory_related_boost": self.memory_related_boost,
            "context_summary_enabled": self.context_summary_enabled,
            "context_summary_max_chars": self.context_summary_max_chars,
            "context_summary_timeout_seconds": (self.context_summary_timeout_seconds),
            "memory_intent_analysis_enabled": self.memory_intent_analysis_enabled,
            "memory_extract_batch_turns": self.memory_extract_batch_turns,
            "memory_extract_idle_seconds": self.memory_extract_idle_seconds,
            "memory_job_max_attempts": self.memory_job_max_attempts,
            "reply_examples_enabled": self.reply_examples_enabled,
            "reply_examples_limit": self.reply_examples_limit,
            "reply_examples_max_chars": self.reply_examples_max_chars,
            "reply_examples_min_quality": self.reply_examples_min_quality,
            "reply_examples_recall_score_threshold": (
                self.reply_examples_recall_score_threshold
            ),
            "proactive_window_initial_seconds": (self.proactive_window_initial_seconds),
            "proactive_window_max_seconds": self.proactive_window_max_seconds,
            "proactive_keywords": list(self.proactive_keywords),
        }
