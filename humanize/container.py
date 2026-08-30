from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import PluginConfig
from .context.composer import ContextComposer
from .context.window import ContextWindowService
from .jargon.matcher import JargonMatcher
from .memory import ChatMemoryService
from .openviking import (
    OpenVikingManagementAdapter,
    OpenVikingMemoryAdapter,
    OpenVikingProviderBridge,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)
from .protocol.envelope import EnvelopeBuilder
from .protocol.parser import ProtocolParser
from .provider_catalog import ProviderCatalog
from .repositories.sqlite import SQLiteRepository
from .services.humanize import HumanizeService
from .services.proactive import ProactiveService
from .web.routes import WebApi

logger = logging.getLogger("astrbot")


def context_window_token_budget(provider_settings: dict[str, Any]) -> int:
    """Reserve a bounded portion of the active Provider context for history.

    Shared by the normal request hook and the proactive evaluation so both
    load the managed window with the same budget.

    Args:
        provider_settings: Current AstrBot provider settings for one session.

    Returns:
        Approximate token budget used by the managed context window.
    """
    try:
        configured = int(provider_settings.get("max_context_length", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        configured = 0
    if configured <= 0:
        return 6_000
    return max(512, min(8_000, configured // 4))


@dataclass(slots=True)
class Container:
    config: PluginConfig
    repository: SQLiteRepository
    envelope: EnvelopeBuilder
    memory: ChatMemoryService
    context_window: ContextWindowService
    service: HumanizeService
    provider_catalog: ProviderCatalog
    proactive: ProactiveService
    web_api: WebApi

    @classmethod
    def build(cls, config: PluginConfig, context: object | None = None) -> Container:
        repository = SQLiteRepository(
            config.data_path() / "humanize.db",
            raw_log_chars=config.protocol_raw_log_chars,
            log_retention_days=config.protocol_log_retention_days,
        )
        openviking_workspace = OpenVikingWorkspace(config.data_path())
        openviking = OpenVikingMemoryAdapter(openviking_workspace)
        openviking_providers = OpenVikingProviderBridge(
            context,
            chat_provider_id=config.memory_extraction_provider_id,
            embedding_provider_id=config.memory_embedding_provider_id,
            rerank_provider_id=config.memory_rerank_provider_id,
            timeout_seconds=config.memory_recall_timeout_seconds,
        )
        openviking_recall = OpenVikingRecallAdapter(
            openviking_workspace,
            openviking_providers,
        )
        openviking_management = OpenVikingManagementAdapter(
            openviking,
            openviking_workspace,
        )
        memory = ChatMemoryService(
            config,
            repository,
            context,
            openviking_adapter=openviking,
            openviking_recall_adapter=openviking_recall,
            openviking_management_adapter=openviking_management,
        )
        context_window = ContextWindowService(openviking_workspace, memory)
        envelope = EnvelopeBuilder(config)
        matcher = JargonMatcher()
        composer = ContextComposer(
            config=config,
            repository=repository,
            envelope=envelope,
            matcher=matcher,
            memory=memory,
        )
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=envelope,
            parser=ProtocolParser(config),
            matcher=matcher,
            composer=composer,
            memory=memory,
        )
        provider_catalog = ProviderCatalog(context)
        return cls(
            config=config,
            repository=repository,
            envelope=envelope,
            memory=memory,
            context_window=context_window,
            service=service,
            provider_catalog=provider_catalog,
            proactive=_build_proactive(
                config,
                repository,
                envelope,
                service,
                context_window,
                context,
            ),
            web_api=WebApi(
                repository,
                config,
                envelope,
                provider_catalog,
                memory=memory,
            ),
        )


def _build_proactive(
    config: PluginConfig,
    repository: SQLiteRepository,
    envelope: EnvelopeBuilder,
    service: HumanizeService,
    context_window: ContextWindowService,
    context: object | None,
) -> ProactiveService:
    """Assemble the proactive service with AstrBot-facing callables.

    The service itself stays framework-free; the callables bridge to the
    host context and degrade to inert behaviour when it is unavailable
    (unit tests build a Container without one).
    """

    def provider_getter(umo: str) -> Any:
        getter = getattr(context, "get_using_provider", None)
        if not callable(getter):
            return None
        try:
            return getter(umo)
        except Exception:
            logger.exception("[Humanize] failed to resolve the provider for %s", umo)
            return None

    async def message_sender(umo: str, text: str) -> None:
        if context is None:
            logger.error(
                "[Humanize] proactive send skipped: no host context for %s", umo
            )
            return
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Plain

        await context.send_message(umo, MessageChain([Plain(text)]))  # type: ignore[union-attr]

    async def persona_getter(umo: str) -> tuple[str, str]:
        """Resolve the group's persona prompt and its managed-window agent id.

        Returns:
            (persona system prompt, agent_id); agent id must match the one
            normal turns use, otherwise the proactive evaluation would read
            a different (empty) managed window.
        """
        resolver = getattr(
            getattr(context, "persona_manager", None),
            "resolve_selected_persona",
            None,
        )
        if not callable(resolver):
            return "", "default"
        provider_settings: dict[str, Any] = {}
        config_getter = getattr(context, "get_config", None)
        if callable(config_getter):
            try:
                candidate = config_getter(umo=umo).get("provider_settings", {})
                if isinstance(candidate, dict):
                    provider_settings = candidate
            except Exception:
                logger.exception(
                    "[Humanize] failed to read provider settings for %s", umo
                )
        try:
            (
                persona_id,
                persona_obj,
                _forced_id,
                use_webchat_default,
            ) = await resolver(
                umo=umo,
                conversation_persona_id=None,
                platform_name="",
                provider_settings=provider_settings,
            )
        except Exception:
            logger.exception("[Humanize] failed to resolve the persona for %s", umo)
            return "", "default"
        agent_id = (
            "_chatui_default_" if use_webchat_default else str(persona_id or "default")
        )
        prompt = (
            str(persona_obj.get("prompt") or "")
            if isinstance(persona_obj, dict)
            else ""
        )
        return prompt, agent_id

    def window_budget_getter(umo: str) -> int:
        config_getter = getattr(context, "get_config", None)
        if callable(config_getter):
            try:
                candidate = config_getter(umo=umo).get("provider_settings", {})
                if isinstance(candidate, dict):
                    return context_window_token_budget(candidate)
            except Exception:
                logger.exception(
                    "[Humanize] failed to read the context budget for %s", umo
                )
        return context_window_token_budget({})

    return ProactiveService(
        config,
        repository,
        context_window,
        service,
        envelope,
        provider_getter=provider_getter,
        message_sender=message_sender,
        persona_getter=persona_getter,
        window_budget_getter=window_budget_getter,
    )
