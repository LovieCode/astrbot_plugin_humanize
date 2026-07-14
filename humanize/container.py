from __future__ import annotations

from dataclasses import dataclass

from .config import PluginConfig
from .jargon.matcher import JargonMatcher
from .protocol.envelope import EnvelopeBuilder
from .protocol.parser import ProtocolParser
from .repositories.sqlite import SQLiteRepository
from .services.humanize import HumanizeService
from .web.routes import WebApi


@dataclass(slots=True)
class Container:
    config: PluginConfig
    repository: SQLiteRepository
    service: HumanizeService
    web_api: WebApi

    @classmethod
    def build(cls, config: PluginConfig) -> Container:
        repository = SQLiteRepository(
            config.data_path() / "humanize.db",
            raw_log_chars=config.protocol_raw_log_chars,
            log_retention_days=config.protocol_log_retention_days,
        )
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=EnvelopeBuilder(config),
            parser=ProtocolParser(config),
            matcher=JargonMatcher(),
        )
        return cls(
            config=config,
            repository=repository,
            service=service,
            web_api=WebApi(repository, config),
        )
