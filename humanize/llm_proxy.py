"""Unified LLM call proxy context for full-chain usage tracing.

The plugin patches every AstrBot ``Provider`` subclass once (see
``main.py`` ``_install_provider_hooks``). Auxiliary LLM calls made by the
plugin itself — image transcription, memory extraction, OpenViking — wrap
their ``text_chat`` calls in :func:`llm_call_context`, which tags the async
context so the patched provider method can persist the provider-reported
real usage (tokens, duration, model) into ``humanize_llm_call_log``.

Pipeline calls already persist real usage through
``record_llm_usage_sample``; they stay out of the proxy log to avoid
double counting. Calls without a context (other plugins, core) are passed
through untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any

from .provider_observability import usage_dict, usage_observed

__all__ = [
    "current_llm_call_context",
    "llm_call_context",
    "llm_response_usage",
]

_CALL_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "humanize_llm_call_context", default=None
)


@asynccontextmanager
async def llm_call_context(
    call_type: str,
    *,
    request_id: str = "",
    scope_type: str = "",
    scope_id: str = "",
    conversation_id: str = "",
) -> AsyncIterator[dict[str, str]]:
    """Tag LLM calls made inside the block for proxy-side usage recording.

    Args:
        call_type: Stable stage identifier such as ``transcribe_sticker``,
            ``transcribe_image``, ``extract`` or ``openviking``.
        request_id: Optional pipeline request linkage.
        scope_type: Optional scope type for tracing.
        scope_id: Optional scope identifier for tracing.
        conversation_id: Optional conversation identifier.

    Yields:
        The immutable context mapping visible to the provider hook.
    """
    context = {
        "call_type": str(call_type or "").strip()[:40],
        "request_id": str(request_id or "")[:200],
        "scope_type": str(scope_type or "")[:120],
        "scope_id": str(scope_id or "")[:300],
        "conversation_id": str(conversation_id or "")[:300],
    }
    token: Token[dict[str, str] | None] = _CALL_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CALL_CONTEXT.reset(token)


def current_llm_call_context() -> dict[str, str] | None:
    """Return the active proxy context, or ``None`` for untagged calls.

    Returns:
        The context mapping set by :func:`llm_call_context`, or ``None``.
    """
    return _CALL_CONTEXT.get()


def llm_response_usage(response: Any) -> tuple[dict[str, int], bool]:
    """Extract provider-reported usage from a finished LLM response.

    Args:
        response: ``LLMResponse``-like object returned by ``text_chat``.

    Returns:
        ``(usage, observed)`` where usage holds provider-reported
        ``input_cached`` / ``input_other`` / ``output`` tokens and observed
        marks whether the provider actually supplied a usage object.
    """
    raw_completion = getattr(response, "raw_completion", None)
    raw_usage = (
        getattr(raw_completion, "usage", None) if raw_completion is not None else None
    )
    response_usage = getattr(response, "usage", None)
    normalized = usage_dict(response_usage)
    if normalized is None or not any(normalized.values()):
        # 适配器可能构造空 TokenUsage；此时以 raw usage 为准（真实回报）。
        raw_normalized = usage_dict(raw_usage)
        if raw_normalized is not None and any(raw_normalized.values()):
            normalized = raw_normalized
    if normalized is None:
        normalized = {"input_cached": 0, "input_other": 0, "output": 0}
    observed = bool(usage_observed(response_usage, raw_usage=raw_usage))
    if not observed:
        # An empty TokenUsage must not masquerade as measured zero usage.
        return {"input_cached": 0, "input_other": 0, "output": 0}, False
    return {
        key: max(0, int(normalized.get(key, 0) or 0))
        for key in ("input_cached", "input_other", "output")
    }, True
