from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import astrbot_plugin_humanize.humanize.memory as memory_module
import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.context.composer import ContextComposer
from astrbot_plugin_humanize.humanize.domain.models import Action, MessageContext
from astrbot_plugin_humanize.humanize.jargon.matcher import JargonMatcher
from astrbot_plugin_humanize.humanize.memory import ChatMemoryService
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.services.humanize import HumanizeService

_TEST_SECRET = b"humanize-memory-tests-use-a-stable-secret"


def _context(
    *,
    request_id: str = "request-1",
    scope_type: str = "private",
    scope_id: str = "private-chat-1",
    sender_id: str = "user-1",
    conversation_id: str = "conversation-1",
    message_id: str = "",
    user_text: str = "无糖乌龙茶",
    agent_id: str = "default",
) -> MessageContext:
    return MessageContext(
        request_id=request_id,
        scope_type=scope_type,
        scope_id=scope_id,
        message_id=message_id or f"message-{request_id}",
        sender_id=sender_id,
        sender_name="测试用户",
        user_text=user_text,
        chat_scene="QQ 私聊" if scope_type == "private" else "QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
        conversation_id=conversation_id,
        occurred_at="2026-07-16T00:00:00+00:00",
        agent_id=agent_id,
    )


def _memory_service(
    repository: object, config: PluginConfig | None = None
) -> ChatMemoryService:
    service = ChatMemoryService(config or PluginConfig(), repository)  # type: ignore[arg-type]
    service._secret = _TEST_SECRET
    service._state = "ready"
    service._reason = "test_identity_secret"
    return service


def test_hmac_identity_is_deterministic_and_isolated_by_chat_scope() -> None:
    service = _memory_service(object())
    private_a = _context()
    private_b = _context(conversation_id="conversation-2")
    other_platform_private = _context(
        scope_id="other-platform:private-chat-1",
        conversation_id="conversation-1",
    )
    other_private = _context(sender_id="user-2", conversation_id="conversation-3")
    group_a = _context(
        scope_type="group",
        scope_id="group-1",
        conversation_id="group-conversation-1",
    )
    group_same_member = _context(
        scope_type="group",
        scope_id="group-1",
        conversation_id="group-conversation-2",
    )
    group_other_member = _context(
        scope_type="group",
        scope_id="group-1",
        sender_id="user-2",
        conversation_id="group-conversation-1",
    )
    group_other_chat = _context(
        scope_type="group",
        scope_id="group-2",
        conversation_id="group-conversation-3",
    )

    private_identity = service.identity_for(private_a)
    repeated_identity = service.identity_for(private_a)
    private_other_conversation = service.identity_for(private_b)
    other_platform_private_identity = service.identity_for(other_platform_private)
    other_private_identity = service.identity_for(other_private)
    group_identity = service.identity_for(group_a)
    group_same_member_identity = service.identity_for(group_same_member)
    group_other_member_identity = service.identity_for(group_other_member)
    group_other_chat_identity = service.identity_for(group_other_chat)

    assert private_identity == repeated_identity
    assert private_identity.primary_scope_type == "private_user"
    assert private_identity.primary_scope_hash == (
        private_other_conversation.primary_scope_hash
    )
    assert private_identity.conversation_hash != (
        private_other_conversation.conversation_hash
    )
    assert private_identity.primary_scope_hash != (
        other_private_identity.primary_scope_hash
    )
    assert private_identity.primary_scope_hash != (
        other_platform_private_identity.primary_scope_hash
    )
    assert private_identity.subject_hash != (
        other_platform_private_identity.subject_hash
    )

    assert group_identity.primary_scope_type == "group_member"
    assert group_identity.primary_scope_hash == (
        group_same_member_identity.primary_scope_hash
    )
    assert group_identity.conversation_hash != (
        group_same_member_identity.conversation_hash
    )
    assert (
        group_identity.scopes[1]["scope_hash"]
        == (group_other_member_identity.scopes[1]["scope_hash"])
    )
    assert group_identity.primary_scope_hash != (
        group_other_member_identity.primary_scope_hash
    )
    assert (
        group_identity.scopes[1]["scope_hash"]
        != (group_other_chat_identity.scopes[1]["scope_hash"])
    )
    assert group_identity.subject_hash != group_other_chat_identity.subject_hash

    with pytest.raises(ValueError, match="sender id"):
        service.identity_for(_context(sender_id=""))

    serialized = json.dumps(
        {
            "private": private_identity.scopes,
            "group": group_identity.scopes,
            "subject": private_identity.subject_hash,
            "conversation": private_identity.conversation_hash,
        }
    )
    for raw_identifier in (
        "user-1",
        "private-chat-1",
        "conversation-1",
        "group-1",
    ):
        assert raw_identifier not in serialized


