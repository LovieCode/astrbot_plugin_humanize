from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from ..config import PluginConfig
from ..context.composer import ContextComposer
from ..domain.errors import ProtocolValidationError
from ..domain.models import (
    ContextSection,
    FinalOutcome,
    MessageContext,
    PreparedRequest,
    UnknownTerm,
)
from ..jargon.matcher import JargonMatcher
from ..jargon.normalizer import is_valid_candidate, normalize_term
from ..memory import ChatMemoryService
from ..ports import RepositoryPort
from ..protocol.envelope import EnvelopeBuilder
from ..protocol.parser import ProtocolParser

logger = logging.getLogger("astrbot")
_PROTOCOL_RECORD_ATTEMPTS = 3


class HumanizeService:
    def __init__(
        self,
        config: PluginConfig,
        repository: RepositoryPort,
        envelope: EnvelopeBuilder,
        parser: ProtocolParser,
        matcher: JargonMatcher,
        composer: ContextComposer | None = None,
        memory: ChatMemoryService | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._envelope = envelope
        self._parser = parser
        self._matcher = matcher
        self._memory = memory
        self._composer = composer or ContextComposer(
            config=config,
            repository=repository,
            envelope=envelope,
            matcher=matcher,
            memory=memory,
        )

    async def prepare_request(
        self,
        context: MessageContext,
        *,
        include_session_fallback: bool = True,
    ) -> PreparedRequest:
        """Prepare temporary context for one request.

        Args:
            context: Trusted current-message metadata.
            include_session_fallback: Whether memory recall may include OpenViking
                Session continuity when no semantic memory matches.

        Returns:
            Fully composed provider request sections.
        """
        return await self._composer.compose(
            context,
            include_session_fallback=include_session_fallback,
        )

    async def record_context_trace(
        self,
        context: MessageContext,
        sections: Sequence[ContextSection],
        *,
        request_snapshot: dict[str, Any] | None = None,
        request_snapshot_complete: bool = False,
        request_snapshot_final: dict[str, Any] | None = None,
        request_snapshot_final_complete: bool = False,
    ) -> bool:
        """Persist a trace only after the adapter applied every section.

        Args:
            context: Trusted identifiers for the active request.
            sections: Context sections successfully applied to the provider request.
            request_snapshot: Final provider request after every request hook mutation.
            request_snapshot_complete: Whether snapshot serialization was lossless.
            request_snapshot_final: Provider-visible context after the agent run,
                including model reasoning and the final response.
            request_snapshot_final_complete: Whether final serialization was lossless.

        Returns:
            ``True`` only when the context trace was persisted within the bounded
            retry budget.
        """
        for attempt in range(1, _PROTOCOL_RECORD_ATTEMPTS + 1):
            try:
                await self._repository.record_context_run(
                    context,
                    sections,
                    self._config.protocol_injection_mode,
                    request_snapshot,
                    request_snapshot_complete,
                    request_snapshot_final,
                    request_snapshot_final_complete,
                )
                return True
            except Exception:
                logger.exception(
                    "[Humanize] failed to persist context trace (attempt %s/%s)",
                    attempt,
                    _PROTOCOL_RECORD_ATTEMPTS,
                )
                if attempt < _PROTOCOL_RECORD_ATTEMPTS:
                    await asyncio.sleep(0.05 * attempt)
        return False

    async def update_context_trace_final_snapshot(
        self,
        context: MessageContext,
        *,
        request_snapshot_final: dict[str, Any] | None = None,
        request_snapshot_final_complete: bool = False,
    ) -> bool:
        """Update an existing context trace with the provider-visible final context.

        Args:
            context: Trusted identifiers for the active request.
            request_snapshot_final: Complete provider-visible context structure.
            request_snapshot_final_complete: Whether final serialization was lossless.

        Returns:
            ``True`` only when the update was persisted within the bounded retry
            budget.
        """
        for attempt in range(1, _PROTOCOL_RECORD_ATTEMPTS + 1):
            try:
                updated = await self._repository.update_context_run_final_snapshot(
                    context,
                    request_snapshot_final=request_snapshot_final,
                    request_snapshot_final_complete=request_snapshot_final_complete,
                )
                return updated
            except Exception:
                logger.exception(
                    "[Humanize] failed to persist final context snapshot "
                    "(attempt %s/%s)",
                    attempt,
                    _PROTOCOL_RECORD_ATTEMPTS,
                )
                if attempt < _PROTOCOL_RECORD_ATTEMPTS:
                    await asyncio.sleep(0.05 * attempt)
        return False

    async def process_final_response(
        self,
        context: MessageContext,
        raw_output: str,
        *,
        model: str,
        provider_id: str = "",
        duration_ms: int,
        stage: str = "final",
        record_success: bool = True,
        allow_wait: bool = False,
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
    ) -> FinalOutcome:
        try:
            decision = self._parser.parse(raw_output, allow_wait=allow_wait)
        except ProtocolValidationError as exc:
            await self._record_protocol_safely(
                context,
                success=False,
                action="",
                failure_code=exc.code,
                failure_detail=exc.detail,
                raw_output=raw_output,
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
                model=model,
                provider_id=provider_id,
                duration_ms=duration_ms,
                stage=stage,
            )
            return FinalOutcome(
                valid=False, error_code=exc.code, error_detail=exc.detail
            )

        unknown_terms = self._filter_unknown_terms(
            decision.unknown_terms, context.user_text
        )
        if self._config.jargon_enabled and unknown_terms:
            try:
                await self._repository.ingest_unknown_terms(
                    context,
                    unknown_terms,
                    self._config.min_confidence_for_injection,
                    self._config.max_evidence_per_entry,
                )
            except Exception:
                logger.exception("[Humanize] failed to persist unknown terms")

        # Providers sometimes add one formatting blank line after the header. Keep the
        # parser's exact body for repair/audit, but remove that framing blank before
        # the validated message reaches outbound dispatch and history.
        messages = tuple(
            message[2:]
            if message.startswith("\r\n")
            else message[1:]
            if message.startswith(("\n", "\r"))
            else message
            for message in decision.messages
        )
        if record_success:
            await self.record_protocol_success(
                context,
                action=decision.action.value,
                raw_output=raw_output,
                messages=messages,
                no_reply_reason=decision.no_reply_reason,
                response_snapshot=response_snapshot,
                response_snapshot_complete=response_snapshot_complete,
                model=model,
                provider_id=provider_id,
                duration_ms=duration_ms,
                stage=stage,
            )
        return FinalOutcome(
            valid=True,
            action=decision.action,
            messages=messages,
            unknown_terms=unknown_terms,
            image_cache=decision.image_cache,
            no_reply_reason=decision.no_reply_reason,
            messages_over_limit=decision.messages_over_limit,
            wait_seconds=decision.wait_seconds,
        )

    async def record_protocol_success(
        self,
        context: MessageContext,
        *,
        action: str,
        raw_output: str,
        messages: Sequence[str] = (),
        no_reply_reason: str = "",
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
        model: str,
        provider_id: str = "",
        context_ref: str = "",
        duration_ms: int,
        stage: str = "final",
    ) -> bool:
        """Persist a protocol success after the adapter reaches its terminal state.

        Args:
            context: Trusted identifiers for the active request.
            action: Validated Reply or No Reply decision.
            raw_output: Validated provider output associated with the decision.
            messages: Parsed user-visible messages after framing normalization.
            response_snapshot: Untouched provider response structure.
            response_snapshot_complete: Whether snapshot serialization was lossless.
            model: Provider model identifier.
            provider_id: Provider instance identifier captured for provenance.
            context_ref: Optional opaque L2 reference for the canonical managed
                context turn associated with this final response.
            duration_ms: Non-negative request duration in milliseconds.
            stage: Protocol stage, either final or tool.

        Returns:
            ``True`` only when the terminal record was persisted.
        """
        return await self._record_protocol_safely(
            context,
            success=True,
            action=action,
            failure_code="",
            failure_detail="",
            raw_output=raw_output,
            messages=tuple(str(message) for message in messages),
            no_reply_reason=str(no_reply_reason or ""),
            response_snapshot=response_snapshot,
            response_snapshot_complete=response_snapshot_complete,
            model=model,
            provider_id=provider_id,
            context_ref=context_ref,
            duration_ms=duration_ms,
            stage=stage,
        )

    async def record_protocol_failure(
        self,
        context: MessageContext,
        *,
        error_code: str,
        error_detail: str,
        raw_output: str,
        messages: Sequence[str] = (),
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
        model: str,
        duration_ms: int,
        stage: str = "final",
    ) -> bool:
        """Persist a protocol failure produced outside the response parser.

        Args:
            context: Trusted identifiers for the active request.
            error_code: Stable machine-readable failure code.
            error_detail: Bounded diagnostic detail for administrators.
            raw_output: Provider output associated with the failure.
            messages: Text already delivered before a partial dispatch failure.
            response_snapshot: Untouched provider response structure.
            response_snapshot_complete: Whether snapshot serialization was lossless.
            model: Provider model identifier.
            duration_ms: Non-negative request duration in milliseconds.
            stage: Protocol stage, either final or tool.

        Returns:
            ``True`` only when the terminal record was persisted.
        """
        return await self._record_protocol_safely(
            context,
            success=False,
            action="",
            failure_code=error_code,
            failure_detail=error_detail,
            raw_output=raw_output,
            messages=tuple(str(message) for message in messages),
            response_snapshot=response_snapshot,
            response_snapshot_complete=response_snapshot_complete,
            model=model,
            duration_ms=duration_ms,
            stage=stage,
        )

    def _filter_unknown_terms(
        self, terms: Sequence[UnknownTerm], source_text: str
    ) -> tuple[UnknownTerm, ...]:
        best_by_word: dict[str, UnknownTerm] = {}
        for term in terms:
            if not is_valid_candidate(
                term, source_text, max_chars=self._config.max_term_chars
            ):
                continue
            key = normalize_term(term.word)
            current = best_by_word.get(key)
            if current is None or term.confidence > current.confidence:
                best_by_word[key] = term
        return tuple(best_by_word.values())

    async def _record_protocol_safely(
        self, context: MessageContext, **kwargs: Any
    ) -> bool:
        """Persist one terminal protocol record with bounded retries.

        Args:
            context: Trusted request metadata.
            **kwargs: Repository protocol-log fields.

        Returns:
            ``True`` only when either the atomic or protocol-only write succeeds.
        """
        provider_id = str(kwargs.pop("provider_id", ""))
        context_ref = str(kwargs.pop("context_ref", ""))
        if (
            self._memory is not None
            and bool(kwargs.get("success", False))
            and kwargs.get("stage", "final") == "final"
        ):
            try:
                turn_job_kwargs: dict[str, Any] = {
                    "action": str(kwargs.get("action", "")),
                    "messages": tuple(kwargs.get("messages", ())),
                    "provider_id": provider_id,
                }
                if context_ref:
                    turn_job_kwargs["context_ref"] = context_ref
                memory_job = await self._memory.build_turn_job(
                    context,
                    **turn_job_kwargs,
                )
            except Exception:
                logger.exception("[Humanize] failed to build the memory turn job")
                memory_job = None
            recorder = getattr(
                self._repository, "record_protocol_and_enqueue_memory", None
            )
            if memory_job is not None and callable(recorder):
                for attempt in range(1, _PROTOCOL_RECORD_ATTEMPTS + 1):
                    try:
                        await recorder(context, memory_job=memory_job, **kwargs)
                        return True
                    except Exception:
                        logger.exception(
                            "[Humanize] failed to atomically persist protocol and "
                            "memory job (attempt %s/%s)",
                            attempt,
                            _PROTOCOL_RECORD_ATTEMPTS,
                        )
                        if attempt < _PROTOCOL_RECORD_ATTEMPTS:
                            await asyncio.sleep(0.05 * attempt)
                logger.error(
                    "[Humanize] falling back to protocol-only persistence after "
                    "atomic memory write failure"
                )

        for attempt in range(1, _PROTOCOL_RECORD_ATTEMPTS + 1):
            try:
                await self._repository.record_protocol(context, **kwargs)
                return True
            except Exception:
                logger.exception(
                    "[Humanize] failed to persist protocol log (attempt %s/%s)",
                    attempt,
                    _PROTOCOL_RECORD_ATTEMPTS,
                )
                if attempt < _PROTOCOL_RECORD_ATTEMPTS:
                    await asyncio.sleep(0.05 * attempt)
        return False
