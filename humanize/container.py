from __future__ import annotations

from dataclasses import dataclass

from .config import PluginConfig
from .context.composer import ContextComposer
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
from .web.routes import WebApi


@dataclass(slots=True)
class Container:
    config: PluginConfig
    repository: SQLiteRepository
    envelope: EnvelopeBuilder
    memory: ChatMemoryService
    service: HumanizeService
    provider_catalog: ProviderCatalog
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
            service=service,
            provider_catalog=provider_catalog,
            web_api=WebApi(
                repository,
                config,
                envelope,
                provider_catalog,
                memory=memory,
            ),
        )
