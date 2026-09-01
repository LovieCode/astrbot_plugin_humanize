from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .config import PluginConfig
from .context.composer import ContextComposer
from .context.summarizer import ContextSummarizer
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
    def build(
        cls,
        config: PluginConfig,
        context: object | None = None,
        *,
        proactive_event_builder: Any | None = None,
        proactive_event_queue_getter: Any | None = None,
    ) -> Container:
        """Assemble every service.

        Args:
            config: Plugin configuration.
            context: AstrBot host context when running inside the framework.
            proactive_event_builder: Callable
                ``(template_event, *, kind, on_outcome) -> event | None``
                that produces the synthetic group event for one proactive
                turn. Injected by the plugin because it needs AstrBot event
                classes; omitted by unit tests, which disable triggering.
            proactive_event_queue_getter: Callable returning the host event
                queue the synthetic event is pushed into.
        """
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
        if config.context_summary_enabled and config.memory_extraction_provider_id:
            # 摘要用独立的 Provider 桥：超时比召回宽（长文本压缩），
            # 复用记忆抽取 Provider 的选择，未配置时保持确定性摘要。
            context_window.attach_summarizer(
                ContextSummarizer(
                    OpenVikingProviderBridge(
                        context,
                        chat_provider_id=config.memory_extraction_provider_id,
                        timeout_seconds=config.context_summary_timeout_seconds,
                    ),
                    max_chars=config.context_summary_max_chars,
                )
            )
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
                proactive_event_builder,
                proactive_event_queue_getter,
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
    event_builder: Any | None,
    event_queue_getter: Any | None,
) -> ProactiveService:
    """Assemble the proactive trigger service.

    The service stays framework-free: the injected builder produces the
    synthetic group event (AstrBot classes live in the plugin entry module)
    and the queue getter hands over the host event queue. Both may be
    ``None`` in unit tests, which disables triggering entirely.
    """
    return ProactiveService(
        config,
        repository,
        event_builder=event_builder,
        event_queue_getter=event_queue_getter,
    )
