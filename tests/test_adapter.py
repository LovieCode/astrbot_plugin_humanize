from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from astrbot_plugin_humanize.humanize.domain.models import (
    Action,
    EventState,
    FinalOutcome,
    MessageContext,
    PreparedRequest,
)
from astrbot_plugin_humanize.main import HumanizePlugin

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.response import AgentResponse
from astrbot.core.agent.message import Message, TextPart, ThinkPart
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.star.star_handler import EventType, star_handlers_registry


class _FakeEvent:
    def __init__(self, result: MessageEventResult | None = None) -> None:
        self.extras: dict[str, object] = {}
        self.result = result
        self.sent: list[str] = []
        self.stopped = False
        self.unified_msg_origin = "group-1"

    def set_extra(self, key: str, value: object) -> None:
        self.extras[key] = value

    def get_extra(self, key: str, default=None):
        return self.extras.get(key, default)

    def get_result(self) -> MessageEventResult | None:
        return self.result

    def set_result(self, result: MessageEventResult) -> None:
        self.result = result

    def clear_result(self) -> None:
        self.result = None

    def stop_event(self) -> None:
        self.stopped = True

    def is_stopped(self) -> bool:
        return self.stopped

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    async def send(self, chain: MessageChain | None) -> None:
        if chain is not None:
            self.sent.append(chain.get_plain_text())


def _context(user_text: str = "hello") -> MessageContext:
    return MessageContext(
        request_id="req-1",
        scope_type="group",
        scope_id="group-1",
        message_id="msg-1",
        sender_id="user-1",
        sender_name="小明",
        user_text=user_text,
        chat_scene="QQ群",
        admin_name="管理员",
        admin_ids=("admin-1",),
    )


def test_history_sync_restores_user_and_cleans_current_assistant() -> None:
    message_xml = "<Msg>hello</Msg>"
    raw_output = '<AgentResponse version="1" />'
    reasoning = ThinkPart(think="internal")
    known_terms = TextPart(text="<KnownTerms />").mark_as_temp()
    run_context = SimpleNamespace(
        messages=[
            Message(role="assistant", content="previous reply"),
            Message(
                role="user",
                content=[TextPart(text=message_xml), known_terms],
            ),
            Message(
                role="assistant",
                content=[reasoning, TextPart(text=raw_output)],
            ),
        ]
    )

    user_index = HumanizePlugin._restore_current_user_message(
        run_context, message_xml, "hello"
    )
    assistant = HumanizePlugin._replace_current_assistant_message(
        run_context,
        user_index=user_index,
        raw_output=raw_output,
        clean_text="第一条\n第二条",
    )

    assert user_index == 1
    assert run_context.messages[1].content[0].text == "hello"
    assert run_context.messages[1].content[1] is known_terms
    assert run_context.messages[0].content == "previous reply"
    assert assistant is run_context.messages[2]
    assert assistant.content[0] is reasoning
    assert assistant.content[1].text == "第一条\n第二条"


def test_blocked_history_removes_only_current_assistant() -> None:
    raw_output = "<broken>"
    run_context = SimpleNamespace(
        messages=[
            Message(role="assistant", content=raw_output),
            Message(role="user", content="hello"),
            Message(role="assistant", content=raw_output),
        ]
    )

    assistant = HumanizePlugin._replace_current_assistant_message(
        run_context,
        user_index=1,
        raw_output=raw_output,
        clean_text="",
    )

    assert assistant is None
    assert [(message.role, message.content) for message in run_context.messages] == [
        ("assistant", raw_output),
        ("user", "hello"),
    ]


def test_residual_tool_fields_do_not_bypass_response_firewall() -> None:
    class Service:
        called = False

        async def process_final_response(self, context, raw_output, **kwargs):
            self.called = True
            return FinalOutcome(
                valid=False,
                error_code="malformed_xml",
                error_detail="not xml",
            )

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        response = LLMResponse(
            role="assistant",
            completion_text="plain text",
            tools_call_args=[{"query": "x"}],
            tools_call_ids=["call-1"],
            tools_call_name=[],
        )

        await plugin.enforce_response_protocol(event, response)

        assert service.called
        assert event.stopped
        assert event.get_extra("_humanize_state") == EventState.FINAL_BLOCKED.value
        assert response.completion_text == ""

    asyncio.run(scenario())


