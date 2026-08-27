from __future__ import annotations

from types import SimpleNamespace

import pytest
from astrbot_plugin_humanize.humanize.openviking import OpenVikingProviderBridge


class _ChatProvider:
    def __init__(self) -> None:
        self.payload: dict[str, object] = {}

    async def text_chat(self, **payload: object) -> SimpleNamespace:
        self.payload = payload
        return SimpleNamespace(
            role="assistant",
            tools_call_name=None,
            tools_call_args=None,
            completion_text="提取结果",
        )


class _EmbeddingProvider:
    def __init__(self) -> None:
        self.batch_calls = 0

    async def get_embedding(self, text: str) -> list[float]:
        return [3.0, 4.0] if text == "first" else [0.0, 2.0]

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [await self.get_embedding(text) for text in texts]


class _RerankProvider:
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int
    ) -> list[SimpleNamespace]:
        assert query == "query"
        assert documents == ["first", "second"]
        assert top_n == 2
        return [
            SimpleNamespace(index=1, relevance_score=0.9),
            SimpleNamespace(index=0, relevance_score=0.4),
        ]


class _ProviderContext:
    def __init__(self) -> None:
        self.chat = _ChatProvider()
        self.embedding = _EmbeddingProvider()
        self.providers = {
            "chat": self.chat,
            "embedding": self.embedding,
            "rerank": _RerankProvider(),
        }

    def get_provider_by_id(self, provider_id: str) -> object | None:
        return self.providers.get(provider_id)


@pytest.mark.asyncio
async def test_provider_bridge_forwards_all_supported_capabilities() -> None:
    context = _ProviderContext()
    bridge = OpenVikingProviderBridge(
        context,
        chat_provider_id="chat",
        embedding_provider_id="embedding",
        rerank_provider_id="rerank",
        timeout_seconds=1.0,
    )

    completion = await bridge.complete("input", system_prompt="system")
    vectors = await bridge.embed(("first", "second"))
    ranked = await bridge.rerank("query", ("first", "second"))

    assert completion == "提取结果"
    assert context.chat.payload["prompt"] == "input"
    assert context.chat.payload["system_prompt"] == "system"
    assert vectors == ((0.6, 0.8), (0.0, 1.0))
    assert context.embedding.batch_calls == 1
    assert [(item.index, item.score) for item in ranked] == [(1, 0.9), (0, 0.4)]


@pytest.mark.asyncio
async def test_provider_bridge_requires_explicit_provider_ids() -> None:
    bridge = OpenVikingProviderBridge(_ProviderContext())

    with pytest.raises(RuntimeError, match="Chat Provider is not configured"):
        await bridge.complete("input")
    with pytest.raises(RuntimeError, match="Embedding Provider is not configured"):
        await bridge.embed(("input",))
    with pytest.raises(RuntimeError, match="Rerank Provider is not configured"):
        await bridge.rerank("query", ("document",))


@pytest.mark.asyncio
async def test_provider_bridge_rejects_inconsistent_embedding_dimensions() -> None:
    class InconsistentEmbeddingProvider:
        async def get_embedding(self, text: str) -> list[float]:
            return [1.0] if text == "first" else [1.0, 0.0]

    context = _ProviderContext()
    context.providers["embedding"] = InconsistentEmbeddingProvider()
    bridge = OpenVikingProviderBridge(context, embedding_provider_id="embedding")

    with pytest.raises(ValueError, match="dimensions are inconsistent"):
        await bridge.embed(("first", "second"))
