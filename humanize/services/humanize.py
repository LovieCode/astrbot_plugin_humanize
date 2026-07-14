from __future__ import annotations

import logging
from collections.abc import Sequence

from ..config import PluginConfig
from ..domain.errors import ProtocolValidationError
from ..domain.models import (
    FinalOutcome,
    MessageContext,
    PreparedRequest,
    UnknownTerm,
)
from ..jargon.matcher import JargonMatcher
from ..jargon.normalizer import is_valid_candidate, normalize_term
from ..ports import RepositoryPort
from ..protocol.envelope import EnvelopeBuilder
from ..protocol.parser import ProtocolParser

logger = logging.getLogger("astrbot")


class HumanizeService:
    def __init__(
        self,
        config: PluginConfig,
        repository: RepositoryPort,
        envelope: EnvelopeBuilder,
        parser: ProtocolParser,
        matcher: JargonMatcher,
    ) -> None:
        self._config = config
        self._repository = repository
        self._envelope = envelope
        self._parser = parser
        self._matcher = matcher

    async def prepare_request(self, context: MessageContext) -> PreparedRequest:
        selected = ()
        if self._config.jargon_enabled and self._config.max_injected_jargons > 0:
            try:
                terms = await self._repository.list_injectable_terms(
                    context.scope_type,
                    context.scope_id,
                    self._config.min_confidence_for_injection,
                )
                selected = self._matcher.select(
                    terms,
                    context.user_text,
                    max_count=self._config.max_injected_jargons,
                    char_budget=self._config.injection_char_budget,
                )
                await self._repository.record_injections(
                    context,
                    selected,
                    "matched_current_message"
                    if selected
                    else "no_matching_trusted_term",
                )
            except Exception:
                logger.exception("[Humanize] failed to prepare jargon injection")

        return PreparedRequest(
            protocol_prompt=self._envelope.build_protocol_prompt(context),
            message_xml=self._envelope.build_message_xml(context.user_text),
            known_terms_xml=self._envelope.build_known_terms_xml(selected),
            matched_terms=selected,
        )

    async def process_final_response(
        self,
        context: MessageContext,
        raw_output: str,
        *,
        model: str,
        duration_ms: int,
    ) -> FinalOutcome:
        try:
            decision = self._parser.parse(raw_output)
        except ProtocolValidationError as exc:
            await self._record_protocol_safely(
                context,
                success=False,
                action="",
                failure_code=exc.code,
                failure_detail=exc.detail,
                raw_output=raw_output,
                model=model,
                duration_ms=duration_ms,
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

        await self._record_protocol_safely(
            context,
            success=True,
            action=decision.action.value,
            failure_code="",
            failure_detail="",
            raw_output=raw_output,
            model=model,
            duration_ms=duration_ms,
        )
        return FinalOutcome(
            valid=True,
            action=decision.action,
            messages=decision.messages,
            unknown_terms=unknown_terms,
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

    async def _record_protocol_safely(self, context: MessageContext, **kwargs) -> None:
        try:
            await self._repository.record_protocol(context, **kwargs)
        except Exception:
            logger.exception("[Humanize] failed to persist protocol log")
