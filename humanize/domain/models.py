from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    REPLY = "Reply"
    NO_REPLY = "No Reply"
    WAIT = "Wait"


class EventState(StrEnum):
    INACTIVE = "inactive"
    REQUESTED = "requested"
    TOOL_RUNNING = "tool_running"
    FINAL_VALID = "final_valid"
    FINAL_BLOCKED = "final_blocked"
    NO_REPLY = "no_reply"
    DISPATCHED = "dispatched"


class JargonStatus(StrEnum):
    CANDIDATE = "candidate"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"
    AMBIGUOUS = "ambiguous"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class UnknownTerm:
    word: str
    guess: str
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class ImageCache:
    """One plain-text image transcription cache entry.

    The text is the combined contextual meaning plus brief content of the
    image (produced by a multimodal model), not a dry visual description.
    """

    text: str


@dataclass(frozen=True, slots=True)
class KnownSense:
    """Represent one scoped meaning that may be injected for a known term."""

    sense_id: int
    meaning: str
    confidence: float
    status: JargonStatus
    reason: str = ""


@dataclass(frozen=True, slots=True)
class KnownTerm:
    entry_id: int
    term: str
    normalized_term: str
    meaning: str
    confidence: float
    status: JargonStatus
    scope_type: str
    scope_id: str
    senses: tuple[KnownSense, ...] = ()
    aliases: tuple[str, ...] = ()
    match_mode: str = "smart"
    case_sensitive: bool = False
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class ProtocolDecision:
    action: Action
    messages: tuple[str, ...]
    unknown_terms: tuple[UnknownTerm, ...]
    image_cache: tuple[ImageCache, ...] = ()
    no_reply_reason: str = ""
    messages_over_limit: bool = False
    wait_seconds: int = 0


@dataclass(frozen=True, slots=True)
class MessageContext:
    request_id: str
    scope_type: str
    scope_id: str
    message_id: str
    sender_id: str
    sender_name: str
    user_text: str
    chat_scene: str
    admin_name: str
    admin_ids: tuple[str, ...]
    conversation_id: str = ""
    occurred_at: str = ""
    attachment_refs: tuple[dict[str, object], ...] = ()
    source_complete: bool = True
    agent_id: str = "default"
    bot_name: str = ""


@dataclass(frozen=True, slots=True)
class ContextSection:
    """Describe one logical context fragment prepared for an LLM request.

    Attributes:
        key: Stable section identifier used by storage and WebUI filters.
        ordinal: Deterministic injection order within the request.
        priority: Relative importance when optional sections compete for budget.
        source_type: Origin category such as message, protocol, or repository.
        source_refs: Stable source references without embedding private content.
        targets: Provider request locations that receive this fragment.
        required: Whether the fragment must be retained without truncation.
        included: Whether the fragment is part of the prepared request.
        budget_tokens: Optional estimated token budget for this section.
        estimated_tokens: Estimated tokens for one copy of the content.
        applied_tokens: Estimated tokens across every configured target.
        item_count: Number of selected source items represented by the fragment.
        reason: Stable explanation for inclusion or omission.
        content: Exact content prepared by the composer.
    """

    key: str
    ordinal: int
    priority: int
    source_type: str
    source_refs: tuple[str, ...]
    targets: tuple[str, ...]
    required: bool
    included: bool
    budget_tokens: int | None
    estimated_tokens: int
    applied_tokens: int
    item_count: int
    reason: str
    content: str


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    protocol_prompt: str
    message_xml: str
    known_terms_xml: str
    matched_terms: tuple[KnownTerm, ...]
    sections: tuple[ContextSection, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalOutcome:
    valid: bool
    action: Action | None = None
    messages: tuple[str, ...] = ()
    unknown_terms: tuple[UnknownTerm, ...] = ()
    image_cache: tuple[ImageCache, ...] = ()
    no_reply_reason: str = ""
    messages_over_limit: bool = False
    wait_seconds: int = 0
    error_code: str = ""
    error_detail: str = ""