def test_scope_tokens_are_signed_and_do_not_expose_internal_hashes() -> None:
    service = _memory_service(object())
    identity = service.identity_for(_context())
    token = service.encode_scope_token(
        scope_type=identity.primary_scope_type,
        scope_hash=identity.primary_scope_hash,
        subject_hash=identity.subject_hash,
    )

    assert service.decode_scope_token(token) == {
        "scope_type": identity.primary_scope_type,
        "scope_hash": identity.primary_scope_hash,
        "subject_hash": identity.subject_hash,
    }
    assert identity.primary_scope_hash not in token
    replacement = "A" if token[-1] != "A" else "B"
    with pytest.raises(ValueError, match="作用域令牌"):
        service.decode_scope_token(f"{token[:-1]}{replacement}")


def test_reply_example_crud_recall_and_never_direct_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        service = _memory_service(repository)
        context = _context(user_text="今天怎么摸鱼", agent_id="agent-a")
        global_scope = service.identity_for(context).scopes[0]

        approved = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "轻松回应摸鱼",
                "scope_type": global_scope["scope_type"],
                "scope_hash": global_scope["scope_hash"],
                "agent_id": "agent-a",
                "topic": "摸鱼",
                "intent": "轻松闲聊",
                "keywords": ["摸鱼", "休息"],
                "style_tags": ["简短", "自然"],
                "turns": [
                    {"role": "user", "content": "今天怎么摸鱼"},
                    {"role": "assistant", "content": "先喘口气"},
                ],
                "ideal_reply": "先把最烦的那件事清掉，再安心摸一会。",
                "status": "approved",
                "enabled": True,
                "quality_score": 0.95,
            }
        )
        draft = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "未审核样例",
                "scope_type": global_scope["scope_type"],
                "scope_hash": global_scope["scope_hash"],
                "agent_id": "agent-a",
                "topic": "摸鱼",
                "keywords": ["摸鱼"],
                "turns": [{"role": "user", "content": "摸鱼"}],
                "ideal_reply": "这条不能被召回",
                "status": "draft",
                "enabled": False,
                "quality_score": 1.0,
            }
        )
        wrong_agent = await repository.apply_reply_example_action(
            {
                "action": "create",
                "title": "其他 Agent 样例",
                "scope_type": global_scope["scope_type"],
                "scope_hash": global_scope["scope_hash"],
                "agent_id": "agent-b",
                "topic": "摸鱼",
                "keywords": ["摸鱼"],
                "turns": [{"role": "user", "content": "摸鱼"}],
                "ideal_reply": "这条属于其他 Agent",
                "status": "approved",
                "enabled": True,
                "quality_score": 1.0,
            }
        )

        search = await repository.search_reply_examples(
            [global_scope],
            "摸鱼",
            limit=10,
            min_quality=0.7,
            agent_id="agent-a",
        )
        recalled = await service.recall_examples(context, agent_id=context.agent_id)
        detail = await repository.get_reply_example_detail(int(approved["id"]))
        public_listing = await service.list_reply_examples(page=1, page_size=20)

        assert [int(item["id"]) for item in search] == [int(approved["id"])]
        assert int(draft["id"]) not in {int(item["id"]) for item in search}
        assert int(wrong_agent["id"]) not in {int(item["id"]) for item in search}
        assert recalled.included is True
        assert recalled.source_refs == (f"example:{approved['id']}",)
        assert recalled.content.startswith("<Examples>")
        assert "不要照抄" in recalled.content
        assert "<Example" in recalled.content
        assert "<IdealReply>" in recalled.content
        assert recalled.content != approved["ideal_reply"]
        assert "这条不能被召回" not in recalled.content
        assert "这条属于其他 Agent" not in recalled.content

        with sqlite3.connect(tmp_path / "humanize.db") as conn:
            conn.row_factory = sqlite3.Row
            usage = conn.execute(
                "SELECT * FROM humanize_reply_example_usage "
                "WHERE request_id = ? ORDER BY rank, id",
                (context.request_id,),
            ).fetchall()
        assert usage
        assert int(usage[0]["example_id"]) == int(approved["id"])
        assert float(usage[0]["score"]) > 0
        assert int(usage[0]["selected"]) == 1

        assert detail is not None
        assert detail["turns"][0] == {
            "role": "user",
            "content": "今天怎么摸鱼",
        }
        assert detail["revisions"][0]["action"] == "create"
        public = next(
            item
            for item in public_listing["items"]
            if int(item["id"]) == int(approved["id"])
        )
        assert "scope_hash" not in public
        assert "subject_hash" not in public
        assert public["scope_token"]

        disabled = await repository.apply_reply_example_action(
            {
                "id": approved["id"],
                "revision": approved["revision"],
                "action": "disable",
            }
        )
        assert disabled["enabled"] is False
        assert (
            await repository.search_reply_examples(
                [global_scope],
                "摸鱼",
                limit=10,
                min_quality=0.7,
                agent_id="agent-a",
            )
            == []
        )

    asyncio.run(scenario())


