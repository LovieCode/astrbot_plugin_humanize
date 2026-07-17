from __future__ import annotations

from collections.abc import Iterable

from ..domain.models import JargonStatus, KnownTerm
from .normalizer import term_matches


class JargonMatcher:
    _STATUS_PRIORITY = {
        JargonStatus.VERIFIED: 0,
        JargonStatus.PROVISIONAL: 1,
        JargonStatus.CANDIDATE: 2,
        JargonStatus.AMBIGUOUS: 3,
        JargonStatus.REJECTED: 4,
    }

    def select(
        self,
        terms: Iterable[KnownTerm],
        text: str,
        *,
        max_count: int,
        char_budget: int,
    ) -> tuple[KnownTerm, ...]:
        matched = [
            term
            for term in terms
            if term.enabled
            and any(
                term_matches(
                    candidate,
                    text,
                    match_mode=term.match_mode,
                    case_sensitive=term.case_sensitive,
                )
                for candidate in (term.term, *term.aliases)
            )
        ]
        matched.sort(
            key=lambda term: (
                self._STATUS_PRIORITY.get(term.status, 99),
                -len(term.normalized_term),
                -term.confidence,
                term.entry_id,
            )
        )

        selected: list[KnownTerm] = []
        used_chars = 0
        for term in matched:
            meanings = term.senses or ()
            meaning_chars = (
                sum(len(sense.meaning) for sense in meanings)
                if meanings
                else len(term.meaning)
            )
            estimated = (
                len(term.term)
                + sum(len(alias) for alias in term.aliases)
                + meaning_chars
                + 24
            )
            if selected and used_chars + estimated > char_budget:
                continue
            if not selected and estimated > char_budget:
                continue
            selected.append(term)
            used_chars += estimated
            if len(selected) >= max_count:
                break
        return tuple(selected)
