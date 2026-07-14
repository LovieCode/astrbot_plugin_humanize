from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    REPLY = "Reply"
    NO_REPLY = "No Reply"


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
class KnownTerm:
    entry_id: int
    term: str
    normalized_term: str
    meaning: str
    confidence: float
    status: JargonStatus
    scope_type: str
    scope_id: str


@dataclass(frozen=True, slots=True)
class ProtocolDecision:
    action: Action
    messages: tuple[str, ...]
    unknown_terms: tuple[UnknownTerm, ...]


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


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    protocol_prompt: str
    message_xml: str
    known_terms_xml: str
    matched_terms: tuple[KnownTerm, ...]


@dataclass(frozen=True, slots=True)
class FinalOutcome:
    valid: bool
    action: Action | None = None
    messages: tuple[str, ...] = ()
    unknown_terms: tuple[UnknownTerm, ...] = ()
    error_code: str = ""
    error_detail: str = ""