def test_protocol_success_and_memory_job_are_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = PluginConfig()
        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        memory = _memory_service(repository, config)
        envelope = EnvelopeBuilder(config)
        matcher = JargonMatcher()
        service = HumanizeService(
            config=config,
            repository=repository,
            envelope=envelope,
            parser=ProtocolParser(config),
            matcher=matcher,
            composer=ContextComposer(
                config=config,
                repository=repository,
                envelope=envelope,
                matcher=matcher,
                memory=memory,
            ),
            memory=memory,
        )
        context = _context(request_id="dispatch-once", user_text="记住我喜欢茶")

        for _ in range(2):
            await service.record_protocol_success(
                context,
                action=Action.REPLY.value,
                raw_output=(
                    "<Action>Reply</Action>\n"
                    "<UnknownTerms>[]</UnknownTerms>\n"
                    "<Reply><Message>收到</Message><Message>会记住</Message></Reply>"
                ),
                messages=("收到", "会记住"),
                response_snapshot={"response": "complete"},
                response_snapshot_complete=True,
                model="test-model",
                provider_id="chat-provider-a",
                duration_ms=12,
                stage="final",
            )

        await service.record_protocol_failure(
            _context(request_id="dispatch-failed"),
            error_code="response_dispatch_failed",
            error_detail="sender raised",
            raw_output="validated but not sent",
            messages=(),
            model="test-model",
            duration_ms=15,
            stage="final",
        )

        with sqlite3.connect(tmp_path / "humanize.db") as conn:
            conn.row_factory = sqlite3.Row
            successes = conn.execute(
                "SELECT * FROM protocol_logs "
                "WHERE request_id = 'dispatch-once' AND stage = 'final' AND success = 1"
            ).fetchall()
            failures = conn.execute(
                "SELECT * FROM protocol_logs "
                "WHERE request_id = 'dispatch-failed' AND stage = 'final' AND success = 0"
            ).fetchall()
            jobs = conn.execute(
                "SELECT * FROM humanize_memory_jobs ORDER BY id"
            ).fetchall()

        assert len(successes) == 1
        assert json.loads(successes[0]["messages_json"]) == ["收到", "会记住"]
        assert len(failures) == 1
        assert len(jobs) == 1
        payload = json.loads(jobs[0]["payload_json"])
        assert jobs[0]["job_key"].startswith("extract_turn:")
        assert jobs[0]["job_key"] != "extract_turn:dispatch-once"
        assert len(jobs[0]["job_key"].removeprefix("extract_turn:")) == 64
        assert jobs[0]["job_type"] == "extract_turn"
        assert payload["action"] == "Reply"
        assert payload["assistant_messages"] == ["收到", "会记住"]
        assert payload["chat_provider_id"] == "chat-provider-a"
        assert payload["request_id"] == "dispatch-once"
        serialized = json.dumps(payload, ensure_ascii=False)
        assert context.scope_id not in serialized
        assert context.sender_id not in serialized
        assert context.conversation_id not in serialized
        assert context.message_id not in serialized

    asyncio.run(scenario())


