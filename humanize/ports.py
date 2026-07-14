from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .domain.models import KnownTerm, MessageContext, UnknownTerm


class RepositoryPort(Protocol):
    async def initialize(self) -> None: ...

    async def list_injectable_terms(
        self,
        scope_type: str,
        scope_id: str,
        min_confidence: float,
        limit: int = 500,
    ) -> list[KnownTerm]: ...

    async def ingest_unknown_terms(
        self,
        context: MessageContext,
        terms: Sequence[UnknownTerm],
        provisional_threshold: float,
        max_evidence: int,
    ) -> list[int]: ...

    async def record_injections(
        self,
        context: MessageContext,
        selected: Sequence[KnownTerm],
        reason: str,
    ) -> None: ...

    async def record_protocol(
        self,
        context: MessageContext,
        *,
        success: bool,
        action: str,
        failure_code: str,
        failure_detail: str,
        raw_output: str,
        model: str,
        duration_ms: int,
    ) -> None: ...

    async def get_overview(self) -> dict[str, Any]: ...

    async def list_jargons(
        self,
        *,
        search: str,
        status: str,
        scope_id: str,
        page: int,
        page_size: int,
        scope_type: str = "",
    ) -> dict[str, Any]: ...

    async def get_jargon_detail(self, entry_id: int) -> dict[str, Any] | None: ...

    async def apply_jargon_action(
        self, entry_id: int, action: str, meaning: str = ""
    ) -> bool: ...

    async def list_protocol_logs(
        self, *, page: int, page_size: int
    ) -> dict[str, Any]: ...
