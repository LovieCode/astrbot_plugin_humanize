from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.context.window import ContextWindowService
from astrbot_plugin_humanize.humanize.domain.models import (
    ImageCache,
    MessageContext,
    PreparedRequest,
)
from astrbot_plugin_humanize.humanize.memory import ChatMemoryService
from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.main import HumanizePlugin

from astrbot.api.provider import ProviderRequest


def _context(
    index: int,
    *,
    scope_id: str = "group-1",
    conversation_id: str = "conversation-1",
    agent_id: str = "default",
    user_text: str | None = None,
    sender_id: str = "user-1",
    sender_name: str = "小明",
) -> MessageContext:
    return MessageContext(
        request_id=f"request-{index}",
        scope_type="group",
        scope_id=scope_id,
        message_id=f"message-{index}",
        sender_id=sender_id,
        sender_name=sender_name,
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


async def _async_build_context(event, text):
    return _context(text)


async def _async_build_context_1(event, text):
    return _context(1, user_text=text)


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
            item["role"] == "user"
            and item["content"].startswith("[小明 · ")
            and "last message" in item["content"]
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


def test_window_clear_removes_active_entries_and_refs(tmp_path: Path) -> None:
    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        context = _context(1)
        appended = await window.append(
            context,
            action="Reply",
            run_messages=_messages(context, "reply"),
            final_messages=("reply",),
        )

        assert await window.clear(context) == 1
        loaded = await window.load(context)
        assert loaded.entry_count == 0
        assert loaded.contexts == ()
        assert await window.read_context(context, appended.context_ref) == ""

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

        identity = memory.session_identity_for(context)
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
        "<ImageCache>结合上下文，这是用户发的橘猫表情包</ImageCache>\n"
        "<Messages><Message>你好</Message></Messages>"
    )

    assert decision.messages == ("你好",)
    assert decision.image_cache[0].text.startswith("结合上下文")


def test_clear_command_resets_native_and_managed_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, text: str) -> None:
            self.chain = [SimpleNamespace(text=text)]

    class Event:
        def __init__(self) -> None:
            self.unified_msg_origin = "group-1"
            self.message_obj = SimpleNamespace(
                timestamp=0,
                message_id="clear-1",
                message=(),
            )
            self.created_at = 0
            self.result: Result | None = None
            self.stopped = False

        def get_message_str(self) -> str:
            return "/clear"

        def get_sender_name(self) -> str:
            return "小明"

        def get_sender_id(self) -> str:
            return "user-1"

        def get_platform_name(self) -> str:
            return "aiocqhttp"

        def is_private_chat(self) -> bool:
            return False

        def plain_result(self, text: str) -> Result:
            return Result(text)

        def set_result(self, result: Result) -> None:
            self.result = result

        def get_result(self) -> Result | None:
            return self.result

        def stop_event(self) -> None:
            self.stopped = True

    class Window:
        cleared_context: MessageContext | None = None

        async def clear(self, context: MessageContext) -> int:
            self.cleared_context = context
            return 2

    class ConversationManager:
        @staticmethod
        async def get_curr_conversation_id(umo: str) -> str:
            assert umo == "group-1"
            return "conversation-1"

        @staticmethod
        async def get_conversation(umo: str, conversation_id: str):
            assert (umo, conversation_id) == ("group-1", "conversation-1")
            return SimpleNamespace(persona_id="default")

    class PersonaManager:
        @staticmethod
        async def resolve_selected_persona(**kwargs):
            assert kwargs["conversation_persona_id"] == "default"
            return "default", None, None, False

    class AppContext:
        conversation_manager = ConversationManager()
        persona_manager = PersonaManager()

        @staticmethod
        def get_config(**kwargs):
            assert kwargs["umo"] == "group-1"
            return {"provider_settings": {}}

    class NativeCommands:
        def __init__(self, context) -> None:
            assert isinstance(context, AppContext)

        async def reset(self, event: Event) -> None:
            event.set_result(event.plain_result("✅ Conversation reset successfully."))

    async def scenario() -> None:
        window = Window()
        plugin = HumanizePlugin(AppContext(), {})
        plugin._container = SimpleNamespace(context_window=window)
        event = Event()

        await plugin.clear_managed_context(event)

        assert event.stopped is True
        assert window.cleared_context is not None
        assert window.cleared_context.conversation_id == "conversation-1"
        assert window.cleared_context.agent_id == "default"
        assert event.result is not None
        assert event.result.chain[0].text.endswith("Humanize context cleared.")

    monkeypatch.setattr(
        "astrbot_plugin_humanize.main.ConversationCommands", NativeCommands
    )
    asyncio.run(scenario())


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
                entry_count=1,
                estimated_tokens=2,
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
        plugin._build_message_context = _async_build_context_1
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