def test_turn_job_idempotency_uses_scoped_message_hmac() -> None:
    async def scenario() -> None:
        service = _memory_service(object())
        first = _context(
            request_id="request-a",
            message_id="platform-message-42",
        )
        retried = _context(
            request_id="request-b",
            message_id="platform-message-42",
        )
        other_scope = _context(
            request_id="request-c",
            scope_id="other-platform:private-chat-1",
            message_id="platform-message-42",
        )
        other_agent = _context(
            request_id="request-d",
            message_id="platform-message-42",
            agent_id="agent-two",
        )

        first_job = await service.build_turn_job(
            first, action="Reply", messages=("收到",)
        )
        retried_job = await service.build_turn_job(
            retried, action="Reply", messages=("收到",)
        )
        other_scope_job = await service.build_turn_job(
            other_scope, action="Reply", messages=("收到",)
        )
        other_agent_job = await service.build_turn_job(
            other_agent, action="Reply", messages=("收到",)
        )

        assert first_job is not None
        assert retried_job is not None
        assert other_scope_job is not None
        assert other_agent_job is not None
        assert first_job["idempotency_key"] == retried_job["idempotency_key"]
        assert first_job["idempotency_key"] != other_scope_job["idempotency_key"]
        assert first_job["idempotency_key"] != other_agent_job["idempotency_key"]
        assert other_agent_job["agent_id"] == "agent-two"
        assert len(first_job["idempotency_key"]) == 64
        assert "platform-message-42" not in json.dumps(first_job)

    asyncio.run(scenario())


def test_manual_mutations_embed_fail_open_when_provider_fails() -> None:
    class MutationRepository:
        async def apply_reply_example_action(self, payload, actor="web_admin"):
            del payload, actor
            return {"id": 22, "status": "approved", "enabled": True}

    async def scenario() -> None:
        config = PluginConfig.from_mapping(
            {"memory_embedding_provider_id": "embedding-provider"}
        )
        service = _memory_service(MutationRepository(), config)
        attempts: list[tuple[str, int]] = []

        async def failing_embed(entity_type: str, entity_id: int) -> None:
            attempts.append((entity_type, entity_id))
            raise RuntimeError("provider unavailable")

        service._embed_entity = failing_embed  # type: ignore[method-assign]
        example = await service.apply_reply_example_action({"action": "approve"})

        assert example["id"] == 22
        assert attempts == [("example", 22)]

    asyncio.run(scenario())


def test_job_worker_renews_lease_and_releases_immediately_on_reload() -> None:
    class LeaseRepository:
        def __init__(self) -> None:
            self.claimed = False
            self.started = asyncio.Event()
            self.renewed = asyncio.Event()
            self.released: list[tuple[int, str, str]] = []
            self.renewals = 0

        async def claim_memory_job(self, lease_owner: str, lease_seconds: int):
            del lease_owner, lease_seconds
            if self.claimed:
                await asyncio.sleep(60)
                return None
            self.claimed = True
            return {"id": 7, "job_type": "extract_turn", "payload": {}}

        async def renew_memory_job(
            self, job_id: int, lease_owner: str, lease_seconds: int = 90
        ) -> bool:
            del job_id, lease_owner, lease_seconds
            self.renewals += 1
            self.renewed.set()
            return True

        async def release_memory_job(
            self, job_id: int, lease_owner: str, reason: str = "worker_cancelled"
        ) -> bool:
            self.released.append((job_id, lease_owner, reason))
            return True

        async def complete_memory_job(
            self, job_id: int, lease_owner: str, result: dict | None = None
        ):
            del result
            raise AssertionError((job_id, lease_owner))

        async def retry_memory_job(self, *args, **kwargs):
            raise AssertionError((args, kwargs))

    async def scenario() -> None:
        repository = LeaseRepository()
        service = _memory_service(repository)
        service._lease_seconds = 1
        service._lease_renew_interval_seconds = 0.01

        async def long_process(row: dict[str, object]) -> None:
            assert row["id"] == 7
            repository.started.set()
            await asyncio.Event().wait()

        service._process_job = long_process  # type: ignore[method-assign]
        service.start_worker()
        await asyncio.wait_for(repository.started.wait(), timeout=1)
        await asyncio.wait_for(repository.renewed.wait(), timeout=1)
        await service.stop()

        assert repository.released == [(7, service._lease_owner, "worker_cancelled")]

    asyncio.run(scenario())


