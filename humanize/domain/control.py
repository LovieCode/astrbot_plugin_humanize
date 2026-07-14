from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _clean_text(value: Any, *, default: str, limit: int) -> str:
    """Normalize a bounded text field."""
    text = str(value if value is not None else default).strip()
    if len(text) > limit:
        raise ValueError(f"text field exceeds {limit} characters")
    return text


def _clean_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    """Normalize a bounded list of unique text values."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("list field must be an array")
    items: list[str] = []
    for raw in value:
        item = str(raw).strip()
        if not item:
            continue
        if len(item) > item_limit:
            raise ValueError(f"list item exceeds {item_limit} characters")
        if item not in items:
            items.append(item)
    if len(items) > limit:
        raise ValueError(f"list field allows at most {limit} items")
    return items


def _bounded_float(value: Any, *, default: float) -> float:
    """Parse a state or policy value and keep it within the 0..1 range."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        parsed = default
    return round(max(0.0, min(1.0, parsed)), 3)


def _bounded_int(value: Any, *, default: int, maximum: int) -> int:
    """Parse a non-negative integer with a product-level ceiling."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(parsed, maximum))


def _boolean(value: Any, *, default: bool) -> bool:
    """Parse booleans from JSON values and common form encodings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


@dataclass(frozen=True, slots=True)
class PersonaConfig:
    """Stable, user-editable persona fields."""

    name: str = "小助手"
    identity: str = "一个有分寸、愿意帮忙的聊天助手。"
    traits: list[str] = field(default_factory=lambda: ["克制", "细心"])
    values: list[str] = field(default_factory=lambda: ["诚实", "尊重边界"])
    boundaries: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> PersonaConfig:
        """Build a validated persona from an API payload or database row."""
        data = raw or {}
        defaults = cls()
        return cls(
            name=_clean_text(data.get("name"), default=defaults.name, limit=80)
            or defaults.name,
            identity=_clean_text(
                data.get("identity"), default=defaults.identity, limit=2_000
            ),
            traits=_clean_list(data.get("traits"), limit=20, item_limit=80),
            values=_clean_list(data.get("values"), limit=20, item_limit=80),
            boundaries=_clean_list(data.get("boundaries"), limit=20, item_limit=200),
        )

    @classmethod
    def from_row(cls, row: Any) -> PersonaConfig:
        """Build a persona from a SQLite row."""
        return cls.from_mapping(
            {
                "name": row["name"],
                "identity": row["identity"],
                "traits": json.loads(row["traits_json"]),
                "values": json.loads(row["values_json"]),
                "boundaries": json.loads(row["boundaries_json"]),
            }
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "name": self.name,
            "identity": self.identity,
            "traits": list(self.traits),
            "values": list(self.values),
            "boundaries": list(self.boundaries),
        }