def test_tool_stage_text_requires_a_valid_final_action() -> None:
    class Service:
        calls: list[str] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            self.calls.append(raw_output)
            if "<Action>Reply</Action>" in raw_output:
                return FinalOutcome(
                    valid=True,
                    action=Action.REPLY,
                    messages=("允许显示",),
                )
            return FinalOutcome(valid=False, error_code="malformed_xml")

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)

        for state in (EventState.REQUESTED, EventState.TOOL_RUNNING):
            event = _FakeEvent(MessageEventResult(chain=[Plain("我先查一下")]))
            event.set_extra("_humanize_state", state.value)
            event.set_extra("_humanize_context", _context())
            event.set_extra("_humanize_tool_history_replacements", {})

            await plugin.dispatch_response(event)
            await plugin.finalize_decoration(event)

            assert not event.stopped
            assert event.result is None
            assert event.sent == []

        direct = _FakeEvent()
        direct.set_extra("_humanize_state", EventState.REQUESTED.value)
        direct.set_extra("_humanize_context", _context())
        direct.set_extra("_humanize_tool_history_replacements", {})
        await plugin.prepare_message_event(direct)

        assert direct.get_extra("enable_streaming") is False
        await direct.send(MessageChain(chain=[Plain("调用工具")], type="tool_call"))
        await direct.send(MessageChain([Plain("没有 Action")]))
        await direct.send(MessageChain([Plain("没有 Action")]))
        assert direct.sent == []
        assert service.calls.count("没有 Action") == 1

        valid_xml = (
            '<AgentResponse version="1"><Action>Reply</Action>'
            "<UnknownTerms /><Reply><Message>允许显示</Message></Reply>"
            "</AgentResponse>"
        )
        await direct.send(MessageChain([Plain(valid_xml)]))
        assert direct.sent == ["允许显示"]

    asyncio.run(scenario())


def test_validated_send_does_not_authorize_concurrent_raw_text() -> None:
    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(valid=False, error_code="malformed_xml")

    class BlockingEvent(_FakeEvent):
        def __init__(self) -> None:
            super().__init__()
            self.send_started = asyncio.Event()
            self.release_send = asyncio.Event()

        async def send(self, chain: MessageChain | None) -> None:
            self.send_started.set()
            await self.release_send.wait()
            await super().send(chain)

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = BlockingEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        await plugin.prepare_message_event(event)

        validated_send = asyncio.create_task(
            plugin._send_messages(event, ("允许显示",))
        )
        await event.send_started.wait()
        await event.send(MessageChain([Plain("没有 Action")]))
        event.release_send.set()
        await validated_send

        assert event.sent == ["允许显示"]

    asyncio.run(scenario())


def test_tool_stage_history_replaces_suppressed_raw_text() -> None:
    raw = "我先查一下"
    run_context = SimpleNamespace(
        messages=[
            Message(role="user", content="hello"),
            Message(
                role="assistant",
                content=[TextPart(text=raw)],
                tool_calls=[{"id": "call-1", "type": "function"}],
            ),
            Message(role="tool", content="result", tool_call_id="call-1"),
        ]
    )
    replacements = {hashlib.sha256(raw.encode()).hexdigest(): ""}

    HumanizePlugin._sanitize_tool_assistant_messages(
        run_context,
        user_index=0,
        replacements=replacements,
    )

    assert run_context.messages[1].content is None
    assert run_context.messages[1].tool_calls
    assert run_context.messages[2].content == "result"


def test_core_run_agent_intermediate_text_reaches_action_gate() -> None:
    class Service:
        called = False

        async def process_final_response(self, context, raw_output, **kwargs):
            self.called = True
            return FinalOutcome(valid=False, error_code="malformed_xml")

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})

        class Runner:
            streaming = False
            run_context = SimpleNamespace(context=SimpleNamespace(event=event))

            async def step(self):
                yield AgentResponse(
                    type="llm_result",
                    data={"chain": MessageChain([Plain("工具前附带文字")])},
                )

            @staticmethod
            def done() -> bool:
                return True

        async for _ in run_agent(Runner(), show_tool_use=False):
            assert event.get_result() is not None
            await plugin.dispatch_response(event)

        assert service.called
        assert event.get_result() is None
        assert event.sent == []

    asyncio.run(scenario())