def test_lost_job_lease_cancels_processing_without_completion() -> None:
    class LostLeaseRepository:
        def __init__(self) -> None:
            self.renewals = 0

        async def renew_memory_job(
            self, job_id: int, lease_owner: str, lease_seconds: int = 90
        ) -> bool:
            del job_id, lease_owner, lease_seconds
            self.renewals += 1
            return False

    async def scenario() -> None:
        repository = LostLeaseRepository()
        service = _memory_service(repository)
        service._lease_renew_interval_seconds = 0.01
        cancelled = asyncio.Event()

        async def long_process(row: dict[str, object]) -> None:
            assert row["id"] == 9
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        service._process_job = long_process  # type: ignore[method-assign]
        retained, results = await service._process_job_with_lease({"id": 9})

        assert retained is False
        assert results == [None]
        assert repository.renewals == 1
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_vector_example_recall_keeps_current_agent_id() -> None:
    class EmbeddingProvider:
        async def get_embedding(self, text: str) -> list[float]:
            assert text == "怎么休息"
            return [1.0, 0.0]

        def meta(self):
            return SimpleNamespace(
                id="embedding-provider",
                model="embedding-model",
                provider_type=SimpleNamespace(value="embedding"),
            )

    class ProviderContext:
        def get_provider_by_id(self, provider_id: str):
            assert provider_id == "embedding-provider"
            return EmbeddingProvider()

    class RecallRepository:
        def __init__(self) -> None:
            self.agent_ids: list[str] = []

        async def search_reply_examples(self, **filters):
            self.agent_ids.append(filters["agent_id"])
            return [
                {
                    "id": 5,
                    "score": 0.9,
                    "turns": [{"role": "user", "content": "怎么休息"}],
                    "ideal_reply": "先停一下。",
                }
            ]

        async def list_recallable_reply_examples(self, **filters):
            self.agent_ids.append(filters["agent_id"])
            return [
                {
                    "id": 5,
                    "score": 0.9,
                    "turns": [{"role": "user", "content": "怎么休息"}],
                    "ideal_reply": "先停一下。",
                }
            ]

        async def list_embeddings(self, **filters):
            del filters
            return []

        async def get_prompt_templates(self):
            return {"templates": {}}

        async def record_reply_example_usage(self, **payload):
            assert payload["agent_id"] == "agent-special"

    async def scenario() -> None:
        repository = RecallRepository()
        config = PluginConfig.from_mapping(
            {"memory_embedding_provider_id": "embedding-provider"}
        )
        service = ChatMemoryService(config, repository, ProviderContext())  # type: ignore[arg-type]
        service._secret = _TEST_SECRET
        service._state = "ready"
        result = await service.recall_examples(
            _context(user_text="怎么休息", agent_id="agent-special"),
            agent_id="agent-special",
        )

        assert result.included is True
        assert repository.agent_ids == ["agent-special", "agent-special"]

    asyncio.run(scenario())


