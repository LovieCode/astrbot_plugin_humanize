from __future__ import annotations

import re
import unicodedata

from ..domain.models import UnknownTerm

_URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
_ONLY_NUMBER_OR_PUNCT_RE = re.compile(r"^[\W\d_]+$", re.UNICODE)
_LATINISH_RE = re.compile(r"^[a-z0-9_+.-]+$", re.IGNORECASE)


def normalize_term(value: str, *, case_sensitive: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not case_sensitive:
        normalized = normalized.casefold()
    return " ".join(normalized.split())


def term_matches(
    term: str,
    text: str,
    *,
    match_mode: str = "smart",
    case_sensitive: bool = False,
) -> bool:
    """Match a term with an explicit, deterministic policy.

    Args:
        term: Canonical term or alias.
        text: Current user message.
        match_mode: One of smart, contains, or exact.
        case_sensitive: Whether letter case must be preserved.

    Returns:
        Whether the term matches the supplied text.

    Raises:
        ValueError: If the match mode is unsupported.
    """
    if match_mode not in {"smart", "contains", "exact"}:
        raise ValueError("unsupported jargon match mode")
    normalized_term = normalize_term(term, case_sensitive=case_sensitive)
    normalized_text = normalize_term(text, case_sensitive=case_sensitive)
    if not normalized_term or not normalized_text:
        return False
    if match_mode == "exact":
        return normalized_term == normalized_text
    if match_mode == "contains":
        return normalized_term in normalized_text
    if _LATINISH_RE.fullmatch(normalized_term):
        pattern = re.compile(
            rf"(?<![\w]){re.escape(normalized_term)}(?![\w])",
            0 if case_sensitive else re.IGNORECASE,
        )
        return pattern.search(normalized_text) is not None
    return normalized_term in normalized_text


def is_valid_candidate(term: UnknownTerm, source_text: str, max_chars: int) -> bool:
    word = term.word.strip()
    normalized = normalize_term(word)
    if not 2 <= len(normalized) <= max_chars:
        return False
    if _URL_RE.match(normalized) or normalized.startswith(("@", "#")):
        return False
    if _ONLY_NUMBER_OR_PUNCT_RE.fullmatch(normalized):
        return False
    if "<" in word or ">" in word:
        return False
    return term_matches(word, source_text)
