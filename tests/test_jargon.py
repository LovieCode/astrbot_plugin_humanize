from __future__ import annotations

import pytest
from astrbot_plugin_humanize.humanize.domain.models import (
    JargonStatus,
    KnownTerm,
    UnknownTerm,
)
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.jargon.normalizer import (
    is_valid_candidate,
    normalize_term,
    term_matches,
)


def _unknown(word: str) -> UnknownTerm:
    return UnknownTerm(word=word, guess="测试含义", confidence=0.9, reason="上下文")


def _known(
    entry_id: int,
    term: str,
    *,
    status: JargonStatus = JargonStatus.PROVISIONAL,
    confidence: float = 0.9,
) -> KnownTerm:
    return KnownTerm(
        entry_id=entry_id,
        term=term,
        normalized_term=normalize_term(term),
        meaning=f"{term} 的含义",
        confidence=confidence,
        status=status,
        scope_type="chat",
        scope_id="group-a",
    )


@pytest.mark.parametrize(
    ("word", "source"),
    [
        ("1", "1"),
        ("12345", "这里有 12345"),
        ("https://example.com", "看看 https://example.com"),
        ("@小明", "@小明 你好"),
        ("不存在", "原文里没有这个词"),
        ("<Action>", "用户输入了 <Action>"),
    ],
)
def test_candidate_filter_rejects_noise(word: str, source: str) -> None:
    assert not is_valid_candidate(_unknown(word), source, max_chars=32)


def test_candidate_filter_accepts_normalized_term_present_in_source() -> None:
    assert normalize_term("  ＹＹＤＳ  ") == "yyds"
    assert is_valid_candidate(_unknown("ＹＹＤＳ"), "这也太 yyds 了", max_chars=32)


def test_latin_terms_require_word_boundaries() -> None:
    assert term_matches("abc", "今天 abc 真强")
    assert not term_matches("abc", "今天 xabcx 真强")
    assert term_matches("内鬼", "群里可能有内鬼")


def test_matcher_prefers_verified_then_longest_term() -> None:
    terms = [
        _known(1, "开摆"),
        _known(2, "开摆了"),
        _known(3, "摆", status=JargonStatus.VERIFIED, confidence=1.0),
    ]

    selected = JargonMatcher().select(
        terms,
        "今天直接开摆了",
        max_count=3,
        char_budget=1_000,
    )

    assert [term.entry_id for term in selected] == [3, 2, 1]


def test_matcher_honors_count_and_budget() -> None:
    terms = [_known(1, "yyds"), _known(2, "xswl")]

    assert JargonMatcher().select(
        terms,
        "yyds xswl",
        max_count=1,
        char_budget=1_000,
    ) == (terms[0],)
    assert (
        JargonMatcher().select(
            terms,
            "yyds xswl",
            max_count=2,
            char_budget=1,
        )
        == ()
    )