def test_vector_and_rerank_candidates_are_bounded_before_paid_calls() -> None:
    class EmbeddingProvider:
        async def get_embedding(self, text: str) -> list[float]:
            assert text == "查询"
            return [1.0, 0.0]

        def get_dim(self) -> int:
            return 2

        def meta(self):
            return SimpleNamespace(
                id="embedding-provider",
                model="embedding-model",
                provider_type=SimpleNamespace(value="embedding"),
            )

    class RerankProvider:
        def __init__(self) -> None:
            self.document_count = 0
            self.top_n = 0

        async def rerank(self, query: str, documents: list[str], top_n: int):
            assert query == "查询"
            self.document_count = len(documents)
            self.top_n = top_n
            return [
                SimpleNamespace(index=index, relevance_score=1.0 - index / 100.0)
                for index in range(len(documents))
            ]

    class ProviderContext:
        def __init__(self) -> None:
            self.embedding = EmbeddingProvider()
            self.rerank = RerankProvider()

        def get_provider_by_id(self, provider_id: str):
            if provider_id == "embedding-provider":
                return self.embedding
            if provider_id == "rerank-provider":
                return self.rerank
            raise AssertionError(provider_id)

    class RecallRepository:
        def __init__(self) -> None:
            self.requested_limit = 0
            self.embedding_ids: list[int] = []

        async def list_recallable_reply_examples(self, **filters):
            self.requested_limit = int(filters["limit"])
            return [
                {"id": index, "score": 0.0, "ideal_reply": f"样例 {index}"}
                for index in range(1, 501)
            ]

        async def list_embeddings(self, **filters):
            self.embedding_ids = [int(value) for value in filters["entity_ids"]]
            return [
                {"entity_id": entity_id, "vector": [1.0, 0.0]}
                for entity_id in self.embedding_ids
            ]

    async def scenario() -> None:
        repository = RecallRepository()
        providers = ProviderContext()
        service = ChatMemoryService(
            PluginConfig.from_mapping(
                {
                    "memory_embedding_provider_id": "embedding-provider",
                    "memory_rerank_provider_id": "rerank-provider",
                }
            ),
            repository,  # type: ignore[arg-type]
            providers,
        )
        service._secret = _TEST_SECRET
        service._state = "ready"
        candidates = [
            {"id": index, "score": index / 1_000.0, "content": f"候选 {index}"}
            for index in range(501, 1_001)
        ]

        merged = await service._merge_vector_scores(
            entity_type="example",
            query="查询",
            scope_filters=[{"scope_type": "global", "scope_hash": "scope"}],
            candidates=candidates,
            agent_id="agent-one",
            candidate_limit=20,
            request_id="bounded-request",
        )
        reranked = await service._rerank(
            "查询", candidates, text_key="content", candidate_limit=20
        )

        assert len(merged) <= 20
        assert repository.requested_limit == 20
        assert len(repository.embedding_ids) <= 20
        assert len(reranked) == 20
        assert providers.rerank.document_count == 20
        assert providers.rerank.top_n == 20
        await service.stop()

    asyncio.run(scenario())


def test_zero_score_reply_example_is_not_injected_even_with_zero_threshold() -> None:
    class RecallRepository:
        async def search_reply_examples(self, **filters):
            del filters
            return [
                {
                    "id": 1,
                    "score": 0.0,
                    "turns": [{"role": "user", "content": "无关问题"}],
                    "ideal_reply": "无关回复",
                }
            ]

        async def get_prompt_templates(self):
            return {"templates": {}}

        async def record_reply_example_usage(self, **payload):
            del payload

    async def scenario() -> None:
        service = _memory_service(
            RecallRepository(),
            PluginConfig.from_mapping({"reply_examples_recall_score_threshold": 0.0}),
        )
        result = await service.recall_examples(_context(user_text="当前问题"))
        debug = await service.debug_recall(
            query="当前问题",
            scope_token="",
            kind="example",
            agent_id="default",
        )

        assert result.included is False
        assert result.item_count == 0
        assert "无关回复" not in result.content
        assert debug["included"] is False
        assert debug["items"] == []

    asyncio.run(scenario())


def test_llm_extraction_uses_its_bounded_background_budget(monkeypatch) -> None:
    class Extractor:
        timeout = 120

        async def text_chat(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                role="assistant",
                tools_call_name=None,
                tools_call_args=None,
                completion_text="[]",
            )

    class ProviderContext:
        def get_provider_by_id(self, provider_id: str):
            assert provider_id == "extractor"
            return Extractor()

    class Repository:
        async def get_prompt_templates(self):
            return {"templates": {}}

    async def scenario() -> None:
        observed_timeouts: list[float] = []
        original_wait_for = asyncio.wait_for

        async def capture_timeout(awaitable, timeout):
            observed_timeouts.append(float(timeout))
            return await original_wait_for(awaitable, timeout)

        monkeypatch.setattr(memory_module.asyncio, "wait_for", capture_timeout)
        service = ChatMemoryService(
            PluginConfig.from_mapping(
                {
                    "memory_extraction_provider_id": "extractor",
                    "memory_recall_timeout_seconds": 1.5,
                }
            ),
            Repository(),  # type: ignore[arg-type]
            ProviderContext(),
        )

        assert await service._llm_candidates_batch([{"user_text": "测试"}]) == []
        assert observed_timeouts == [15.0]

    asyncio.run(scenario())