@dataclass(frozen=True, slots=True)
class DynamicState:
    """Bounded, resettable runtime state for the assistant."""

    mood: float = 0.5
    energy: float = 0.5
    interest: float = 0.5
    stress: float = 0.0
    focus: str = ""
    updated_at: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> DynamicState:
        """Build validated dynamic state from an API payload or database row."""
        data = raw or {}
        defaults = cls()
        return cls(
            mood=_bounded_float(data.get("mood"), default=defaults.mood),
            energy=_bounded_float(data.get("energy"), default=defaults.energy),
            interest=_bounded_float(data.get("interest"), default=defaults.interest),
            stress=_bounded_float(data.get("stress"), default=defaults.stress),
            focus=_clean_text(data.get("focus"), default="", limit=500),
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )

    @classmethod
    def from_row(cls, row: Any) -> DynamicState:
        """Build dynamic state from a SQLite row."""
        return cls.from_mapping(dict(row))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "mood": self.mood,
            "energy": self.energy,
            "interest": self.interest,
            "stress": self.stress,
            "focus": self.focus,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class BehaviorPolicy:
    """Switches and thresholds used by future behavior decision executors."""

    enabled: bool = True
    allow_no_reply: bool = True
    allow_follow_up: bool = True
    allow_proactive: bool = False
    allow_end_topic: bool = True
    reply_threshold: float = 0.35
    follow_up_threshold: float = 0.65
    proactive_threshold: float = 0.85
    end_topic_threshold: float = 0.8
    cooldown_minutes: int = 30
    updated_at: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> BehaviorPolicy:
        """Build a validated behavior policy from an API payload or row."""
        data = raw or {}
        defaults = cls()
        return cls(
            enabled=_boolean(data.get("enabled"), default=defaults.enabled),
            allow_no_reply=_boolean(
                data.get("allow_no_reply"), default=defaults.allow_no_reply
            ),
            allow_follow_up=_boolean(
                data.get("allow_follow_up"), default=defaults.allow_follow_up
            ),
            allow_proactive=_boolean(
                data.get("allow_proactive"), default=defaults.allow_proactive
            ),
            allow_end_topic=_boolean(
                data.get("allow_end_topic"), default=defaults.allow_end_topic
            ),
            reply_threshold=_bounded_float(
                data.get("reply_threshold"), default=defaults.reply_threshold
            ),
            follow_up_threshold=_bounded_float(
                data.get("follow_up_threshold"), default=defaults.follow_up_threshold
            ),
            proactive_threshold=_bounded_float(
                data.get("proactive_threshold"), default=defaults.proactive_threshold
            ),
            end_topic_threshold=_bounded_float(
                data.get("end_topic_threshold"), default=defaults.end_topic_threshold
            ),
            cooldown_minutes=_bounded_int(
                data.get("cooldown_minutes"),
                default=defaults.cooldown_minutes,
                maximum=10_080,
            ),
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )

    @classmethod
    def from_row(cls, row: Any) -> BehaviorPolicy:
        """Build a behavior policy from a SQLite row."""
        return cls.from_mapping(dict(row))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "enabled": self.enabled,
            "allow_no_reply": self.allow_no_reply,
            "allow_follow_up": self.allow_follow_up,
            "allow_proactive": self.allow_proactive,
            "allow_end_topic": self.allow_end_topic,
            "reply_threshold": self.reply_threshold,
            "follow_up_threshold": self.follow_up_threshold,
            "proactive_threshold": self.proactive_threshold,
            "end_topic_threshold": self.end_topic_threshold,
            "cooldown_minutes": self.cooldown_minutes,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ExpressionConfig:
    """Configuration and observed status for an expression-layer integration."""

    enabled: bool = False
    provider: str = "astrbot_plugin_style_learner"
    mode: str = "observe"
    profile: str = "default"
    integration_status: str = "not_checked"
    last_checked_at: str | None = None
    last_error: str = ""
    updated_at: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> ExpressionConfig:
        """Build validated expression integration configuration."""
        data = raw or {}
        defaults = cls()
        mode = str(data.get("mode") or defaults.mode).strip().lower()
        if mode not in {"off", "observe", "inject"}:
            raise ValueError("expression mode must be off, observe, or inject")
        status = str(
            data.get("integration_status") or defaults.integration_status
        ).strip()
        if status not in {
            "not_checked",
            "available",
            "unavailable",
            "disabled",
            "ready",
            "connected",
            "error",
            "unknown",
        }:
            raise ValueError("unsupported expression integration status")
        enabled = _boolean(data.get("enabled"), default=defaults.enabled)
        if not enabled or mode == "off":
            status = "disabled"
        elif status == "disabled":
            status = "not_checked"
        return cls(
            enabled=enabled,
            provider=_clean_text(
                data.get("provider"), default=defaults.provider, limit=120
            ),
            mode=mode,
            profile=_clean_text(
                data.get("profile"), default=defaults.profile, limit=120
            ),
            integration_status=status,
            last_checked_at=(
                str(data["last_checked_at"]) if data.get("last_checked_at") else None
            ),
            last_error=_clean_text(data.get("last_error"), default="", limit=1_000),
            updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        )

    @classmethod
    def from_row(cls, row: Any) -> ExpressionConfig:
        """Build expression configuration from a SQLite row."""
        return cls.from_mapping(dict(row))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "mode": self.mode,
            "profile": self.profile,
            "integration_status": self.integration_status,
            "last_checked_at": self.last_checked_at,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


CONTROL_SECTIONS = ("persona", "state", "behavior", "expression")
