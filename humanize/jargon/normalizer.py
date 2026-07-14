from __future__ import annotations

import re
import unicodedata

from ..domain.models import UnknownTerm

_URL_RE = re.compile(r"^(?:https?://|www\.)", re.IGNORECASE)
_ONLY_NUMBER_OR_PUNCT_RE = re.compile(r"^[\W\d_]+$", re.UNICODE)
_LATINISH_RE = re.compile(r"^[a-z0-9_+.-]+$", re.IGNORECASE)


def normalize_term(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def term_matches(term: str, text: str) -> bool:
    normalized_term = normalize_term(term)
    normalized_text = normalize_term(text)
    if not normalized_term or not normalized_text:
        return False
    if _LATINISH_RE.fullmatch(normalized_term):
        pattern = re.compile(
            rf"(?<![\w]){re.escape(normalized_term)}(?![\w])", re.IGNORECASE
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