def test_identity_dependent_management_fails_closed_without_secret() -> None:
    class ManagementRepository:
        def __init__(self) -> None:
            self.list_called = False

        async def get_memory_overview(self):
            return {
                "scope_options": [
                    {
                        "scope_type": "global",
                        "scope_hash": "must-not-leak",
                        "subject_hash": "",
                    }
                ]
            }

        async def list_memories(self, **filters):
            del filters
            self.list_called = True
            return {"items": [], "total": 0}

    async def scenario() -> None:
        repository = ManagementRepository()
        service = ChatMemoryService(PluginConfig(), repository)  # type: ignore[arg-type]
        service._state = "error"
        service._reason = "identity_initialization_failed"

        status = await service.get_status()

        assert status["overview"] == {}
        with pytest.raises(RuntimeError, match="identity"):
            service.encode_scope_token(scope_type="global", scope_hash="scope")
        with pytest.raises(RuntimeError, match="identity"):
            await service.list_memories()
        assert repository.list_called is False

    asyncio.run(scenario())


def test_management_lists_only_filter_agent_when_explicitly_requested() -> None:
    class ManagementRepository:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def list_memory_jobs(self, **filters):
            self.calls.append(("job", filters))
            return {"items": [], "total": 0}

        async def list_reply_examples(self, **filters):
            self.calls.append(("example", filters))
            return {"items": [], "total": 0}

    async def scenario() -> None:
        repository = ManagementRepository()
        service = _memory_service(repository)

        await service.list_memory_jobs(page=1)
        await service.list_reply_examples(page=1, agent_id="  ")
        await service.list_reply_examples(page=1, agent_id=" agent-a ")

        assert repository.calls[:2] == [
            ("job", {"page": 1}),
            ("example", {"page": 1}),
        ]
        assert repository.calls[2] == (
            "example",
            {"page": 1, "agent_id": "agent-a"},
        )

    asyncio.run(scenario())


def test_intent_analysis_feeds_typed_queries_into_recall(monkeypatch) -> None:
    """Enabled intent analysis produces typed queries for the recall adapter."""

    class IntentExtractor:
        async def text_chat(self, **kwargs):
            del kwargs
            return SimpleNamespace(
                role="assistant",
                tools_call_name=None,
                tools_call_args=None,
                completion_text=(
                    '{"queries":[{"query":"喜欢吃什么","context_type":"preference",'
                    '"intent":"偏好","priority":4}]}'
                ),
            )

    class ProviderContext:
        def get_provider_by_id(self, provider_id: str):
            assert provider_id == "extractor"
            return IntentExtractor()

    class FakeRecall:
        def __init__(self) -> None:
            self.queries: tuple[str, ...] = ()

        async def recall(self, **kwargs):
            self.queries = kwargs.get("queries", ())
            return SimpleNamespace(
                included=False,
                content="",
                source_refs=(),
                item_count=0,
                reason="no_match",
                duration_ms=1,
            )

    async def scenario() -> None:
        fake = FakeRecall()
        service = ChatMemoryService(
            PluginConfig.from_mapping(
                {
                    "memory_intent_analysis_enabled": True,
                    "memory_extraction_provider_id": "extractor",
                }
            ),
            object(),  # type: ignore[arg-type]
            ProviderContext(),
        )
        service._secret = _TEST_SECRET
        service._state = "ready"
        service._reason = "test_identity_secret"
        service._openviking_ready = True
        service._openviking_recall = fake  # type: ignore[assignment]

        result = await service.recall_memories(_context(request_id="intent-1"))
        assert result.reason == "no_match"
        assert fake.queries == ("喜欢吃什么",)

    asyncio.run(scenario())


def test_intent_analysis_disabled_passes_no_typed_queries() -> None:
    class FakeRecall:
        def __init__(self) -> None:
            self.queries: tuple[str, ...] = ()

        async def recall(self, **kwargs):
            self.queries = kwargs.get("queries", ())
            return SimpleNamespace(
                included=False,
                content="",
                source_refs=(),
                item_count=0,
                reason="no_match",
                duration_ms=1,
            )

    async def scenario() -> None:
        fake = FakeRecall()
        service = ChatMemoryService(PluginConfig(), object())  # type: ignore[arg-type]
        service._secret = _TEST_SECRET
        service._state = "ready"
        service._reason = "test_identity_secret"
        service._openviking_ready = True
        service._openviking_recall = fake  # type: ignore[assignment]

        await service.recall_memories(_context(request_id="intent-2"))
        assert fake.queries == ()

    asyncio.run(scenario())


