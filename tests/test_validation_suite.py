"""Validation test suite for the Humanize protocol.

Layers:
1. Compliance corpus — ~35 parametric cases covering the full output space.
2. Repair-flow pass rate — repairable cases go through extract → compose.
3. End-to-end scenario conversations — jargon learning, scope isolation.
4. Optional real-LLM probe — set HUMANIZE_VALIDATION_LLM=1 to activate.

Usage:
    pytest tests/test_validation_suite.py -v
    HUMANIZE_VALIDATION_LLM=1 \\
      HUMANIZE_TEST_API_KEY=sk-... \\
      HUMANIZE_TEST_API_BASE=https://api.openai.com/v1 \\
      HUMANIZE_TEST_MODEL=gpt-4o-mini \\
      pytest tests/test_validation_suite.py -v -k llm
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.errors import ProtocolValidationError
from astrbot_plugin_humanize.humanize.domain.models import Action, MessageContext
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.services.humanize import HumanizeService

# ---------------------------------------------------------------------------
# Corpus dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseCase:
    """One LLM response sample with expected parse outcome."""

    description: str
    raw: str
    expected_valid: bool
    expected_error_code: str = ""  # when expected_valid is False
    expected_action: str = ""  # when expected_valid is True
    expected_no_reply_reason: str = ""  # when expected_action is No Reply
    repairable: bool = False  # repair flow recovers this failed case
    config_overrides: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Corpus definition
# ---------------------------------------------------------------------------


def _ut(word: str, guess: str, confidence: float = 0.85) -> str:
    """Build a compact UnknownTerms JSON string for one term."""
    return json.dumps(
        [{"word": word, "guess": guess, "confidence": confidence, "reason": "test"}],
        ensure_ascii=False,
        separators=(",", ":"),
    )


CORPUS: list[ResponseCase] = [
    # --- Valid Reply cases ---
    ResponseCase(
        description="minimal reply with Messages container",
        raw="<Action>Reply</Action>\n<Messages><Message>好的</Message></Messages>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with multiple messages",
        raw=(
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>第一条</Message><Message>第二条</Message></Messages>"
        ),
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with unknown terms",
        raw=f"<Action>Reply</Action>\n<UnknownTerms>{_ut('yyds', '永远的神', 0.92)}</UnknownTerms>\n"
        "<Messages><Message>懂了</Message></Messages>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with ImageCache",
        raw=(
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>好可爱</Message></Messages>\n"
            "<ImageCache>一只小猪瞪着无辜的眼睛</ImageCache>"
        ),
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with legacy Reply container",
        raw="<Action>Reply</Action>\n<Reply><Message>旧格式</Message></Reply>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="action tag after messages block",
        raw="<Messages><Message>正文</Message></Messages>\n<Action>Reply</Action>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with leading whitespace in Action value",
        raw="<Action>  Reply  </Action>\n<Messages><Message>ok</Message></Messages>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with UnknownTerms confidence at boundary 0.0",
        raw=f"<Action>Reply</Action>\n<UnknownTerms>{_ut('词', '含义', 0.0)}</UnknownTerms>\n"
        "<Messages><Message>收到</Message></Messages>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with UnknownTerms confidence at boundary 1.0",
        raw=f"<Action>Reply</Action>\n<UnknownTerms>{_ut('词', '含义', 1.0)}</UnknownTerms>\n"
        "<Messages><Message>收到</Message></Messages>",
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with Chinese + emoji in message body",
        raw=(
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>哈哈哈 😂 真的太好笑了！</Message></Messages>"
        ),
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with newlines preserved inside Message",
        raw=(
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>第一行\n\n第三行</Message></Messages>"
        ),
        expected_valid=True,
        expected_action="Reply",
    ),
    ResponseCase(
        description="reply with Action tag before UnknownTerms swapped",
        raw=(
            "<UnknownTerms>[]</UnknownTerms>\n"
            "<Action>Reply</Action>\n"
            "<Messages><Message>顺序不重要</Message></Messages>"
        ),
        expected_valid=True,
        expected_action="Reply",
    ),
    # --- Valid No Reply cases ---
    ResponseCase(
        description="minimal No Reply",
        raw="<Action>No Reply</Action>",
        expected_valid=True,
        expected_action="No Reply",
    ),
    ResponseCase(
        description="No Reply with UnknownTerms",
        raw=f"<Action>No Reply</Action>\n<UnknownTerms>{_ut('梗', '流行语', 0.7)}</UnknownTerms>",
        expected_valid=True,
        expected_action="No Reply",
    ),
    ResponseCase(
        description="No Reply with whitespace-only trailing content",
        raw="<Action>No Reply</Action>\n   \n",
        expected_valid=True,
        expected_action="No Reply",
    ),
    # --- Invalid: structural failures ---
    ResponseCase(
        description="empty string",
        raw="",
        expected_valid=False,
        expected_error_code="empty_output",
    ),
    ResponseCase(
        description="pure whitespace",
        raw="   \n\t  ",
        expected_valid=False,
        expected_error_code="empty_output",
    ),
    ResponseCase(
        description="bare natural-language reply, no Action tag",
        raw="好的，我明白了。",
        expected_valid=False,
        expected_error_code="missing_action",
    ),
    ResponseCase(
        description="legacy colon header only, no XML tags",
        raw="Action: Reply\nUnknownTerms: []\n正文",
        expected_valid=False,
        expected_error_code="missing_action",
    ),
    ResponseCase(
        description="unsupported Action value",
        raw="<Action>Wait</Action>\n<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_action",
    ),
    ResponseCase(
        description="empty Action value",
        raw="<Action></Action>\n<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_action",
    ),
    ResponseCase(
        description="No Reply with a Messages reason",
        raw=(
            "<Action>No Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>当前话题不适合插话</Message></Messages>"
        ),
        expected_valid=True,
        expected_action="No Reply",
        expected_no_reply_reason="当前话题不适合插话",
    ),
    ResponseCase(
        description="UnknownTerms JSON parse error",
        raw="<Action>Reply</Action>\n<UnknownTerms>[broken</UnknownTerms>\n"
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms_json",
    ),
    ResponseCase(
        description="UnknownTerms is a JSON object, not array",
        raw="<Action>Reply</Action>\n<UnknownTerms>{}</UnknownTerms>\n"
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms item has extra keys",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"x","guess":"g","confidence":0.5,"reason":"r","extra":"bad"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms word is not a string",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":42,"guess":"g","confidence":0.5,"reason":"r"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms word is empty",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"","guess":"g","confidence":0.5,"reason":"r"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms guess is empty",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"x","guess":"","confidence":0.5,"reason":"r"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms confidence above 1.0",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"x","guess":"g","confidence":1.5,"reason":"r"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    ResponseCase(
        description="UnknownTerms confidence is NaN string",
        raw="<Action>Reply</Action>\n"
        '<UnknownTerms>[{"word":"x","guess":"g","confidence":"nan","reason":"r"}]</UnknownTerms>\n'
        "<Messages><Message>ok</Message></Messages>",
        expected_valid=False,
        expected_error_code="invalid_unknown_terms",
    ),
    # --- Repairable: legacy header format ---
    ResponseCase(
        description="legacy colon header with Messages body",
        raw="Action: Reply\nUnknownTerms: []\n<Messages><Message>正文</Message></Messages>",
        expected_valid=False,
        expected_error_code="missing_action",
        repairable=True,
    ),
    ResponseCase(
        description="lowercase action colon with unknown terms and body",
        raw=(
            "action: Reply\n"
            f"UnknownTerms: {_ut('nb', '很厉害', 0.88)}\n"
            "<Messages><Message>没错</Message></Messages>"
        ),
        expected_valid=False,
        expected_error_code="missing_action",
        repairable=True,
    ),
]

# Split for easy filtering in assertions
_VALID_CASES = [c for c in CORPUS if c.expected_valid]
_INVALID_CASES = [c for c in CORPUS if not c.expected_valid]
_REPAIRABLE_CASES = [c for c in CORPUS if c.repairable]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _parser(overrides: dict[str, Any] | None = None) -> ProtocolParser:
    config = PluginConfig.from_mapping(overrides) if overrides else PluginConfig()
    return ProtocolParser(config)


def _make_context(
    scope_id: str = "group-a",
    *,
    request_id: str = "req-val-1",
    user_text: str = "测试消息",
) -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type="group",
        scope_id=scope_id,
        message_id=f"msg-{request_id}",
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text,
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


async def _build_service(db_path: Path) -> tuple[HumanizeService, SQLiteRepository]:
    config = PluginConfig()
    repo = SQLiteRepository(db_path)
    await repo.initialize()
    svc = HumanizeService(
        config=config,
        repository=repo,
        envelope=EnvelopeBuilder(config),
        parser=ProtocolParser(config),
        matcher=JargonMatcher(),
    )
    return svc, repo


# ---------------------------------------------------------------------------
# 1. Parametric corpus — every case asserted individually
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    CORPUS,
    ids=[c.description for c in CORPUS],
)
def test_corpus_case(case: ResponseCase) -> None:
    """Every corpus entry must produce the declared parse outcome."""
    parser = _parser(case.config_overrides or None)
    if case.expected_valid:
        decision = parser.parse(case.raw)
        assert decision.action.value == case.expected_action, (
            f"expected action={case.expected_action!r}, got {decision.action!r}"
        )
        assert decision.no_reply_reason == case.expected_no_reply_reason, (
            f"expected no_reply_reason={case.expected_no_reply_reason!r}, "
            f"got {decision.no_reply_reason!r}"
        )
    else:
        with pytest.raises(ProtocolValidationError) as exc_info:
            parser.parse(case.raw)
        assert exc_info.value.code == case.expected_error_code, (
            f"expected error_code={case.expected_error_code!r}, "
            f"got {exc_info.value.code!r}"
        )


# ---------------------------------------------------------------------------
# 2. Pass rate summary — aggregate thresholds across the corpus
# ---------------------------------------------------------------------------


def test_valid_corpus_pass_rate_is_100_percent() -> None:
    """Every case marked expected_valid must parse without exception."""
    parser = _parser()
    failures: list[str] = []
    for case in _VALID_CASES:
        try:
            parser.parse(case.raw)
        except ProtocolValidationError as exc:
            failures.append(f"{case.description!r}: {exc.code} — {exc.detail}")
    assert not failures, (
        f"{len(failures)}/{len(_VALID_CASES)} valid cases failed:\n"
        + "\n".join(f"  • {f}" for f in failures)
    )


def test_invalid_corpus_rejection_rate_is_100_percent() -> None:
    """Every case marked expected_valid=False must raise ProtocolValidationError."""
    parser = _parser()
    surprises: list[str] = []
    for case in _INVALID_CASES:
        try:
            parser.parse(case.raw)
            surprises.append(case.description)
        except ProtocolValidationError:
            pass
    assert not surprises, (
        f"{len(surprises)}/{len(_INVALID_CASES)} invalid cases unexpectedly passed:\n"
        + "\n".join(f"  • {s!r}" for s in surprises)
    )


# ---------------------------------------------------------------------------
# 3. Repair-flow pass rate
# ---------------------------------------------------------------------------


def test_repairable_cases_survive_extract_compose_cycle() -> None:
    """For every repairable corpus case the extract→compose→parse cycle succeeds."""
    parser = _parser()
    results: list[str] = []
    for case in _REPAIRABLE_CASES:
        # Step 1: direct parse must fail
        with pytest.raises(ProtocolValidationError):
            parser.parse(case.raw)

        # Step 2: extract body + required action
        candidate = ProtocolParser.extract_repair_candidate(case.raw)
        if candidate is None:
            results.append(f"EXTRACT_FAILED: {case.description!r}")
            continue
        body, required_action = candidate

        # Step 3: simulate a repair response (what the LLM would return)
        repair_response = (
            f"<Action>{required_action}</Action>\n<UnknownTerms>[]</UnknownTerms>"
        )

        # Step 4: compose repaired full response
        try:
            repaired = ProtocolParser.compose_repaired_response(repair_response, body)
        except ProtocolValidationError as exc:
            results.append(f"COMPOSE_FAILED: {case.description!r} — {exc.code}")
            continue

        # Step 5: repaired response must parse cleanly
        try:
            decision = parser.parse(repaired)
        except ProtocolValidationError as exc:
            results.append(f"PARSE_FAILED: {case.description!r} — {exc.code}")
            continue

        assert decision.action.value == required_action, (
            f"repaired action mismatch for {case.description!r}"
        )

    assert not results, (
        f"{len(results)}/{len(_REPAIRABLE_CASES)} repair cases failed:\n"
        + "\n".join(f"  • {r}" for r in results)
    )


def test_repair_combined_pass_rate_meets_threshold() -> None:
    """Direct parses plus repairs must recover ≥ 90 % of the recoverable corpus.

    The denominator is deliberately restricted to cases that carry a usable
    reply body: valid cases plus repairable ones. Hard-invalid cases (empty
    output, malformed UnknownTerms, ``No Reply`` with a body) carry nothing to
    recover, so rejecting them is the desired outcome rather than a miss, and
    counting them here would cap the achievable rate below any useful bar.
    """
    parser = _parser()
    recoverable = [c for c in CORPUS if c.expected_valid or c.repairable]
    missed: list[str] = []

    for case in recoverable:
        if case.expected_valid:
            try:
                parser.parse(case.raw)
            except ProtocolValidationError as exc:
                # Unexpected; test_valid_corpus_pass_rate_is_100_percent localizes it.
                missed.append(f"DIRECT: {case.description!r} — {exc.code}")
            continue

        candidate = ProtocolParser.extract_repair_candidate(case.raw)
        if candidate is None:
            missed.append(f"EXTRACT: {case.description!r}")
            continue
        body, action = candidate
        repair = f"<Action>{action}</Action>\n<UnknownTerms>[]</UnknownTerms>"
        try:
            parser.parse(ProtocolParser.compose_repaired_response(repair, body))
        except ProtocolValidationError as exc:
            missed.append(f"REPAIR: {case.description!r} — {exc.code}")

    total = len(recoverable)
    rate = (total - len(missed)) / total
    assert rate >= 0.90, (
        f"combined pass rate {rate:.1%} ({total - len(missed)}/{total}) "
        "is below the 90 % threshold:\n" + "\n".join(f"  • {m}" for m in missed)
    )


# ---------------------------------------------------------------------------
# 4. End-to-end scenario conversations
# ---------------------------------------------------------------------------


def test_scenario_jargon_learned_and_scoped(tmp_path: Path) -> None:
    """Multi-turn: jargon learned in turn 1 is injected only in the same scope."""

    async def run() -> None:
        svc, repo = await _build_service(tmp_path / "val.db")

        # Turn 1 — model learns "yyds" in group-a
        ctx1 = _make_context("group-a", request_id="req-s1", user_text="这真的 yyds")
        assert (await svc.prepare_request(ctx1)).matched_terms == ()

        raw1 = (
            "<Action>Reply</Action>\n"
            '<UnknownTerms>[{"word":"yyds","guess":"永远的神",'
            '"confidence":0.93,"reason":"用于称赞"}]</UnknownTerms>\n'
            "<Messages><Message>确实</Message></Messages>"
        )
        outcome1 = await svc.process_final_response(
            ctx1, raw1, model="test", duration_ms=10
        )
        assert outcome1.valid
        assert outcome1.action is Action.REPLY
        assert outcome1.messages == ("确实",)
        assert [t.word for t in outcome1.unknown_terms] == ["yyds"]

        # Turn 2 — same group-a: injected
        ctx2 = _make_context("group-a", request_id="req-s2", user_text="yyds 再来一次")
        prepared2 = await svc.prepare_request(ctx2)
        assert [t.term for t in prepared2.matched_terms] == ["yyds"]
        assert "永远的神" in prepared2.known_terms_xml

        # Turn 3 — different group-b: NOT injected (scope isolation)
        ctx3 = _make_context("group-b", request_id="req-s3", user_text="yyds 在别处")
        prepared3 = await svc.prepare_request(ctx3)
        assert prepared3.matched_terms == ()
        assert "<Term>" not in prepared3.known_terms_xml

    asyncio.run(run())


def test_scenario_invalid_response_logged_no_learning(tmp_path: Path) -> None:
    """An invalid LLM response is logged as a failure and leaves no jargon."""

    async def run() -> None:
        svc, repo = await _build_service(tmp_path / "val.db")
        ctx = _make_context("group-a", request_id="req-bad", user_text="什么是 yyds")

        outcome = await svc.process_final_response(
            ctx,
            "Action: Reply\n这个 yyds 就是永远的神",  # legacy header, missing XML tag
            model="test",
            duration_ms=5,
        )
        assert not outcome.valid
        assert outcome.error_code == "missing_action"

        jargons = await repo.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        logs = await repo.list_protocol_logs(page=1, page_size=20)

        assert jargons["total"] == 0, (
            "no jargon should be learned from an invalid response"
        )
        assert logs["total"] == 1
        assert logs["items"][0]["success"] == 0
        assert logs["items"][0]["failure_code"] == "missing_action"

    asyncio.run(run())


def test_scenario_no_reply_suppresses_dispatch(tmp_path: Path) -> None:
    """No Reply action returns valid=True but empty messages."""

    async def run() -> None:
        svc, _ = await _build_service(tmp_path / "val.db")
        ctx = _make_context("group-a", request_id="req-nr", user_text="不重要的内容")
        raw = (
            "<Action>No Reply</Action>\n"
            '<UnknownTerms>[{"word":"不重要","guess":"无聊话题","confidence":0.6,'
            '"reason":"上下文不需要回复"}]</UnknownTerms>'
        )
        outcome = await svc.process_final_response(
            ctx, raw, model="test", duration_ms=3
        )
        assert outcome.valid
        assert outcome.action is Action.NO_REPLY
        assert outcome.messages == ()

    asyncio.run(run())


def test_scenario_repair_flow_recovers_then_learns(tmp_path: Path) -> None:
    """Simulate the full repair loop: bad → repair → valid with jargon."""

    async def run() -> None:
        parser = ProtocolParser(PluginConfig())
        svc, repo = await _build_service(tmp_path / "val.db")
        ctx = _make_context(
            "group-a", request_id="req-repair", user_text="你懂 yyds 吗"
        )
        unknown_json = (
            '[{"word":"yyds","guess":"永远的神","confidence":0.9,"reason":"称赞"}]'
        )
        malformed = (
            f"Action: Reply\nUnknownTerms: {unknown_json}\n"
            "<Messages><Message>当然懂</Message></Messages>"
        )

        # First attempt fails
        outcome1 = await svc.process_final_response(
            ctx, malformed, model="test", duration_ms=8
        )
        assert not outcome1.valid

        # Repair step: extract body, build repair prompt, simulate LLM repair response
        candidate = parser.extract_repair_candidate(malformed)
        assert candidate is not None
        body, required_action = candidate
        repair_llm_response = (
            f"<Action>{required_action}</Action>\n"
            f"<UnknownTerms>{unknown_json}</UnknownTerms>"
        )
        repaired = parser.compose_repaired_response(repair_llm_response, body)

        # Second attempt with repaired response succeeds
        outcome2 = await svc.process_final_response(
            ctx, repaired, model="test", duration_ms=12
        )
        assert outcome2.valid
        assert outcome2.action is Action.REPLY
        assert outcome2.messages == ("当然懂",)

        # Jargon was learned
        jargons = await repo.list_jargons(
            search="", status="", scope_id="group-a", page=1, page_size=20
        )
        assert jargons["total"] == 1
        assert jargons["items"][0]["term"] == "yyds"

    asyncio.run(run())


def test_scenario_tool_stage_logged_separately(tmp_path: Path) -> None:
    """A tool-call stage response and the final stage are both logged correctly."""

    async def run() -> None:
        svc, repo = await _build_service(tmp_path / "val.db")
        ctx = _make_context("group-a", request_id="req-tool", user_text="帮我搜索天气")

        # Tool stage: model calls a tool, produces intermediate Reply
        tool_raw = (
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>正在查询天气……</Message></Messages>"
        )
        await svc.record_protocol_success(
            ctx,
            action="Reply",
            raw_output=tool_raw,
            messages=("正在查询天气……",),
            model="test",
            duration_ms=5,
            stage="tool",
        )

        # Final stage: model returns final reply after tool result
        final_raw = (
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
            "<Messages><Message>今天晴，25 °C。</Message></Messages>"
        )
        outcome = await svc.process_final_response(
            ctx, final_raw, model="test", duration_ms=18, stage="final"
        )
        assert outcome.valid
        assert outcome.messages == ("今天晴，25 °C。",)

        logs = await repo.list_protocol_logs(page=1, page_size=20)
        by_stage = {item["stage"]: item for item in logs["items"]}
        assert "tool" in by_stage
        assert "final" in by_stage
        assert by_stage["tool"]["success"] == 1
        assert by_stage["final"]["success"] == 1
        assert by_stage["tool"]["is_final"] is False
        assert by_stage["final"]["is_final"] is True

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. Optional real-LLM probe
# ---------------------------------------------------------------------------

_LLM_ENABLED = os.environ.get("HUMANIZE_VALIDATION_LLM", "").lower() in {
    "1",
    "true",
    "yes",
}
_LLM_API_KEY = os.environ.get("HUMANIZE_TEST_API_KEY", "")
_LLM_API_BASE = os.environ.get(
    "HUMANIZE_TEST_API_BASE", "https://api.openai.com/v1"
).rstrip("/")
_LLM_MODEL = os.environ.get("HUMANIZE_TEST_MODEL", "gpt-4o-mini")
_LLM_SAMPLES = max(1, int(os.environ.get("HUMANIZE_TEST_SAMPLES", "8")))

# Realistic user messages that exercise diverse protocol scenarios
_LLM_TEST_MESSAGES = [
    "yyds 是什么意思？",
    "今天天气真的 yyds，你觉得呢？",
    "你好，随便聊聊吧",
    "这操作属于是 nb 了",
    "不用回我，我在说废话",
    "帮我解释一下 gkd",
    "你懂 awsl 这个词吗",
    "好的，知道了",
]


def _call_llm(
    system: str,
    user: str,
    *,
    contexts: list[dict[str, str]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Make one OpenAI-compatible chat completion request.

    Args:
        system: System prompt to inject.
        user: User message text.
        contexts: Optional conversation history (list of role/content dicts).
        tools: Optional function/tool definitions.

    Returns:
        Parsed JSON response body.

    Raises:
        RuntimeError: On HTTP or JSON errors.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    for ctx_msg in contexts or []:
        messages.append(ctx_msg)
    messages.append({"role": "user", "content": user})

    payload: dict[str, Any] = {
        "model": _LLM_MODEL,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_LLM_API_BASE}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {_LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM API HTTP {exc.code}: {body}") from exc


def _extract_completion_text(response: dict[str, Any]) -> str:
    """Pull the assistant text from a chat completion response."""
    choices = response.get("choices", [])
    if not choices:
        raise RuntimeError("LLM response has no choices")
    message = choices[0].get("message", {})
    # tool_calls are not protocol output — skip counting them as failures
    if message.get("tool_calls"):
        return ""
    return str(message.get("content") or "").strip()


@pytest.mark.skipif(
    not _LLM_ENABLED,
    reason="set HUMANIZE_VALIDATION_LLM=1 to enable real-LLM probe",
)
def test_real_llm_protocol_compliance() -> None:
    """Measure protocol pass rate across N real LLM responses.

    Sends each test message with the Humanize protocol prompt injected into
    the system.  Asserts ≥ 75 % of responses pass the parser on the first try.
    """
    if not _LLM_API_KEY:
        pytest.skip("HUMANIZE_TEST_API_KEY not set")

    config = PluginConfig()
    envelope = EnvelopeBuilder(config)
    parser = ProtocolParser(config)

    dummy_ctx = _make_context("llm-probe", request_id="probe", user_text="")
    system_prompt = envelope.build_protocol_prompt(dummy_ctx)

    passed = 0
    repaired = 0
    failed: list[dict[str, str]] = []
    messages_to_test = (_LLM_TEST_MESSAGES * 4)[:_LLM_SAMPLES]

    for user_text in messages_to_test:
        msg_xml = envelope.build_message_xml(user_text)
        try:
            raw_response = _call_llm(system_prompt, msg_xml)
            completion = _extract_completion_text(raw_response)
        except Exception as exc:  # noqa: BLE001
            failed.append({"user": user_text, "error": str(exc), "raw": ""})
            continue

        if not completion:
            continue  # tool call or empty — not a protocol violation

        try:
            parser.parse(completion)
            passed += 1
        except ProtocolValidationError as parse_exc:
            candidate = ProtocolParser.extract_repair_candidate(completion)
            if candidate is not None:
                body, action = candidate
                sim_repair = (
                    f"<Action>{action}</Action>\n<UnknownTerms>[]</UnknownTerms>"
                )
                try:
                    parser.parse(
                        ProtocolParser.compose_repaired_response(sim_repair, body)
                    )
                    repaired += 1
                    continue
                except ProtocolValidationError:
                    pass
            failed.append(
                {
                    "user": user_text,
                    "error": parse_exc.code,
                    "raw": completion[:200],
                }
            )

    total_evaluated = passed + repaired + len(failed)
    if total_evaluated == 0:
        pytest.skip("no responses were evaluated (all were tool calls or empty)")

    first_pass_rate = passed / total_evaluated
    combined_rate = (passed + repaired) / total_evaluated
    print(
        f"\n[real-LLM] model={_LLM_MODEL} n={total_evaluated} "
        f"pass={passed} repaired={repaired} failed={len(failed)}\n"
        f"  first-pass  : {first_pass_rate:.1%}\n"
        f"  after-repair: {combined_rate:.1%}"
    )
    for item in failed:
        print(f"  ✗ [{item['error']}] {item['user']!r} → {item['raw']!r}")

    assert first_pass_rate >= 0.75, (
        f"real-LLM first-pass rate {first_pass_rate:.1%} "
        f"({passed}/{total_evaluated}) is below 75 %"
    )


@pytest.mark.skipif(
    not _LLM_ENABLED,
    reason="set HUMANIZE_VALIDATION_LLM=1 to enable real-LLM probe",
)
def test_real_llm_tool_call_then_protocol_compliance() -> None:
    """Verify protocol compliance after a simulated tool-call exchange.

    Sends a conversation where the assistant has already issued a tool call
    and received the result.  The model must still produce a valid protocol
    response for the final user-visible turn.
    """
    if not _LLM_API_KEY:
        pytest.skip("HUMANIZE_TEST_API_KEY not set")

    config = PluginConfig()
    envelope = EnvelopeBuilder(config)
    parser = ProtocolParser(config)

    dummy_ctx = _make_context("llm-tool-probe", request_id="tool-probe", user_text="")
    system_prompt = envelope.build_protocol_prompt(dummy_ctx)

    # Pre-baked tool call history injected as prior conversation context
    tool_history: list[dict[str, str]] = [
        {"role": "user", "content": "<Msg>帮我搜索今天北京天气</Msg>"},
        {
            "role": "assistant",
            "content": (
                "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n"
                "<Messages><Message>正在为你查询……</Message></Messages>"
            ),
        },
        {"role": "tool", "content": "北京今天晴，气温 25 °C，空气质量良好。"},
    ]
    final_user = envelope.build_message_xml("结果怎么样？")

    try:
        raw_response = _call_llm(system_prompt, final_user, contexts=tool_history)
        completion = _extract_completion_text(raw_response)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"LLM call failed: {exc}")
        return

    if not completion:
        pytest.skip("LLM returned a tool call (no text content to validate)")

    try:
        decision = parser.parse(completion)
    except ProtocolValidationError as exc:
        pytest.fail(
            f"Protocol validation failed after tool-call history: "
            f"{exc.code} — {exc.detail}\nRaw: {completion[:300]}"
        )
        return

    assert decision.action in (Action.REPLY, Action.NO_REPLY), (
        f"unexpected action: {decision.action!r}"
    )
    print(
        f"\n[tool-probe] action={decision.action.value} "
        f"messages={len(decision.messages)}"
    )
