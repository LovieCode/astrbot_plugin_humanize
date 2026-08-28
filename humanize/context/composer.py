from __future__ import annotations

import asyncio
import logging

from astrbot.core.agent.context.token_counter import EstimateTokenCounter, TokenCounter
from astrbot.core.agent.message import Message

from ..config import PluginConfig
from ..domain.models import ContextSection, KnownTerm, MessageContext, PreparedRequest
from ..jargon.matcher import JargonMatcher
from ..memory import ChatMemoryService, RecallResult
from ..ports import RepositoryPort
from ..protocol.envelope import EnvelopeBuilder

logger = logging.getLogger("astrbot")


class ContextComposer:
    """Build deterministic, budget-aware context sections for one request."""

    def __init__(
        self,
        config: PluginConfig,
        repository: RepositoryPort,
        envelope: EnvelopeBuilder,
        matcher: JargonMatcher,
        token_counter: TokenCounter | None = None,
        memory: ChatMemoryService | None = None,
    ) -> None:
        """Initialize the context composer.

        Args:
            config: Validated plugin configuration.
            repository: Shared repository used to retrieve trusted jargon.
            envelope: Builder for untrusted message and protocol boundaries.
            matcher: Scope-local jargon matcher.
            token_counter: Optional deterministic token counter for tests.
            memory: Optional shared internal memory service.
        """
        self._config = config
        self._repository = repository
        self._envelope = envelope
        self._matcher = matcher
        self._token_counter = token_counter or EstimateTokenCounter()
        self._memory = memory

    async def compose(
        self,
        context: MessageContext,
        *,
        include_session_fallback: bool = True,
    ) -> PreparedRequest:
        """Compose the current message, response protocol, and known terms.

        Args:
            context: Trusted metadata and the current untrusted user message.
            include_session_fallback: Whether OpenViking may inject its bounded
                Session continuity fallback when semantic memory is absent.

        Returns:
            A backward-compatible prepared request with an ordered section trace.
        """
        message_xml = self._envelope.build_message_xml(context.user_text)
        protocol_prompt = self._envelope.build_protocol_prompt(context)

        selected: tuple[KnownTerm, ...] = ()
        jargon_reason = "jargon_disabled"
        candidate_count = 0
        if self._config.jargon_enabled and self._config.max_injected_jargons > 0:
            try:
                terms = await self._repository.list_injectable_terms(
                    context.scope_type,
                    context.scope_id,
                    self._config.min_confidence_for_injection,
                )
                ranked = self._matcher.select(
                    terms,
                    context.user_text,
                    max_count=max(1, len(terms)),
                    char_budget=2**31 - 1,
                )
                candidate_count = len(ranked)
                budgeted: list[KnownTerm] = []
                if self._config.max_injection_tokens > 0:
                    for term in ranked:
                        if len(budgeted) >= self._config.max_injected_jargons:
                            break
                        candidate = (*budgeted, term)
                        payload = self._envelope.build_known_terms_xml(candidate)
                        if (
                            self._count_tokens(payload)
                            <= self._config.max_injection_tokens
                        ):
                            budgeted.append(term)
                selected = tuple(budgeted)
                if selected and len(selected) < candidate_count:
                    jargon_reason = "matched_current_message_budgeted"
                elif selected:
                    jargon_reason = "matched_current_message"
                elif candidate_count:
                    jargon_reason = "token_budget_exhausted"
                else:
                    jargon_reason = "no_matching_trusted_term"
            except Exception:
                logger.exception("[Humanize] failed to compose known-term context")
                jargon_reason = "source_error"

        known_terms_xml = self._envelope.build_known_terms_xml(selected)
        memory_recall = RecallResult(False, "", (), 0, "not_initialized", 0)
        example_recall = RecallResult(False, "", (), 0, "not_initialized", 0)
        if self._memory is not None:
            memory_recall_call = (
                self._memory.recall_memories(context)
                if include_session_fallback
                else self._memory.recall_memories(
                    context,
                    include_session_fallback=False,
                )
            )
            memory_result, example_result = await asyncio.gather(
                memory_recall_call,
                self._memory.recall_examples(context, agent_id=context.agent_id),
                return_exceptions=True,
            )
            if isinstance(memory_result, BaseException):
                logger.error(
                    "[Humanize] failed to compose memory context: %s",
                    memory_result,
                )
                memory_recall = RecallResult(False, "", (), 0, "source_error", 0)
            else:
                memory_recall = memory_result
            if isinstance(example_result, BaseException):
                logger.error(
                    "[Humanize] failed to compose reply examples: %s",
                    example_result,
                )
                example_recall = RecallResult(False, "", (), 0, "source_error", 0)
            else:
                example_recall = example_result

        protocol_targets = (
            ("temp_user", "system")
            if self._config.protocol_injection_mode == "both"
            else ("temp_user",)
        )
        message_tokens = self._count_tokens(message_xml)
        jargon_tokens = self._count_tokens(known_terms_xml)
        memory_tokens = self._count_tokens(memory_recall.content)
        example_tokens = self._count_tokens(example_recall.content)
        protocol_tokens = self._count_tokens(protocol_prompt)
        sections = [
            ContextSection(
                key="current_message",
                ordinal=0,
                priority=100,
                source_type="message",
                source_refs=(f"message:{context.message_id}",),
                targets=("prompt",),
                required=True,
                included=True,
                budget_tokens=None,
                estimated_tokens=message_tokens,
                applied_tokens=message_tokens,
                item_count=1,
                reason="current_user_message",
                content=message_xml,
            ),
            ContextSection(
                key="known_terms",
                ordinal=1,
                priority=60,
                source_type="repository",
                source_refs=tuple(f"jargon:{term.entry_id}" for term in selected),
                targets=("temp_user",),
                required=False,
                included=True,
                budget_tokens=self._config.max_injection_tokens,
                estimated_tokens=jargon_tokens,
                applied_tokens=jargon_tokens,
                item_count=len(selected),
                reason=jargon_reason,
                content=known_terms_xml,
            ),
        ]
        sections.append(
            ContextSection(
                key="memory_context",
                ordinal=2,
                priority=70,
                source_type="memory",
                source_refs=memory_recall.source_refs,
                targets=("temp_user",),
                required=False,
                included=memory_recall.included,
                budget_tokens=max(1, self._config.memory_recall_max_chars // 4),
                estimated_tokens=memory_tokens,
                applied_tokens=memory_tokens if memory_recall.included else 0,
                item_count=memory_recall.item_count,
                reason=memory_recall.reason,
                content=self._wrap_memory(memory_recall.content)
                if memory_recall.included
                else memory_recall.content,
            )
        )
        sections.append(
            ContextSection(
                key="reply_examples",
                ordinal=3,
                priority=65,
                source_type="reply_examples",
                source_refs=example_recall.source_refs,
                targets=("temp_user",),
                required=False,
                included=example_recall.included,
                budget_tokens=max(1, self._config.reply_examples_max_chars // 4),
                estimated_tokens=example_tokens,
                applied_tokens=example_tokens if example_recall.included else 0,
                item_count=example_recall.item_count,
                reason=example_recall.reason,
                content=example_recall.content,
            )
        )
        sections.append(
            ContextSection(
                key="response_protocol",
                ordinal=4,
                priority=90,
                source_type="protocol",
                source_refs=(
                    "response_protocol",
                    f"default_rule:{int(self._config.default_rule_enabled)}",
                ),
                targets=protocol_targets,
                required=True,
                included=True,
                budget_tokens=None,
                estimated_tokens=protocol_tokens,
                applied_tokens=protocol_tokens * len(protocol_targets),
                item_count=1,
                reason="required_response_protocol",
                content=protocol_prompt,
            )
        )
        return PreparedRequest(
            protocol_prompt=protocol_prompt,
            message_xml=message_xml,
            known_terms_xml=known_terms_xml,
            matched_terms=selected,
            sections=tuple(sections),
        )

    def _wrap_memory(self, content: str) -> str:
        """Wrap recalled memory content in the protocol's <Memory> tag.

        Numbered plain lines are kept as-is; only the tag wrapper is added so
        the injected block matches the protocol's input spec.

        Args:
            content: Raw recalled memory text (possibly empty).

        Returns:
            Tag-wrapped memory block, or the original content when empty or
            already wrapped.
        """
        text = (content or "").strip()
        if not text or text.startswith("<Memory>"):
            return content
        return f"<Memory>\n{text}\n</Memory>"

    def _count_tokens(self, content: str) -> int:
        """Estimate text tokens through AstrBot's public counter interface.

        Args:
            content: Text fragment to estimate.

        Returns:
            Non-negative estimated token count.
        """
        return max(
            0,
            self._token_counter.count_tokens([Message(role="user", content=content)]),
        )
