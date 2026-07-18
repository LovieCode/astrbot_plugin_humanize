from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_humanize.main import HumanizePlugin
from humanize.config import PluginConfig
from humanize.context.window import ContextWindowService
from humanize.domain.models import MessageContext, PreparedRequest
from humanize.memory import ChatMemoryService
from humanize.openviking import (
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)
from humanize.protocol.parser import ProtocolParser
from humanize.repositories.sqlite import SQLiteRepository

from astrbot.api.provider import ProviderRequest


def _context(
    index: int,
    *,
    scope_id: str = "group-1",
    conversation_id: str = "conversation-1",
    agent_id: str = "default",
    user_text: str | None = None,
) -> MessageContext:
    return MessageContext(
        request_id=f"request-{index}",
        scope_type="group",
        scope_id=scope_id,
        message_id=f"message-{index}",
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text or f"message {index}",
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
        conversation_id=conversation_id,
        occurred_at=f"2026-07-19T00:{index % 60:02d}:00+00:00",
        agent_id=agent_id,
    )


async def _window(
    tmp_path: Path,
    *,
    secret: bytes = b"x" * 32,
) -> tuple[ContextWindowService, ChatMemoryService, OpenVikingWorkspace]:
    repository = SQLiteRepository(tmp_path / "humanize.db")
    await repository.initialize()
    memory = ChatMemoryService(PluginConfig(), repository)
    memory._secret = secret
    memory._state = "ready"
    workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
    window = ContextWindowService(workspace, memory)
    window.initialize()
    return window, memory, workspace


def _messages(context: MessageContext, reply: str) -> list[dict[str, object]]:
    return [
        {"role": "user", "content": context.user_text},
        {"role": "assistant", "content": reply},
    ]


def test_window_compacts_40_entries_to_20_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        refs: list[str] = []
        last_context = _context(40)
        for index in range(1, 41):
            context = _context(index)
            result = await window.append(
                context,
                action="Reply",
                run_messages=_messages(context, f"reply {index}"),
                final_messages=(f"reply {index}",),
                token_budget=30_000,
            )
            refs.append(result.context_ref)

        loaded = await window.load(last_context, token_budget=30_000)
        assert loaded.entry_count == 20
        assert loaded.compacted is False
        assert any(
            item["role"] == "system"
            and "HumanizeContextSummary" in str(item["content"])
            for item in loaded.contexts
        )
        assert sum(item["role"] == "user" for item in loaded.contexts) == 20
        assert all(len(ref) == len("ctx-7F3K9M2Q") for ref in refs)

        retried = await window.append(
            _context(1),
            action="Reply",
            run_messages=_messages(_context(1), "reply 1"),
            final_messages=("reply 1",),
            token_budget=30_000,
        )
        assert retried.duplicate is True
        assert retried.context_ref == refs[0]

        restarted, _, _ = await _window(tmp_path)
        restored = await restarted.load(last_context, token_budget=30_000)
        assert restored.entry_count == 20
        assert restored.contexts == loaded.contexts

    asyncio.run(scenario())


def test_window_compacts_long_history_by_token_budget_and_keeps_latest_turns(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        last_context = _context(12, user_text="last message")
        for index in range(1, 13):
            context = last_context if index == 12 else _context(index)
            text = "长文本" * 1_500
            await window.append(
                context,
                action="Reply",
                run_messages=_messages(context, text),
                final_messages=(text,),
                token_budget=30_000,
            )

        loaded = await window.load(last_context, token_budget=500)
        assert loaded.compacted is True
        assert loaded.entry_count == 10
        assert any(
            item["role"] == "system" and "Earlier turn" in str(item["content"])
            for item in loaded.contexts
        )
        assert any(
            item["role"] == "user" and item["content"] == "last message"
            for item in loaded.contexts
        )

    asyncio.run(scenario())


def test_context_refs_are_opaque_scoped_and_retry_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        values = iter((b"aaaaa", b"aaaaa", b"bbbbb"))
        monkeypatch.setattr(
            "humanize.context.window.secrets.token_bytes", lambda size: next(values)
        )
        window, _, _ = await _window(tmp_path)
        first_context = _context(1)
        second_context = _context(2)
        first = await window.append(
            first_context,
            action="Reply",
            run_messages=_messages(first_context, "first reply"),
            final_messages=("first reply",),
        )
        second = await window.append(
            second_context,
            action="Reply",
            run_messages=_messages(second_context, "second reply"),
            final_messages=("second reply",),
        )
        assert first.context_ref != second.context_ref
        assert first.context_ref.startswith("ctx-")
        assert len(first.context_ref) == len("ctx-7F3K9M2Q")

        detail = await window.read_context(first_context, first.context_ref)
        assert "first reply" in detail
        assert "viking://" not in detail
        assert (
            await window.read_context(
                _context(1, scope_id="group-2"), first.context_ref
            )
            == ""
        )
        assert (
            await window.read_context(
                _context(1, agent_id="other-agent"), first.context_ref
            )
            == ""
        )
        with pytest.raises(ValueError):
            await window.read_context(first_context, "ctx-this-is-too-long")

    asyncio.run(scenario())


def test_tool_chains_and_images_are_safe_in_hot_and_cold_context(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        first_context = _context(1, user_text="请查询图片")
        first = await window.append(
            first_context,
            action="Reply",
            run_messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请查询图片"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,SECRET"},
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "tool-1",
                    "content": "result " + "x" * 3_000,
                },
                {"role": "assistant", "content": "raw final"},
            ],
            final_messages=("validated final",),
            image_cache=(
                {
                    "index": 1,
                    "description": "一只橘猫坐在窗边",
                    "ocr": "hello",
                    "objects": ["cat", "window"],
                },
            ),
            image_count=1,
            token_budget=30_000,
        )
        for index in range(2, 12):
            context = _context(index)
            await window.append(
                context,
                action="Reply",
                run_messages=_messages(context, f"reply {index}"),
                final_messages=(f"reply {index}",),
                token_budget=30_000,
            )

        loaded = await window.load(_context(11), token_budget=30_000)
        tool_index = next(
            index
            for index, item in enumerate(loaded.contexts)
            if item["role"] == "assistant" and item.get("tool_calls")
        )
        assert loaded.contexts[tool_index + 1]["role"] == "tool"
        assert "folded" in str(loaded.contexts[tool_index + 1]["content"])
        detail = await window.read_context(first_context, first.context_ref)
        assert "data:image" not in detail
        assert "一只橘猫坐在窗边" in detail
        assert "validated final" in detail

    asyncio.run(scenario())


