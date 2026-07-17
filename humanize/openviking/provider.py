"""AstrBot Provider bridge for the embedded OpenViking adapters."""

from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderRerankResult:
    """Validated rerank result independent of AstrBot Provider classes."""

    index: int
    score: float


class OpenVikingProviderBridge:
    """Forward OpenViking model operations to explicitly selected Providers."""

    def __init__(
        self,
        context: Any | None,
        *,
        chat_provider_id: str = "",
        embedding_provider_id: str = "",
        rerank_provider_id: str = "",
        timeout_seconds: float = 5.0,
    ) -> None:
        """Configure Provider identities without resolving or calling them.

        Args:
            context: AstrBot context exposing ``get_provider_by_id``.
            chat_provider_id: Explicit Chat Provider identifier.
            embedding_provider_id: Explicit Embedding Provider identifier.
            rerank_provider_id: Explicit Rerank Provider identifier.
            timeout_seconds: Per-operation timeout in seconds.
        """
        self._context = context
        self._chat_provider_id = str(chat_provider_id or "").strip()
        self._embedding_provider_id = str(embedding_provider_id or "").strip()
        self._rerank_provider_id = str(rerank_provider_id or "").strip()
        self._timeout_seconds = max(0.2, min(float(timeout_seconds), 60.0))

    @property
    def embedding_enabled(self) -> bool:
        """Return whether an Embedding Provider was explicitly configured."""
        return bool(self._embedding_provider_id)

    @property
    def rerank_enabled(self) -> bool:
        """Return whether a Rerank Provider was explicitly configured."""
        return bool(self._rerank_provider_id)

    async def complete(self, prompt: str, *, system_prompt: str = "") -> str:
        """Request one text-only completion from the configured Chat Provider.

        Args:
            prompt: Bounded extraction or summarization input.
            system_prompt: Trusted system instruction for the operation.

        Returns:
            Non-empty assistant completion text.

        Raises:
            RuntimeError: If no compatible Chat Provider is configured.
            ValueError: If the Provider returns a non-text or empty response.
            TimeoutError: If the Provider exceeds the configured timeout.
        """
        provider = await self._resolve(self._chat_provider_id, "Chat")
        text_chat = getattr(provider, "text_chat", None)
        if not callable(text_chat):
            raise RuntimeError("configured Chat Provider is incompatible")
        response = await asyncio.wait_for(
            text_chat(
                prompt=str(prompt),
                session_id="",
                image_urls=[],
                audio_urls=[],
                func_tool=None,
                contexts=[],
                system_prompt=str(system_prompt),
                tool_calls_result=None,
                extra_user_content_parts=[],
                request_max_retries=1,
            ),
            timeout=self._timeout_seconds,
        )
        if (
            getattr(response, "role", "") != "assistant"
            or getattr(response, "tools_call_name", None)
            or getattr(response, "tools_call_args", None)
        ):
            raise ValueError("Chat Provider returned a non-text response")
        content = str(getattr(response, "completion_text", "") or "").strip()
        if not content:
            raise ValueError("Chat Provider returned an empty response")
        return content

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Return normalized vectors from the configured Embedding Provider.

        Args:
            texts: Non-empty texts to embed in the given order.

        Returns:
            L2-normalized vectors with one consistent dimension.

        Raises:
            RuntimeError: If no compatible Embedding Provider is configured.
            ValueError: If vectors are empty, non-finite, zero, or inconsistent.
            TimeoutError: If the Provider exceeds the configured timeout.
        """
        if not texts:
            return ()
        provider = await self._resolve(self._embedding_provider_id, "Embedding")
        getter = getattr(provider, "get_embedding", None)
        batch_getter = getattr(provider, "get_embeddings", None)
        if not callable(batch_getter) and not callable(getter):
            raise RuntimeError("configured Embedding Provider is incompatible")
        if callable(batch_getter):
            raw_vectors = await asyncio.wait_for(
                batch_getter([str(text) for text in texts]),
                timeout=self._timeout_seconds,
            )
        else:
            raw_vectors = await asyncio.wait_for(
                asyncio.gather(*(getter(str(text)) for text in texts)),
                timeout=self._timeout_seconds,
            )
        if not isinstance(raw_vectors, (list, tuple)) or len(raw_vectors) != len(texts):
            raise ValueError("Embedding Provider returned an unexpected vector count")
        vectors: list[tuple[float, ...]] = []
        dimension = 0
        for raw in raw_vectors:
            if not isinstance(raw, (list, tuple)) or not raw:
                raise ValueError("Embedding Provider returned an empty vector")
            vector = tuple(float(value) for value in raw)
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("Embedding Provider returned non-finite values")
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError("Embedding Provider returned a zero vector")
            normalized = tuple(value / norm for value in vector)
            if dimension and len(normalized) != dimension:
                raise ValueError("Embedding Provider dimensions are inconsistent")
            dimension = len(normalized)
            vectors.append(normalized)
        return tuple(vectors)

    async def rerank(
        self,
        query: str,
        documents: tuple[str, ...],
    ) -> tuple[ProviderRerankResult, ...]:
        """Rerank documents through the configured AstrBot Provider.

        Args:
            query: Current user query.
            documents: Candidate documents in stable source order.

        Returns:
            Complete validated ranking ordered by Provider relevance.

        Raises:
            RuntimeError: If no compatible Rerank Provider is configured.
            ValueError: If the Provider returns incomplete or invalid results.
            TimeoutError: If the Provider exceeds the configured timeout.
        """
        if not documents:
            return ()
        provider = await self._resolve(self._rerank_provider_id, "Rerank")
        rerank = getattr(provider, "rerank", None)
        if not callable(rerank):
            raise RuntimeError("configured Rerank Provider is incompatible")
        raw_results = await asyncio.wait_for(
            rerank(str(query), list(documents), top_n=len(documents)),
            timeout=self._timeout_seconds,
        )
        seen: set[int] = set()
        results: list[ProviderRerankResult] = []
        for raw in raw_results:
            index = int(getattr(raw, "index", -1))
            score = float(getattr(raw, "relevance_score", float("nan")))
            if (
                index < 0
                or index >= len(documents)
                or index in seen
                or not math.isfinite(score)
            ):
                raise ValueError("Rerank Provider returned invalid results")
            seen.add(index)
            results.append(ProviderRerankResult(index=index, score=score))
        if len(results) != len(documents):
            raise ValueError("Rerank Provider returned incomplete results")
        return tuple(results)

    async def _resolve(self, provider_id: str, capability: str) -> Any:
        """Resolve one explicit Provider without selecting a default.

        Args:
            provider_id: Explicit AstrBot Provider identifier.
            capability: Capability name used in safe errors.

        Returns:
            Resolved Provider instance.

        Raises:
            RuntimeError: If the Provider is unconfigured or unavailable.
        """
        getter = getattr(self._context, "get_provider_by_id", None)
        if not provider_id or not callable(getter):
            raise RuntimeError(f"{capability} Provider is not configured")
        provider = getter(provider_id)
        if inspect.isawaitable(provider):
            provider = await provider
        if provider is None:
            raise RuntimeError(f"{capability} Provider is unavailable")
        return provider