def test_parse_tool_time_bounds() -> None:
    """Model-facing time bounds: empty, date-only, naive local, invalid."""

    _parse = memory_module._parse_tool_time
    assert _parse("", end_of_day=True) is None

    # date-only until covers the whole day: local 23:59:59+08:00 == 15:59:59Z.
    until = _parse("2026-07-31", end_of_day=True)
    assert until == datetime(2026, 7, 31, 15, 59, 59, tzinfo=UTC)

    # naive date-time is interpreted as UTC+8: 14:00+08:00 == 06:00Z.
    naive = _parse("2026-07-31 14:00", end_of_day=False)
    assert naive == datetime(2026, 7, 31, 6, 0, tzinfo=UTC)

    # date-only since starts at midnight local and normalizes to UTC.
    since = _parse("2026-07-31", end_of_day=False)
    assert since == datetime(2026, 7, 30, 16, 0, tzinfo=UTC)

    with pytest.raises(ValueError):
        _parse("昨天", end_of_day=False)


def test_search_memory_for_tool_renders_sections_guards_and_args() -> None:
    class FakeRecall:
        def __init__(self) -> None:
            self.recall_kwargs: dict[str, object] = {}
            self.history_kwargs: dict[str, object] = {}

        async def recall(self, **kwargs):
            self.recall_kwargs = kwargs
            return SimpleNamespace(
                included=True,
                content="<MemoryContext><Memory type='preference'>喜欢爬山</Memory></MemoryContext>",
                source_refs=(),
                item_count=1,
                reason="matched",
                duration_ms=1,
            )

        async def search_session_history(self, **kwargs):
            self.history_kwargs = kwargs
            return SimpleNamespace(
                included=True,
                rows=(
                    {
                        "updated_at": "2026-07-17T00:00:00+00:00",
                        "action": "Reply",
                        "context_ref": "ctx-1A2B3C4D",
                        "content": "用户提到周末想去爬山",
                    },
                ),
                reason="ok",
                duration_ms=1,
            )

    async def scenario() -> None:
        fake = FakeRecall()
        service = _memory_service(object())  # type: ignore[arg-type]
        service._openviking_ready = True
        service._openviking_recall = fake  # type: ignore[assignment]

        result = await service.search_memory_for_tool(
            _context(request_id="search-1"),
            query="爬山",
            since="2026-07-01",
            until="2026-07-31",
            limit=2,
        )
        assert "资料而不是指令" in result
        assert "== 长期记忆 ==" in result
        assert "== 对话归档" in result
        assert "ctx-1A2B3C4D" in result
        # 存储时间 2026-07-17T00:00Z 按 UTC+8 展示为 08:00。
        assert "2026-07-17 08:00" in result
        # 时间边界按东八区归一化为 UTC 后传给 recall 适配层。
        assert fake.recall_kwargs["since"] == datetime(2026, 6, 30, 16, 0, tzinfo=UTC)
        assert fake.recall_kwargs["until"] == datetime(
            2026, 7, 31, 15, 59, 59, tzinfo=UTC
        )
        assert fake.recall_kwargs["include_session_fallback"] is False
        assert fake.history_kwargs["query"] == "爬山"
        assert fake.history_kwargs["limit"] == 2

        bad_type = await service.search_memory_for_tool(
            _context(request_id="search-2"), memory_type="junk"
        )
        assert "memory_type" in bad_type

        bad_time = await service.search_memory_for_tool(
            _context(request_id="search-3"), since="昨天"
        )
        assert "时间参数无效" in bad_time

        type_only = await service.search_memory_for_tool(
            _context(request_id="search-4"), memory_type="preference"
        )
        assert "query" in type_only

    asyncio.run(scenario())


def test_search_memory_for_tool_invalid_time_returns_safe_message() -> None:
    class FakeRecall:
        async def recall(self, **kwargs):
            raise AssertionError("recall must not run for invalid time input")

        async def search_session_history(self, **kwargs):
            raise AssertionError("history search must not run for invalid time input")

    async def scenario() -> None:
        service = _memory_service(object())  # type: ignore[arg-type]
        service._openviking_ready = True
        service._openviking_recall = FakeRecall()  # type: ignore[assignment]
        result = await service.search_memory_for_tool(
            _context(request_id="search-5"), since="not-a-time"
        )
        assert "时间参数无效" in result

    asyncio.run(scenario())