def test_request_appends_full_protocol_after_known_terms() -> None:
    class Service:
        async def prepare_request(self, context):
            return PreparedRequest(
                protocol_prompt="<HumanizeProtocol />",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        plugin._build_message_context = lambda event, text: _context(text)
        event = _FakeEvent()
        request = ProviderRequest(prompt="hello", system_prompt="persona")

        await plugin.on_llm_request(event, request)

        assert request.prompt == "<Msg>hello</Msg>"
        assert request.system_prompt == "persona"
        assert [part.text for part in request.extra_user_content_parts] == [
            "<KnownTerms />",
            request.extra_user_content_parts[-1].text,
        ]
        contract = request.extra_user_content_parts[-1].text
        assert contract.startswith("<HumanizeProtocol />\n\n")
        assert '<ResponseContract version="1">' in contract
        assert "legacy history and are invalid output examples" in contract
        assert "including after any tool call" in contract
        assert "exactly one complete AgentResponse XML document" in contract
        assert contract.endswith("</ResponseContract>")

    asyncio.run(scenario())


def test_both_injection_mode_keeps_user_protocol_and_system_copy() -> None:
    class Service:
        async def prepare_request(self, context):
            return PreparedRequest(
                protocol_prompt="<HumanizeProtocol />",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(
            SimpleNamespace(), {"protocol_injection_mode": "both"}
        )
        plugin._container = SimpleNamespace(service=Service())
        plugin._build_message_context = lambda event, text: _context(text)
        event = _FakeEvent()
        request = ProviderRequest(prompt="hello", system_prompt="persona")

        await plugin.on_llm_request(event, request)

        assert request.system_prompt == "persona\n\n<HumanizeProtocol />"
        assert request.extra_user_content_parts[-1].text.startswith(
            "<HumanizeProtocol />\n\n"
        )

    asyncio.run(scenario())


def test_firewall_preparation_handler_runs_first() -> None:
    handlers = star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent
    )
    priorities = {
        handler.handler_name: handler.extras_configs.get("priority", 0)
        for handler in handlers
        if "astrbot_plugin_humanize" in handler.handler_module_path
    }

    assert priorities["prepare_message_event"] == 100_000


def test_no_reply_keeps_user_history_without_stopping_pipeline() -> None:
    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(valid=True, action=Action.NO_REPLY)

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent(MessageEventResult(chain=[Plain(" ")]))
        context = _context("先看看")
        message_xml = "<Msg>先看看</Msg>"
        raw_output = (
            '<AgentResponse version="1"><Action>No Reply</Action>'
            "<UnknownTerms /><Reply /></AgentResponse>"
        )
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", context)
        event.set_extra("_humanize_message_xml", message_xml)
        event.set_extra("_humanize_history_sync_required", True)
        response = LLMResponse(role="assistant", completion_text=raw_output)

        await plugin.enforce_response_protocol(event, response)
        run_context = SimpleNamespace(
            messages=[
                Message(role="user", content=[TextPart(text=message_xml)]),
                Message(role="assistant", content=raw_output),
            ]
        )
        await plugin.synchronize_agent_history(event, run_context, response)
        await plugin.finalize_agent_history(event, run_context, response)

        assert not event.stopped
        assert response.completion_text == " "
        assert len(run_context.messages) == 1
        assert run_context.messages[0].content[0].text == "先看看"

        class HistoryStage:
            async def process(self, current_event):
                yield
                current_event.history_saved = not current_event.is_stopped()

        class DecorationStage:
            async def process(self, current_event):
                await plugin.dispatch_response(current_event)
                await plugin.finalize_decoration(current_event)

        scheduler = object.__new__(PipelineScheduler)
        scheduler.stages = [HistoryStage(), DecorationStage()]
        await scheduler._process_stages(event)

        assert not event.stopped
        assert event.history_saved
        assert event.result is None
        assert event.sent == []

        class ConversationManager:
            history = None

            async def update_conversation(
                self, umo, cid, *, history, token_usage
            ) -> None:
                self.history = history

        manager = ConversationManager()
        stage = object.__new__(InternalAgentSubStage)
        stage.conv_manager = manager
        request = ProviderRequest(conversation=SimpleNamespace(cid="conversation-1"))
        await stage._save_to_history(
            event,
            request,
            response,
            run_context.messages,
            runner_stats=None,
        )

        assert manager.history == [
            {
                "role": "user",
                "content": [{"type": "text", "text": "先看看"}],
            }
        ]

    asyncio.run(scenario())


def test_dispatch_respects_prior_rejection_and_splits_modified_plain_text() -> None:
    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        rejected = _FakeEvent(MessageEventResult(chain=[]))
        rejected.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        rejected.set_extra("_humanize_messages", ("原回复",))

        await plugin.dispatch_response(rejected)
        assert rejected.sent == []

        modified = _FakeEvent(
            MessageEventResult(chain=[Plain("这是其他插件修改后的较长回复")])
        )
        modified.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        modified.set_extra("_humanize_messages", ("原回复",))

        await plugin.dispatch_response(modified)

        assert modified.result is None
        assert len(modified.sent) == 2
        assert all(len(message) <= 10 for message in modified.sent)
        assert "".join(modified.sent) == "这是其他插件修改后的较长回复"

    asyncio.run(scenario())


def test_third_party_runner_skips_local_history_sync() -> None:
    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("收到",),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent(MessageEventResult(chain=[Plain("raw")]))
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_message_xml", "<Msg>hello</Msg>")
        event.set_extra("_humanize_history_sync_required", False)
        response = LLMResponse(role="assistant", completion_text="raw")

        await plugin.enforce_response_protocol(event, response)
        await plugin.synchronize_agent_history(
            event,
            SimpleNamespace(messages=[]),
            response,
        )

        assert not event.stopped
        assert event.get_extra("_humanize_state") == EventState.FINAL_VALID.value
        assert response.completion_text == "收到"

    asyncio.run(scenario())