def test_canonical_context_turn_is_reused_by_openviking_memory_and_session_fallback(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        window, memory, workspace = await _window(tmp_path)
        context = _context(1, user_text="我喜欢无糖乌龙茶")
        persisted = await window.append(
            context,
            action="Reply",
            run_messages=_messages(context, "记住了"),
            final_messages=("记住了",),
        )
        adapter = OpenVikingMemoryAdapter(workspace)
        adapter.initialize()
        memory._openviking = adapter
        memory._openviking_ready = True
        committed = await memory.commit_context_turn(
            context,
            action="Reply",
            messages=("记住了",),
            context_ref=persisted.context_ref,
        )
        assert committed is True
        job = await memory.build_turn_job(
            context,
            action="Reply",
            messages=("记住了",),
            context_ref=persisted.context_ref,
        )
        assert job is not None
        commit = adapter.commit_turn(job)
        assert commit.duplicate is True
        assert commit.message_count == 2

        identity = memory.identity_for(context)
        session_root = (
            workspace.root
            / "sessions"
            / "default"
            / identity.primary_scope_type
            / identity.primary_scope_hash
            / identity.conversation_hash
        )
        commit_payload = json.loads(
            (session_root / "commits" / f"{job['idempotency_key']}.json").read_text(
                encoding="utf-8"
            )
        )
        assert commit_payload["l1"] == ""
        assert commit_payload["context_ref"] == persisted.context_ref
        assert not (session_root / "messages.jsonl").exists()

        recall = OpenVikingRecallAdapter(workspace)
        disabled = await recall.recall(
            query="乌龙茶",
            agent_id=context.agent_id,
            scope_filters=identity.scopes,
            conversation_hash=identity.conversation_hash,
            limit=5,
            threshold=0.2,
            max_chars=1_000,
            include_session_fallback=False,
        )
        enabled = await recall.recall(
            query="乌龙茶",
            agent_id=context.agent_id,
            scope_filters=identity.scopes,
            conversation_hash=identity.conversation_hash,
            limit=5,
            threshold=0.2,
            max_chars=1_000,
        )
        assert disabled.included is False
        assert enabled.included is True

    asyncio.run(scenario())


def test_image_cache_protocol_is_hidden_from_visible_reply() -> None:
    parser = ProtocolParser(PluginConfig())

    decision = parser.parse(
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n"
        '<ImageCache>[{"index":1,"description":"橘猫","ocr":"","objects":["cat"]}]</ImageCache>\n'
        "你好"
    )

    assert decision.messages == ("你好",)
    assert decision.image_cache[0].description == "橘猫"


def test_request_takeover_replaces_native_history_and_disables_session_fallback() -> (
    None
):
    class Event:
        def __init__(self) -> None:
            self.extras: dict[str, object] = {}
            self.unified_msg_origin = "group-1"
            self.message_str = "hello"
            self.stopped = False

        def set_extra(self, key: str, value: object) -> None:
            self.extras[key] = value

        def get_extra(self, key: str, default=None):
            return self.extras.get(key, default)

        def get_message_str(self) -> str:
            return self.message_str

        def get_platform_name(self) -> str:
            return "aiocqhttp"

        async def send(self, message) -> None:
            del message

        def clear_result(self) -> None:
            return None

        def stop_event(self) -> None:
            self.stopped = True

    class Window:
        async def load(self, context, *, token_budget: int):
            del context, token_budget
            return SimpleNamespace(
                contexts=({"role": "user", "content": "managed history"},),
            )

    class Service:
        def __init__(self) -> None:
            self.include_session_fallback: bool | None = None

        async def prepare_request(self, context, *, include_session_fallback: bool):
            del context
            self.include_session_fallback = include_session_fallback
            return PreparedRequest(
                protocol_prompt="protocol",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    class PersonaManager:
        @staticmethod
        async def resolve_selected_persona(**kwargs):
            del kwargs
            return "default", None, None, False

    class AppContext:
        persona_manager = PersonaManager()

        @staticmethod
        def get_config(**kwargs):
            del kwargs
            return {"provider_settings": {"max_context_length": 16_000}}

        @staticmethod
        def get_using_provider(umo):
            del umo
            return None

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(AppContext(), {})
        plugin._container = SimpleNamespace(service=service, context_window=Window())
        plugin._build_message_context = lambda event, text: _context(1, user_text=text)
        event = Event()
        request = ProviderRequest(
            prompt="hello",
            contexts=[{"role": "assistant", "content": "native history"}],
            conversation=SimpleNamespace(cid="conversation-1", persona_id="default"),
        )

        await plugin.on_llm_request(event, request)

        assert request.conversation is None
        assert request.contexts == [{"role": "user", "content": "managed history"}]
        assert service.include_session_fallback is False
        assert event.get_extra("_humanize_context_window_active") is True
        assert event.get_extra("_humanize_history_sync_required") is False

    asyncio.run(scenario())
