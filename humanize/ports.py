from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .domain.models import ContextSection, KnownTerm, MessageContext, UnknownTerm


class RepositoryPort(Protocol):
    async def initialize(self) -> None: ...

    async def get_prompt_templates(self) -> dict[str, Any]: ...

    async def update_prompt_templates(
        self,
        value: dict[str, Any],
        *,
        actor: str = "web_admin",
        reason: str = "web update",
        action: str = "update",
    ) -> dict[str, Any]: ...

    async def list_prompt_template_audit(
        self, *, page: int, page_size: int
    ) -> dict[str, Any]: ...

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
        messages: Sequence[str] = (),
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool = False,
        model: str,
        duration_ms: int,
        stage: str = "final",
    ) -> None: ...

    async def record_protocol_and_enqueue_memory(
        self,
        context: MessageContext,
        outcome: Any | None = None,
        raw_output: str = "",
        model: str = "",
        duration_ms: int = 0,
        request_snapshot: dict[str, Any] | None = None,
        actual_messages: Sequence[str] = (),
        provider_id: str = "",
        scope_type: str = "",
        scope_hash: str = "",
        subject_hash: str = "",
        conversation_hash: str = "",
        action: str = "reply",
        *,
        memory_job: dict[str, Any] | None = None,
        success: bool = True,
        failure_code: str = "",
        failure_detail: str = "",
        messages: Sequence[str] = (),
        response_snapshot: dict[str, Any] | None = None,
        response_snapshot_complete: bool | None = None,
        stage: str = "final",
    ) -> dict[str, Any]: ...

    async def claim_memory_job(
        self, lease_owner: str, lease_seconds: int = 60
    ) -> dict[str, Any] | None: ...

    async def claim_memory_job_batch(
        self,
        lease_owner: str,
        lease_seconds: int = 90,
        batch_turns: int = 4,
        idle_seconds: int = 180,
    ) -> list[dict[str, Any]]: ...

    async def renew_memory_job(
        self, job_id: int, lease_owner: str, lease_seconds: int = 90
    ) -> bool: ...

    async def release_memory_job(
        self,
        job_id: int,
        lease_owner: str,
        reason: str = "worker_cancelled",
    ) -> bool: ...

    async def complete_memory_job(
        self, job_id: int, lease_owner: str
    ) -> dict[str, Any]: ...

    async def retry_memory_job(
        self,
        job_id: int,
        lease_owner: str,
        error: str,
        max_attempts: int,
        delay_seconds: int,
    ) -> dict[str, Any]: ...

    async def list_recallable_reply_examples(
        self,
        scope_filters: Any,
        min_quality: float = 0.0,
        agent_id: str = "default",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    async def search_reply_examples(
        self,
        scope_filters: Any,
        query: str,
        limit: int,
        min_quality: float,
        agent_id: str = "default",
    ) -> list[dict[str, Any]]: ...

    async def record_reply_example_usage(self, **kwargs: Any) -> None: ...

    async def upsert_embedding(
        self,
        entity_type: str,
        entity_id: int,
        provider_id: str,
        model: str,
        dimension: int,
        vector: Sequence[float],
        generation: str | int,
    ) -> dict[str, Any]: ...

    async def list_embeddings(
        self,
        entity_type: str = "",
        provider_id: str = "",
        model: str = "",
        generation: str | int = "",
        entity_ids: Sequence[int] = (),
    ) -> list[dict[str, Any]]: ...

    async def list_memory_jobs(
        self, filters: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def list_reply_examples(
        self, filters: dict[str, Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]: ...

    async def get_reply_example_detail(
        self, example_id: int
    ) -> dict[str, Any] | None: ...

    async def apply_reply_example_action(
        self, payload: dict[str, Any], actor: str = "web_admin"
    ) -> dict[str, Any]: ...

    async def record_context_run(
        self,
        context: MessageContext,
        sections: Sequence[ContextSection],
        protocol_mode: str,
        request_snapshot: dict[str, Any] | None = None,
        request_snapshot_complete: bool = False,
    ) -> None: ...

    async def list_context_runs(
        self,
        *,
        scope_type: str,
        scope_id: str,
        section_key: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]: ...

    async def get_context_run(self, request_id: str) -> dict[str, Any] | None: ...

    async def get_context_stats(self, *, days: int) -> dict[str, Any]: ...

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
        self,
        entry_id: int,
        action: str,
        meaning: str = "",
        *,
        payload: dict[str, Any] | None = None,
    ) -> bool: ...

    async def export_jargons(
        self,
        *,
        search: str = "",
        scope_type: str = "",
        scope_id: str = "",
        status: str = "",
    ) -> dict[str, Any]: ...

    async def list_protocol_logs(
        self, *, page: int, page_size: int
    ) -> dict[str, Any]: ...

    async def record_llm_usage_sample(self, **kwargs: Any) -> None: ...

    async def get_latest_prompt_prefix_sample(
        self, *, scope_type: str, scope_id: str, conversation_id: str
    ) -> dict[str, Any] | None: ...