def test_request_takeover_omits_native_history_when_window_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            raise RuntimeError("workspace unavailable")

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

    warnings: list[str] = []

    def capture_warning(message: str, *args, **kwargs) -> None:
        del kwargs
        warnings.append(message % args if args else message)

    monkeypatch.setattr(
        "astrbot_plugin_humanize.main.logger.warning",
        capture_warning,
    )

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(AppContext(), {})
        plugin._container = SimpleNamespace(service=service, context_window=Window())
        plugin._build_message_context = _async_build_context_1
        event = Event()
        request = ProviderRequest(
            prompt="hello",
            contexts=[{"role": "assistant", "content": "native history"}],
            conversation=SimpleNamespace(cid="conversation-1", persona_id="default"),
        )

        await plugin.on_llm_request(event, request)

        assert request.conversation is None
        assert request.contexts == []
        assert service.include_session_fallback is False
        assert event.get_extra("_humanize_context_window_active") is False
        assert event.get_extra("_humanize_history_sync_required") is False
        assert (
            "context window unavailable; cleared AstrBot native history" in warnings[0]
        )

    asyncio.run(scenario())


def test_image_cache_objects_and_text_are_marked_into_turn(
    tmp_path: Path,
) -> None:
    """ImageCache dataclass entries and plain text both become [图片 N: ...] markers."""

    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        context = _context(1, user_text="")  # 图片轮：无文本
        await window.append(
            context,
            action="Reply",
            run_messages=[
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "看到了"},
            ],
            final_messages=("看到了",),
            image_cache=(
                ImageCache(text="图片用夸张的近距离视角表现两只卡通猪"),
                "纯文本转述第二条",
            ),
            image_count=2,
            token_budget=30_000,
        )
        loaded = await window.load(_context(1), token_budget=30_000)
        joined = "\n".join(str(item.get("content", "")) for item in loaded.contexts)
        assert "图片用夸张的近距离视角表现两只卡通猪" in joined
        assert "纯文本转述第二条" in joined
        assert "[图片 1:" in joined
        assert "ImageCache(text=" not in joined

    asyncio.run(scenario())


def test_historical_turns_carry_sender_and_time_prefixes(tmp_path: Path) -> None:
    """Rendered history includes the sender and a compact time label per message."""

    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        context = _context(3, user_text="晚上吃什么")
        await window.append(
            context,
            action="Reply",
            run_messages=[
                {"role": "user", "content": "晚上吃什么"},
                {"role": "assistant", "content": "火锅吧"},
            ],
            final_messages=("火锅吧",),
            token_budget=30_000,
        )
        loaded = await window.load(_context(3), token_budget=30_000)
        user_msgs = [
            str(item["content"]) for item in loaded.contexts if item["role"] == "user"
        ]
        assert user_msgs, "expected at least one rendered user message"
        assert user_msgs[0].startswith("[小明 · ")
        assert "晚上吃什么" in user_msgs[0]
        # assistant 消息带 Bot 前缀
        assistant_msgs = [
            str(item["content"])
            for item in loaded.contexts
            if item["role"] == "assistant"
        ]
        assert any(msg.startswith("[Bot · ") for msg in assistant_msgs)

    asyncio.run(scenario())


def test_chatter_enters_group_window_shared_across_senders(tmp_path: Path) -> None:
    """Unaddressed chatter is an ordinary entry in the group's shared window."""

    async def scenario() -> None:
        window, memory, workspace = await _window(tmp_path)
        alice = _context(1, user_text="旁路一句")
        bob = _context(2, sender_id="user-2", sender_name="小红", user_text="旁路二句")
        other_conversation = _context(
            3, conversation_id="conversation-other", user_text="另一会话"
        )
        other_agent = _context(4, agent_id="other-agent", user_text="另一人格")
        other_group = _context(5, scope_id="group-2", user_text="另一群")

        assert await window.append_chatter(alice, has_image=True) is True
        assert await window.append_chatter(alice) is False  # 按消息编号去重
        assert await window.append_chatter(bob) is True
        assert await window.append_chatter(other_conversation) is True
        assert await window.append_chatter(other_agent) is True
        assert await window.append_chatter(other_group) is True

        # 同群不同发送者共享同一份历史：@ 机器人的回合能看到全部旁观记录。
        loaded = await window.load(_context(10, user_text="@bot"))
        assert loaded.entry_count == 2
        contents = [str(item.get("content")) for item in loaded.contexts]
        assert any("[小明" in content and "旁路一句" in content for content in contents)
        assert any("[小红" in content and "旁路二句" in content for content in contents)
        assert any("[图片]" in content for content in contents)

        # 其他会话/人格/群各自独立。
        isolated = await window.load(other_conversation)
        assert isolated.entry_count == 1

        # 状态文件落在群作用域目录下（跨成员共享）。
        identity = memory.identity_for(alice)
        group_scope = next(
            scope for scope in identity.scopes if scope["scope_type"] == "group"
        )
        state_path = (
            workspace.root
            / "sessions"
            / "default"
            / "group"
            / group_scope["scope_hash"]
            / identity.conversation_hash
            / "context_window.json"
        )
        assert state_path.is_file()
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["scope_type"] == "group"
        assert len(payload["entries"]) == 2

    asyncio.run(scenario())


def test_chatter_compacts_like_any_other_entry(tmp_path: Path) -> None:
    """Chatter counts against the token budget and folds into the summary."""

    async def scenario() -> None:
        window, _, _ = await _window(tmp_path)
        for index in range(1, 31):
            await window.append_chatter(
                _context(
                    index,
                    sender_id=f"user-{index}",
                    sender_name=f"成员{index}",
                    user_text=f"line-{index}-" + ("闲聊内容" * 8),
                ),
                token_budget=256,
            )
        loaded = await window.load(_context(99, user_text="@bot"), token_budget=256)
        assert loaded.entry_count <= 10
        summary = str(loaded.contexts[0].get("content"))
        assert "<HumanizeContextSummary>" in summary
        assert "闲聊内容" in summary

    asyncio.run(scenario())


def test_on_llm_request_loads_managed_history_without_trailing_fragments() -> None:
    """The managed window is authoritative; no ambient suffix is appended."""

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
                contexts=(
                    {"role": "system", "content": "persona prefix"},
                    {"role": "user", "content": "managed history"},
                ),
                entry_count=2,
                estimated_tokens=2,
            )

    class Service:
        async def prepare_request(self, context, *, include_session_fallback: bool):
            del context, include_session_fallback
            return PreparedRequest(
                protocol_prompt="protocol",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

        async def record_context_trace(self, *args, **kwargs) -> None:
            del args, kwargs

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
        window = Window()
        plugin = HumanizePlugin(AppContext(), {})
        plugin._container = SimpleNamespace(service=Service(), context_window=window)
        plugin._build_message_context = _async_build_context_1
        event = Event()
        request = ProviderRequest(
            prompt="hello",
            contexts=[{"role": "assistant", "content": "native history"}],
            conversation=SimpleNamespace(cid="conversation-1", persona_id="default"),
        )

        await plugin.on_llm_request(event, request)

        assert request.conversation is None
        assert request.contexts == [
            {"role": "system", "content": "persona prefix"},
            {"role": "user", "content": "managed history"},
        ]

    asyncio.run(scenario())


def test_prepare_records_unaddressed_group_messages_and_skips_at_turns() -> None:
    class RecordingWindow:
        def __init__(self) -> None:
            self.chatter: list[tuple[MessageContext, bool]] = []

        async def append_chatter(self, context, *, has_image: bool = False, **kwargs):
            del kwargs
            self.chatter.append((context, has_image))
            return True

    class GroupEvent:
        def __init__(
            self,
            *,
            private: bool,
            at: bool,
            text: str,
            message_id: str,
            sender: str = "user-2",
        ) -> None:
            self.extras: dict[str, object] = {}
            self.unified_msg_origin = "group-1"
            self.is_at_or_wake_command = at
            self.message_str = text
            self.message_obj = SimpleNamespace(
                message_id=message_id,
                timestamp=1_777_000_000,
                message=(),
            )
            self._private = private
            self._sender = sender

        def set_extra(self, key: str, value: object) -> None:
            self.extras[key] = value

        def get_extra(self, key: str, default=None):
            return self.extras.get(key, default)

        def is_private_chat(self) -> bool:
            return self._private

        def get_message_str(self) -> str:
            return self.message_str

        def get_sender_id(self) -> str:
            return self._sender

        def get_sender_name(self) -> str:
            return "小红"

        def get_self_id(self) -> str:
            return "bot-1"

        async def send(self, message) -> None:
            del message

    async def scenario() -> None:
        window = RecordingWindow()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(context_window=window)

        from tests.test_adapter import _FakeEvent

        await plugin.prepare_message_event(_FakeEvent())
        assert window.chatter == []

        await plugin.prepare_message_event(
            GroupEvent(private=True, at=False, text="私聊", message_id="p1")
        )
        assert window.chatter == []

        await plugin.prepare_message_event(
            GroupEvent(private=False, at=True, text="@bot 你好", message_id="a1")
        )
        assert window.chatter == []

        await plugin.prepare_message_event(
            GroupEvent(
                private=False,
                at=False,
                text="未点名的话",
                message_id="g1",
            )
        )
        assert len(window.chatter) == 1
        recorded, has_image = window.chatter[0]
        assert recorded.user_text == "未点名的话"
        assert recorded.scope_type == "group"
        assert recorded.sender_name == "小红"
        assert has_image is False

        await plugin.prepare_message_event(
            GroupEvent(
                private=False,
                at=False,
                text="机器人自己说",
                message_id="g2",
                sender="bot-1",
            )
        )
        assert len(window.chatter) == 1

    asyncio.run(scenario())
