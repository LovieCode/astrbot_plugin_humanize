from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from astrbot_plugin_humanize import main as humanize_main
from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.errors import ProtocolValidationError
from astrbot_plugin_humanize.humanize.domain.models import (
    Action,
    ContextSection,
    EventState,
    FinalOutcome,
    MessageContext,
    PreparedRequest,
)
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.protocol.parser import ProtocolParser
from astrbot_plugin_humanize.main import HumanizePlugin

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image, Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.message import Message, TextPart, ThinkPart
from astrbot.core.agent.response import AgentResponse
from astrbot.core.astr_agent_run_util import run_agent
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
    InternalAgentSubStage,
)
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.star.star_handler import EventType, star_handlers_registry


class _FakeEvent:
    def __init__(self, result: MessageEventResult | None = None) -> None:
        self.extras: dict[str, object] = {}
        self.result = result
        self.sent: list[str] = []
        self.sent_chains: list[MessageChain] = []
        self.stopped = False
        self.unified_msg_origin = "group-1"
        self.message_str = "hello"

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

    def get_message_str(self) -> str:
        return self.message_str

    async def send(self, chain: MessageChain | None) -> None:
        if chain is not None:
            self.sent_chains.append(chain)
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


async def _async_build_context(event, text):
    return _context(text)


def test_private_context_uses_sender_name_in_chat_scene() -> None:
    plugin = HumanizePlugin(SimpleNamespace(), {})
    event = SimpleNamespace(
        is_private_chat=lambda: True,
        get_sender_name=lambda: "小明",
        get_sender_id=lambda: "user-1",
        unified_msg_origin="private-1",
        message_obj=SimpleNamespace(message_id="msg-1", timestamp=0),
    )

    async def scenario() -> None:
        context = await plugin._build_message_context(event, "你好")
        assert context.chat_scene == "QQ 上和小明"

    asyncio.run(scenario())


def test_initialize_keeps_stable_memory_identity_when_memory_is_disabled(
    monkeypatch,
) -> None:
    class Repository:
        async def initialize(self):
            return None

        async def get_prompt_templates(self):
            return {"templates": {}}

    class Memory:
        def __init__(self) -> None:
            self._config = PluginConfig(memory_enabled=False)
            self._state = "disabled"
            self._reason = "disabled"
            self.secret = b""
            self.initialized_with_enabled = False
            self.worker_starts = 0

        async def initialize(self):
            self.initialized_with_enabled = self._config.memory_enabled
            if not self._config.memory_enabled:
                return
            self.secret = b"stable-identity"
            self._state = "ready"

        def start_worker(self):
            self.worker_starts += 1

        async def get_status(self):
            return {"state": self._state, "reason": self._reason}

    class Envelope:
        def set_templates(self, templates):
            self.templates = templates

    class AppContext:
        def __init__(self) -> None:
            self.routes: list[tuple] = []

        def register_web_api(self, *args):
            self.routes.append(args)

    async def scenario() -> None:
        memory = Memory()
        envelope = Envelope()
        container = SimpleNamespace(
            repository=Repository(),
            memory=memory,
            envelope=envelope,
            web_api=SimpleNamespace(dispatch=lambda: None),
        )
        monkeypatch.setattr(
            "astrbot_plugin_humanize.main.Container.build",
            lambda config, context: container,
        )
        context = AppContext()
        plugin = HumanizePlugin(context, {"memory_enabled": False})

        await plugin.initialize()

        assert memory.initialized_with_enabled is True
        assert memory.secret == b"stable-identity"
        assert memory._config.memory_enabled is False
        assert memory._state == "disabled"
        assert memory.worker_starts == 0
        assert context.routes

    asyncio.run(scenario())


def test_initialize_fails_open_when_memory_identity_setup_fails(monkeypatch) -> None:
    class Repository:
        async def initialize(self):
            return None

        async def get_prompt_templates(self):
            return {"templates": {}}

    class Memory:
        def __init__(self) -> None:
            self._config = PluginConfig()
            self.worker_starts = 0

        async def initialize(self):
            raise OSError("identity path unavailable")

        def start_worker(self):
            self.worker_starts += 1

        async def get_status(self):
            return {"state": "error", "reason": "identity_initialization_failed"}

    class AppContext:
        def __init__(self) -> None:
            self.routes: list[tuple] = []

        def register_web_api(self, *args):
            self.routes.append(args)

    async def scenario() -> None:
        memory = Memory()
        envelope = SimpleNamespace(set_templates=lambda templates: None)
        container = SimpleNamespace(
            repository=Repository(),
            memory=memory,
            envelope=envelope,
            web_api=SimpleNamespace(dispatch=lambda: None),
        )
        monkeypatch.setattr(
            "astrbot_plugin_humanize.main.Container.build",
            lambda config, context: container,
        )
        context = AppContext()
        plugin = HumanizePlugin(context, {})

        await plugin.initialize()

        assert plugin._container is container
        assert memory.worker_starts == 0
        assert context.routes

    asyncio.run(scenario())


def test_history_sync_restores_user_and_cleans_current_assistant() -> None:
    message_xml = "<Msg>hello</Msg>"
    raw_output = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\nraw"
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
                error_code="invalid_control_header",
                error_detail="missing control header",
            )

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(
            SimpleNamespace(), {"protocol_repair_retry_enabled": False}
        )
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


def test_response_snapshot_keeps_tool_turns_without_overwriting_final() -> None:
    event = _FakeEvent()
    tool_response = LLMResponse(
        role="assistant",
        completion_text="tool preface",
        tools_call_args=[{"query": "x"}],
        tools_call_name=["search"],
        tools_call_ids=["call-1"],
    )
    final_response = LLMResponse(
        role="assistant",
        completion_text="<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n完成",
        reasoning_content="provider reasoning metadata",
    )

    HumanizePlugin._capture_llm_response_snapshot(event, tool_response)
    HumanizePlugin._capture_llm_response_snapshot(event, final_response)
    snapshot, complete = HumanizePlugin._response_snapshot_for_record(event)

    assert complete is True
    assert [item["phase"] for item in snapshot["responses"]] == ["tool", "final"]
    assert snapshot["final_response"]["response"]["fields"]["completion_text"] == (
        final_response.completion_text
    )
    assert (
        snapshot["final_response"]["response"]["fields"]["reasoning_content"]
        == "provider reasoning metadata"
    )


def test_terminal_error_response_is_classified_as_the_final_snapshot() -> None:
    event = _FakeEvent()
    response = LLMResponse(role="err", completion_text="provider unavailable")

    HumanizePlugin._capture_llm_response_snapshot(event, response)
    snapshot, complete = HumanizePlugin._response_snapshot_for_record(event)

    assert complete is True
    assert len(snapshot["responses"]) == 1
    assert snapshot["responses"][0]["phase"] == "final"
    assert snapshot["final_response"]["response"]["fields"]["role"] == "err"
    assert (
        snapshot["final_response"]["response"]["fields"]["completion_text"]
        == "provider unavailable"
    )


def test_early_duplicate_is_sent_once_and_records_final_snapshot() -> None:
    first_raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n\n反弹"
    final_raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n反弹"

    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("反弹",),
            )

        async def record_protocol_success(self, context, **kwargs):
            self.successes.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_history_sync_required", False)
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_tool_sent_messages", [])
        event.set_extra("_humanize_final_response_dispatched", False)

        event.set_result(MessageEventResult(chain=[Plain(first_raw)]))
        await plugin.dispatch_response(event)
        event.set_result(MessageEventResult(chain=[Plain(final_raw)]))
        await plugin.dispatch_response(event)

        response = LLMResponse(
            role="assistant",
            completion_text=final_raw,
            reasoning_content="final reasoning",
        )
        await plugin.enforce_response_protocol(event, response)
        await plugin.synchronize_agent_history(
            event,
            SimpleNamespace(messages=[]),
            response,
        )
        assert event.get_extra("_humanize_final_response_dispatched") is False
        assert event.get_extra("_humanize_final_protocol_log_pending") is True
        event.set_result(MessageEventResult(chain=[Plain(response.completion_text)]))
        await plugin.dispatch_response(event)

        assert event.sent == ["反弹"]
        assert event.get_extra("_humanize_final_response_dispatched") is True
        assert event.get_extra("_humanize_final_protocol_log_pending") is False
        assert len(service.successes) == 2
        tool_record, recorded = service.successes
        assert tool_record["stage"] == "tool"
        assert tool_record["messages"] == ("反弹",)
        assert recorded["stage"] == "final"
        assert recorded["raw_output"] == final_raw
        assert recorded["messages"] == ("反弹",)
        assert recorded["response_snapshot_complete"] is True
        assert (
            recorded["response_snapshot"]["final_response"]["response"]["fields"][
                "completion_text"
            ]
            == final_raw
        )
        assert (
            recorded["response_snapshot"]["final_response"]["response"]["fields"][
                "reasoning_content"
            ]
            == "final reasoning"
        )

    asyncio.run(scenario())


def test_dispatch_records_the_exact_decorated_outbound_once() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent(
            MessageEventResult(chain=[Plain("其他插件最终修改后的正文")])
        )
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_messages", ("模型原正文",))
        event.set_extra("_humanize_dispatched_messages", [])
        event.set_extra("_humanize_tool_sent_messages", [])
        event.set_extra("_humanize_validated_output", "模型原始协议输出")
        event.set_extra("_humanize_final_protocol_log_pending", True)
        event.set_extra("_humanize_final_response_dispatched", False)

        await plugin.dispatch_response(event)
        event.set_result(MessageEventResult(chain=[Plain("重复装饰结果")]))
        await plugin.dispatch_response(event)

        assert event.sent == ["其他插件最终修改后的正文"]
        assert len(service.successes) == 1
        assert service.successes[0]["messages"] == ("其他插件最终修改后的正文",)
        assert event.get_extra("_humanize_final_response_dispatched") is True
        assert event.get_extra("_humanize_final_protocol_log_pending") is False

    asyncio.run(scenario())


def test_agent_done_waits_for_normal_result_decorators_before_dispatch() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_history_sync_required", False)
        event.set_extra("_humanize_messages", ("模型原正文",))
        event.set_extra("_humanize_dispatched_messages", [])
        event.set_extra("_humanize_validated_output", "模型原始协议输出")
        event.set_extra("_humanize_final_protocol_log_pending", True)
        event.set_extra("_humanize_final_response_dispatched", False)
        response = LLMResponse(role="assistant", completion_text="模型原正文")

        await plugin.synchronize_agent_history(
            event,
            SimpleNamespace(messages=[]),
            response,
        )

        assert event.sent == []
        assert event.get_extra("_humanize_final_response_dispatched") is False
        event.set_result(MessageEventResult(chain=[Plain("装饰后的最终正文")]))
        await plugin.dispatch_response(event)

        assert event.sent == ["装饰后的最终正文"]
        assert service.successes[0]["messages"] == ("装饰后的最终正文",)

    asyncio.run(scenario())


def test_decorator_rewrite_cannot_expose_protocol_control_tags() -> None:
    class Service:
        def __init__(self) -> None:
            self.failures: list[dict] = []

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent(
            MessageEventResult(
                chain=[Plain("改写正文<Action>Reply</Action><Message>泄漏</Message>")]
            )
        )
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_messages", ("模型正文",))
        event.set_extra("_humanize_dispatched_messages", [])
        event.set_extra("_humanize_tool_sent_messages", [])
        event.set_extra("_humanize_validated_output", "模型原始协议输出")
        event.set_extra("_humanize_final_protocol_log_pending", True)
        event.set_extra("_humanize_final_response_dispatched", False)

        await plugin.dispatch_response(event)

        assert event.sent == []
        assert event.get_extra("_humanize_state") == EventState.FINAL_BLOCKED.value
        assert (
            event.get_extra("_humanize_protocol_error")
            == "decorated_response_control_tag_leak"
        )
        assert len(service.failures) == 1
        assert service.failures[0]["messages"] == ()
        assert event.get_extra("_humanize_final_protocol_log_pending") is False

    asyncio.run(scenario())


def test_final_log_pending_is_cleared_only_after_confirmed_persistence() -> None:
    class Service:
        def __init__(self) -> None:
            self.results = [False, True]
            self.calls = 0

        async def record_protocol_success(self, context, **kwargs):
            del context, kwargs
            result = self.results[self.calls]
            self.calls += 1
            return result

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_dispatched_messages", ["真实正文"])
        event.set_extra("_humanize_validated_output", "模型原始协议输出")
        event.set_extra("_humanize_final_protocol_log_pending", True)

        assert await plugin._record_final_protocol_success(event) is False
        assert event.get_extra("_humanize_final_protocol_log_pending") is True
        assert await plugin._record_final_protocol_success(event) is True
        assert event.get_extra("_humanize_final_protocol_log_pending") is False
        assert service.calls == 2

    asyncio.run(scenario())


def test_firewall_terminal_failure_is_logged_with_snapshot_without_pending_flag() -> (
    None
):
    class Service:
        def __init__(self) -> None:
            self.failures: list[dict] = []

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_final_protocol_log_pending", False)
        response = LLMResponse(
            role="assistant",
            completion_text="未经过响应防火墙的原始回复",
            reasoning_content="完整推理快照",
        )

        await plugin.synchronize_agent_history(
            event,
            SimpleNamespace(messages=[]),
            response,
        )
        await plugin.synchronize_agent_history(
            event,
            SimpleNamespace(messages=[]),
            response,
        )

        assert len(service.failures) == 1
        recorded = service.failures[0]
        assert recorded["error_code"] == "response_firewall_not_applied"
        assert recorded["raw_output"] == "未经过响应防火墙的原始回复"
        assert recorded["response_snapshot_complete"] is True
        fields = recorded["response_snapshot"]["final_response"]["response"]["fields"]
        assert fields["completion_text"] == "未经过响应防火墙的原始回复"
        assert fields["reasoning_content"] == "完整推理快照"

    asyncio.run(scenario())


def test_partial_dispatch_failure_records_only_delivered_text_and_no_success() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []
            self.failures: list[dict] = []

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    class FailingSecondSendEvent(_FakeEvent):
        async def send(self, chain: MessageChain | None) -> None:
            if len(self.sent_chains) == 1:
                raise RuntimeError("platform send failed")
            await super().send(chain)

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = FailingSecondSendEvent(
            MessageEventResult(chain=[Plain("第一条\n第二条")])
        )
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_messages", ("第一条", "第二条"))
        event.set_extra("_humanize_dispatched_messages", [])
        event.set_extra("_humanize_tool_sent_messages", [])
        event.set_extra("_humanize_validated_output", "有效协议输出")
        event.set_extra("_humanize_final_protocol_log_pending", True)
        event.set_extra("_humanize_final_response_dispatched", False)

        await plugin.dispatch_response(event)

        assert event.sent == ["第一条"]
        assert service.successes == []
        assert len(service.failures) == 1
        assert service.failures[0]["error_code"] == "response_dispatch_failed"
        assert service.failures[0]["messages"] == ("第一条",)
        assert event.get_extra("_humanize_state") == EventState.FINAL_BLOCKED.value
        assert event.get_extra("_humanize_final_protocol_log_pending") is False

    asyncio.run(scenario())


def test_reply_block_with_framing_blank_dispatches_only_message_text() -> None:
    raw = (
        "<Action>Reply</Action>\n"
        "<UnknownTerms>[]</UnknownTerms>\n\n"
        "<Reply><Message>贴贴真好</Message></Reply>"
    )

    class Service:
        def __init__(self) -> None:
            self.parser = ProtocolParser(PluginConfig())

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, kwargs
            decision = self.parser.parse(raw_output)
            return FinalOutcome(
                valid=True,
                action=decision.action,
                messages=decision.messages,
                unknown_terms=decision.unknown_terms,
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_history_sync_required", False)
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_final_response_dispatched", False)
        response = LLMResponse(role="assistant", completion_text=raw)

        await plugin.enforce_response_protocol(event, response)
        event.set_result(MessageEventResult(chain=[Plain(response.completion_text)]))
        await plugin.dispatch_response(event)

        assert response.completion_text == "贴贴真好"
        assert event.sent == ["贴贴真好"]
        assert "<Reply>" not in event.sent[0]
        assert "<Message>" not in event.sent[0]

    asyncio.run(scenario())


def test_tool_stage_text_requires_a_valid_final_action() -> None:
    class Service:
        calls: list[str] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            self.calls.append(raw_output)
            if raw_output.startswith("<Action>Reply</Action>\n"):
                return FinalOutcome(
                    valid=True,
                    action=Action.REPLY,
                    messages=("允许显示",),
                )
            return FinalOutcome(valid=False, error_code="invalid_control_header")

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

        valid_response = (
            "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n允许显示"
        )
        await direct.send(MessageChain([Plain(valid_response)]))
        assert direct.sent == ["允许显示"]

    asyncio.run(scenario())


def test_tool_stage_never_sends_control_tags_returned_by_the_service() -> None:
    class Service:
        def __init__(self) -> None:
            self.failures: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output, kwargs
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("&lt;Message&gt;泄漏&lt;/Message&gt;",),
            )

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})

        await plugin._process_tool_stage_payload(event, "伪装成有效协议")

        assert event.sent == []
        assert len(service.failures) == 1
        assert service.failures[0]["error_code"] == "tool_response_control_tag_leak"
        assert service.failures[0]["messages"] == ()

    asyncio.run(scenario())


def test_multi_message_dispatch_waits_between_sends(monkeypatch) -> None:
    timeline: list[tuple[str, str | float]] = []

    class TrackingEvent(_FakeEvent):
        async def send(self, chain: MessageChain | None) -> None:
            assert chain is not None
            timeline.append(("send", chain.get_plain_text()))
            await super().send(chain)

    async def fake_sleep(seconds: float) -> None:
        timeline.append(("sleep", seconds))

    async def scenario() -> None:
        plugin = HumanizePlugin(
            SimpleNamespace(),
            {"general": {"message_interval_seconds": 0.8}},
        )
        event = TrackingEvent()

        await plugin._send_messages(event, ("第一条", "第二条", "第三条"))

        assert event.sent == ["第一条", "第二条", "第三条"]
        assert timeline == [
            ("send", "第一条"),
            ("sleep", 0.8),
            ("send", "第二条"),
            ("sleep", 0.8),
            ("send", "第三条"),
        ]

    monkeypatch.setattr(humanize_main.asyncio, "sleep", fake_sleep)
    asyncio.run(scenario())


def test_tool_stage_records_success_only_after_actual_dispatch() -> None:
    class Service:
        def __init__(self) -> None:
            self.process_kwargs: list[dict] = []
            self.successes: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output
            self.process_kwargs.append(kwargs)
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("第一条", "第二条"),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_dispatched_messages", [])
        event.set_extra("_humanize_provider_id", "provider-a")

        await plugin._process_tool_stage_payload(event, "有效工具阶段协议")

        assert service.process_kwargs[0]["record_success"] is False
        assert event.sent == ["第一条", "第二条"]
        assert len(service.successes) == 1
        assert service.successes[0]["messages"] == ("第一条", "第二条")
        assert service.successes[0]["provider_id"] == "provider-a"
        assert service.successes[0]["stage"] == "tool"

    asyncio.run(scenario())


def test_tool_stage_partial_dispatch_records_only_sent_prefix() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []
            self.failures: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output
            assert kwargs["record_success"] is False
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("第一条", "第二条"),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    class FailingSecondSendEvent(_FakeEvent):
        async def send(self, chain: MessageChain | None) -> None:
            if len(self.sent_chains) == 1:
                raise RuntimeError("platform send failed")
            await super().send(chain)

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = FailingSecondSendEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_dispatched_messages", [])

        await plugin._process_tool_stage_payload(event, "有效工具阶段协议")

        assert event.sent == ["第一条"]
        assert service.successes == []
        assert len(service.failures) == 1
        assert service.failures[0]["messages"] == ("第一条",)
        assert service.failures[0]["stage"] == "tool"
        assert event.get_extra("_humanize_tool_sent_messages") == ["第一条"]

    asyncio.run(scenario())


def test_tool_stage_partial_dispatch_can_retry_only_the_unsent_suffix() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []
            self.failures: list[dict] = []
            self.process_calls = 0

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output, kwargs
            self.process_calls += 1
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("第一条", "第二条"),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

        async def record_protocol_failure(self, context, **kwargs):
            del context
            self.failures.append(kwargs)
            return True

    class FailOnceOnSecondSendEvent(_FakeEvent):
        def __init__(self) -> None:
            super().__init__()
            self.send_attempts = 0

        async def send(self, chain: MessageChain | None) -> None:
            self.send_attempts += 1
            if self.send_attempts == 2:
                raise RuntimeError("platform send failed once")
            await super().send(chain)

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = FailOnceOnSecondSendEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_dispatched_messages", [])

        await plugin._process_tool_stage_payload(event, "可重试工具阶段协议")
        await plugin._process_tool_stage_payload(event, "可重试工具阶段协议")

        assert event.sent == ["第一条", "第二条"]
        assert service.process_calls == 2
        assert len(service.failures) == 1
        assert service.failures[0]["messages"] == ("第一条",)
        assert len(service.successes) == 1
        assert service.successes[0]["messages"] == ("第二条",)
        assert event.get_extra("_humanize_tool_sent_messages") == [
            "第一条",
            "第二条",
        ]

    asyncio.run(scenario())


def test_concurrent_tool_stage_payloads_reserve_delivery_atomically() -> None:
    class Service:
        def __init__(self) -> None:
            self.processed: list[str] = []
            self.successes: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, kwargs
            self.processed.append(raw_output)
            await asyncio.sleep(0)
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("并发结果",),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

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
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = BlockingEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_dispatched_messages", [])

        first = asyncio.create_task(
            plugin._process_tool_stage_payload(event, "并发协议 A")
        )
        await event.send_started.wait()
        duplicate = asyncio.create_task(
            plugin._process_tool_stage_payload(event, "并发协议 A")
        )
        equivalent = asyncio.create_task(
            plugin._process_tool_stage_payload(event, "并发协议 B")
        )
        await asyncio.sleep(0)
        event.release_send.set()
        await asyncio.gather(first, duplicate, equivalent)

        assert event.sent == ["并发结果"]
        assert service.processed == ["并发协议 A", "并发协议 B"]
        assert len(service.successes) == 1
        assert service.successes[0]["messages"] == ("并发结果",)

    asyncio.run(scenario())


def test_final_dispatch_waits_for_inflight_tool_delivery_before_deduping() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output, kwargs
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("同一结果",),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

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
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = BlockingEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        event.set_extra("_humanize_dispatched_messages", [])

        tool_dispatch = asyncio.create_task(
            plugin._process_tool_stage_payload(event, "工具阶段协议")
        )
        await event.send_started.wait()

        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_messages", ("同一结果",))
        event.set_extra("_humanize_validated_output", "最终协议")
        event.set_extra("_humanize_final_protocol_log_pending", True)
        event.set_result(MessageEventResult(chain=[Plain("同一结果")]))
        final_dispatch = asyncio.create_task(plugin.dispatch_response(event))
        await asyncio.sleep(0)

        assert not final_dispatch.done()
        event.release_send.set()
        await asyncio.gather(tool_dispatch, final_dispatch)

        assert event.sent == ["同一结果"]
        assert event.get_extra("_humanize_state") == EventState.DISPATCHED.value
        assert [item["stage"] for item in service.successes] == ["tool", "final"]
        assert service.successes[-1]["messages"] == ("同一结果",)

    asyncio.run(scenario())


def test_queued_tool_payload_cannot_send_after_terminal_state() -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = 0

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, raw_output, kwargs
            self.calls += 1
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("迟到的工具文本",),
            )

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        lock = asyncio.Lock()
        await lock.acquire()
        event.set_extra("_humanize_tool_send_lock", lock)

        queued = asyncio.create_task(
            plugin._process_tool_stage_payload(event, "排队中的工具协议")
        )
        await asyncio.sleep(0)
        event.set_extra("_humanize_state", EventState.DISPATCHED.value)
        lock.release()
        await queued

        assert service.calls == 0
        assert event.sent == []

    asyncio.run(scenario())


def test_tool_stage_media_is_preserved_and_mixed_text_is_validated() -> None:
    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            if raw_output.startswith("<Action>Reply</Action>\n"):
                return FinalOutcome(
                    valid=True,
                    action=Action.REPLY,
                    messages=("图片结果",),
                )
            return FinalOutcome(valid=False, error_code="invalid_control_header")

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        await plugin.prepare_message_event(event)

        image = Image(file="https://example.com/result.png")
        await event.send(MessageChain([image]))
        assert event.sent_chains[-1].chain == [image]

        valid_text = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n图片结果"
        mixed_image = Image(file="https://example.com/mixed.png")
        await event.send(MessageChain([Plain(valid_text), mixed_image]))

        sent = event.sent_chains[-1]
        assert sent.get_plain_text() == "图片结果"
        assert sent.chain[1] is mixed_image

        before = len(event.sent_chains)
        await event.send(MessageChain([Plain("没有控制头"), mixed_image]))
        assert len(event.sent_chains) == before

    asyncio.run(scenario())


def test_repeated_tool_text_keeps_only_new_media() -> None:
    raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n图片结果"

    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("图片结果",),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})

        first_image = Image(file="https://example.com/first.png")
        event.set_result(MessageEventResult(chain=[Plain(raw), first_image]))
        await plugin.dispatch_response(event)

        second_image = Image(file="https://example.com/second.png")
        event.set_result(MessageEventResult(chain=[Plain(raw), second_image]))
        await plugin.dispatch_response(event)
        event.set_result(MessageEventResult(chain=[Plain(raw), second_image]))
        await plugin.dispatch_response(event)

        assert [chain.get_plain_text() for chain in event.sent_chains] == [
            "图片结果",
            "",
        ]
        assert event.sent_chains[0].chain[-1] is first_image
        assert event.sent_chains[1].chain == [second_image]

    asyncio.run(scenario())


def test_valid_final_media_chain_is_sent_when_validated_text_is_unchanged() -> None:
    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=SimpleNamespace())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_messages", ("完成",))
        await plugin.prepare_message_event(event)
        image = Image(file="https://example.com/final.png")
        event.set_result(MessageEventResult(chain=[Plain("完成"), image]))

        await plugin.dispatch_response(event)
        await plugin.finalize_decoration(event)

        assert event.get_extra("_humanize_state") == EventState.DISPATCHED.value
        assert event.result is None
        assert event.sent_chains[-1].get_plain_text() == "完成"
        assert event.sent_chains[-1].chain[1] is image

    asyncio.run(scenario())


def test_valid_final_media_does_not_repeat_tool_stage_text() -> None:
    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=SimpleNamespace())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_messages", ("完成",))
        event.set_extra("_humanize_tool_sent_messages", ["完成"])
        image = Image(file="https://example.com/final.png")
        event.set_result(MessageEventResult(chain=[Plain("完成"), image]))

        await plugin.dispatch_response(event)

        assert event.get_extra("_humanize_state") == EventState.DISPATCHED.value
        assert event.sent == [""]
        assert event.sent_chains[0].chain == [image]

    asyncio.run(scenario())


def test_final_media_does_not_repeat_a_tool_stage_chain() -> None:
    raw = "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n完成"

    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("完成",),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})

        tool_image = Image(file="https://example.com/same.png")
        event.set_result(MessageEventResult(chain=[Plain(raw), tool_image]))
        await plugin.dispatch_response(event)

        final_image = Image(file="https://example.com/same.png")
        event.set_extra("_humanize_state", EventState.FINAL_VALID.value)
        event.set_extra("_humanize_messages", ("完成",))
        event.set_result(MessageEventResult(chain=[Plain("完成"), final_image]))
        await plugin.dispatch_response(event)

        assert event.get_extra("_humanize_state") == EventState.DISPATCHED.value
        assert len(event.sent_chains) == 1
        assert event.sent_chains[0].chain[-1] is tool_image

    asyncio.run(scenario())


def test_validated_send_does_not_authorize_concurrent_raw_text() -> None:
    class Service:
        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(valid=False, error_code="invalid_control_header")

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


def test_hot_reload_never_leaves_an_inflight_old_send_gate_swallowing_text() -> None:
    class Memory:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    async def scenario() -> None:
        memory = Memory()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(
            service=SimpleNamespace(),
            memory=memory,
        )
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        await plugin.prepare_message_event(event)

        lock = asyncio.Lock()
        await lock.acquire()
        event.set_extra("_humanize_tool_send_lock", lock)
        raw = "热重载期间的原始发送"
        send_task = asyncio.create_task(event.send(MessageChain([Plain(raw)])))
        await asyncio.sleep(0)
        assert not send_task.done()

        await plugin.terminate()
        lock.release()
        await send_task

        assert memory.stopped is True
        assert event.sent == [raw]

    asyncio.run(scenario())


def test_hot_reload_new_instance_replaces_and_owns_the_stale_send_gate() -> None:
    class Memory:
        async def stop(self) -> None:
            return None

    class Service:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            del context, kwargs
            self.calls.append(raw_output)
            return FinalOutcome(valid=False, error_code="invalid_control_header")

    async def scenario() -> None:
        old_plugin = HumanizePlugin(SimpleNamespace(), {})
        old_plugin._container = SimpleNamespace(
            service=SimpleNamespace(),
            memory=Memory(),
        )
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_tool_history_replacements", {})
        await old_plugin.prepare_message_event(event)
        stale_send = event.send
        await old_plugin.terminate()

        service = Service()
        new_plugin = HumanizePlugin(SimpleNamespace(), {})
        new_plugin._container = SimpleNamespace(service=service)
        await new_plugin.prepare_message_event(event)

        await stale_send(MessageChain([Plain("没有控制头")]))

        assert service.calls == ["没有控制头"]
        assert event.sent == []

    asyncio.run(scenario())


def test_stale_send_gate_never_leaks_protocol_tags_without_a_new_owner() -> None:
    class Memory:
        async def stop(self) -> None:
            return None

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(
            service=SimpleNamespace(),
            memory=Memory(),
        )
        event = _FakeEvent()
        await plugin.prepare_message_event(event)
        stale_send = event.send
        await plugin.terminate()

        await stale_send(
            MessageChain(
                [Plain("<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>\n泄漏")]
            )
        )

        assert event.sent == []

    asyncio.run(scenario())


def test_validated_messages_are_sent_as_separate_chains() -> None:
    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        event = _FakeEvent()
        await plugin.prepare_message_event(event)

        await plugin._send_messages(event, ("第一条", "第二条"))

        assert event.sent == ["第一条", "第二条"]

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
            return FinalOutcome(valid=False, error_code="invalid_control_header")

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
        async def prepare_request(self, context, **kwargs):
            return PreparedRequest(
                protocol_prompt=EnvelopeBuilder(PluginConfig()).build_protocol_prompt(
                    context
                ),
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=Service())
        plugin._build_message_context = _async_build_context
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
        assert contract.startswith("<Rule>")
        assert contract.count("回复控制协议 v1") == 1
        assert "本块为已知信息" in contract
        assert "不符合要求的内容将发送失败" in contract
        assert "<Action>Reply</Action>" in contract
        assert "即使只有一条消息，也必须使用Messages标签" in contract
        assert "不在<Message>标签中的内容将不会发送给用户" in contract
        assert '"confidence":0.86' in contract

    asyncio.run(scenario())


def test_both_injection_mode_keeps_user_protocol_and_system_copy() -> None:
    class Service:
        async def prepare_request(self, context, **kwargs):
            return PreparedRequest(
                protocol_prompt=EnvelopeBuilder(PluginConfig()).build_protocol_prompt(
                    context
                ),
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    async def scenario() -> None:
        plugin = HumanizePlugin(SimpleNamespace(), {"protocol_injection_mode": "both"})
        plugin._container = SimpleNamespace(service=Service())
        plugin._build_message_context = _async_build_context
        event = _FakeEvent()
        request = ProviderRequest(prompt="hello", system_prompt="persona")

        await plugin.on_llm_request(event, request)

        injected = request.extra_user_content_parts[-1].text
        assert request.system_prompt == f"persona\n\n{injected}"
        assert injected.startswith("<Rule>")
        assert "回复控制协议 v1" in injected

    asyncio.run(scenario())


def test_prompt_cache_prefix_includes_captured_provider_identity() -> None:
    class Service:
        async def prepare_request(self, context, **kwargs):
            return PreparedRequest(
                protocol_prompt="完整协议",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
            )

    class Tracker:
        def __init__(self) -> None:
            self.observations: list[dict] = []

        async def observe(self, **kwargs):
            self.observations.append(kwargs)
            return SimpleNamespace(
                request_fingerprint="request-fingerprint",
                prefix_fingerprint="prefix-fingerprint",
                epoch_id="epoch-1",
                first_difference="",
                longest_common_prefix_chars=0,
                epoch_reason="initial",
            )

    class Provider:
        provider_config = {
            "id": "provider-a",
            "type": "openai_chat_completion",
            "model": "model-a",
            "model_revision": "2026-07-16",
        }

        @staticmethod
        def meta():
            return SimpleNamespace(
                id="provider-a",
                type="openai_chat_completion",
                model="model-a",
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
            return {"provider_settings": {}}

        @staticmethod
        def get_using_provider(umo):
            del umo
            return Provider()

    async def scenario() -> None:
        tracker = Tracker()
        plugin = HumanizePlugin(AppContext(), {})
        plugin._container = SimpleNamespace(service=Service())
        plugin._prompt_cache_tracker = tracker
        plugin._build_message_context = _async_build_context
        event = _FakeEvent()
        request = ProviderRequest(prompt="hello", model="model-a")

        await plugin.on_llm_request(event, request)

        observation = tracker.observations[0]
        expected_identity = {
            "provider_id": "provider-a",
            "provider_type": "openai_chat_completion",
            "model_revision": "2026-07-16",
        }
        assert {
            key: observation["prefix_fields"][key] for key in expected_identity
        } == expected_identity
        assert {
            key: observation["stable_fields"][key] for key in expected_identity
        } == expected_identity
        assert event.get_extra("_humanize_provider_id") == "provider-a"
        assert (
            event.get_extra("_humanize_provider_identity")["model_revision"]
            == "2026-07-16"
        )

    asyncio.run(scenario())


def test_request_applies_composed_sections_before_recording_context_trace() -> None:
    sections = (
        ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="<Msg>hello</Msg>",
        ),
        ContextSection(
            key="known_terms",
            ordinal=1,
            priority=60,
            source_type="repository",
            source_refs=("jargon:1",),
            targets=("temp_user",),
            required=False,
            included=True,
            budget_tokens=256,
            estimated_tokens=6,
            applied_tokens=6,
            item_count=1,
            reason="matched_current_message",
            content="<KnownTerms><Term /></KnownTerms>",
        ),
        ContextSection(
            key="memory_context",
            ordinal=2,
            priority=70,
            source_type="memory",
            source_refs=("memory:4",),
            targets=("temp_user",),
            required=False,
            included=True,
            budget_tokens=2_500,
            estimated_tokens=8,
            applied_tokens=8,
            item_count=1,
            reason="matched",
            content='<MemoryContext><Memory id="4" /></MemoryContext>',
        ),
        ContextSection(
            key="reply_examples",
            ordinal=3,
            priority=65,
            source_type="reply_examples",
            source_refs=("example:7",),
            targets=("temp_user",),
            required=False,
            included=True,
            budget_tokens=2_000,
            estimated_tokens=8,
            applied_tokens=8,
            item_count=1,
            reason="matched",
            content='<ReplyExamples><Example id="7" /></ReplyExamples>',
        ),
        ContextSection(
            key="response_protocol",
            ordinal=4,
            priority=90,
            source_type="protocol",
            source_refs=("protocol:v1",),
            targets=("temp_user", "system"),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=10,
            applied_tokens=20,
            item_count=1,
            reason="required_response_protocol",
            content="完整协议",
        ),
    )

    class Service:
        def __init__(self) -> None:
            self.recorded = False

        async def prepare_request(self, context, **kwargs):
            return PreparedRequest(
                protocol_prompt="完整协议",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms><Term /></KnownTerms>",
                matched_terms=(),
                sections=sections,
            )

        async def record_context_trace(self, context, applied_sections, **kwargs):
            assert request.prompt == "<Msg>hello</Msg>"
            assert request.system_prompt == "persona\n\n完整协议"
            assert [part.text for part in request.extra_user_content_parts] == [
                "<KnownTerms><Term /></KnownTerms>",
                '<MemoryContext><Memory id="4" /></MemoryContext>',
                '<ReplyExamples><Example id="7" /></ReplyExamples>',
                "完整协议",
            ]
            assert applied_sections == sections
            assert kwargs["request_snapshot_complete"] is True
            fields = kwargs["request_snapshot"]["fields"]
            assert fields["prompt"] == "<Msg>hello</Msg>"
            assert fields["system_prompt"] == "persona\n\n完整协议"
            self.recorded = True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {"protocol_injection_mode": "both"})
        plugin._container = SimpleNamespace(service=service)
        plugin._build_message_context = _async_build_context
        event = _FakeEvent()

        await plugin.on_llm_request(event, request)

        assert service.recorded
        assert not event.stopped
        assert event.get_extra("_humanize_state") == EventState.REQUESTED.value

    request = ProviderRequest(prompt="hello", system_prompt="persona")
    asyncio.run(scenario())


def test_request_rejects_multiple_prompt_context_sections() -> None:
    sections = (
        ContextSection(
            key="current_message",
            ordinal=0,
            priority=100,
            source_type="message",
            source_refs=("message:msg-1",),
            targets=("prompt",),
            required=True,
            included=True,
            budget_tokens=None,
            estimated_tokens=4,
            applied_tokens=4,
            item_count=1,
            reason="current_user_message",
            content="<Msg>hello</Msg>",
        ),
        ContextSection(
            key="known_terms",
            ordinal=1,
            priority=60,
            source_type="repository",
            source_refs=("jargon:1",),
            targets=("prompt",),
            required=False,
            included=True,
            budget_tokens=256,
            estimated_tokens=6,
            applied_tokens=6,
            item_count=1,
            reason="invalid_prompt_target",
            content="<KnownTerms><Term /></KnownTerms>",
        ),
    )

    class Service:
        recorded = False

        async def prepare_request(self, context, **kwargs):
            return PreparedRequest(
                protocol_prompt="完整协议",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms><Term /></KnownTerms>",
                matched_terms=(),
                sections=sections,
            )

        async def record_context_trace(self, context, applied_sections):
            self.recorded = True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        plugin._build_message_context = _async_build_context
        event = _FakeEvent()
        request = ProviderRequest(prompt="hello")

        await plugin.on_llm_request(event, request)

        assert event.stopped
        assert (
            event.get_extra("_humanize_protocol_error") == "context_application_failed"
        )
        assert not service.recorded

    asyncio.run(scenario())


def test_request_wraps_only_exact_user_segment_and_restores_full_prompt() -> None:
    section = ContextSection(
        key="current_message",
        ordinal=0,
        priority=100,
        source_type="message",
        source_refs=("message:msg-1",),
        targets=("prompt",),
        required=True,
        included=True,
        budget_tokens=None,
        estimated_tokens=4,
        applied_tokens=4,
        item_count=1,
        reason="current_user_message",
        content="<Msg>hello</Msg>",
    )

    class Service:
        def __init__(self) -> None:
            self.snapshots: list[dict] = []

        async def prepare_request(self, context, **kwargs):
            assert context.user_text == "hello"
            return PreparedRequest(
                protocol_prompt="protocol",
                message_xml="<Msg>hello</Msg>",
                known_terms_xml="<KnownTerms />",
                matched_terms=(),
                sections=(section,),
            )

        async def record_context_trace(self, context, sections, **kwargs):
            self.snapshots.append(kwargs["request_snapshot"])

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})

        class Window:
            async def load(self, context, *, token_budget: int):
                del context, token_budget
                return SimpleNamespace(
                    contexts=(),
                    entry_count=0,
                    estimated_tokens=0,
                )

        plugin._container = SimpleNamespace(service=service, context_window=Window())
        plugin._build_message_context = _async_build_context
        event = _FakeEvent()
        original = "[time: 12:00]\nhello\n[external plugin note]"
        request = ProviderRequest(
            prompt=original,
            system_prompt="external system",
            contexts=[{"role": "assistant", "content": "external history"}],
            extra_user_content_parts=[TextPart(text="external user injection")],
        )

        await plugin.on_llm_request(event, request)

        wrapped = "[time: 12:00]\n<Msg>hello</Msg>\n[external plugin note]"
        assert request.prompt == wrapped
        assert event.get_extra("_humanize_original_prompt") == original
        assert event.get_extra("_humanize_wrapped_prompt") == wrapped
        assert service.snapshots[0]["fields"]["prompt"] == wrapped
        assert service.snapshots[0]["fields"]["system_prompt"] == "external system"
        assert service.snapshots[0]["fields"]["contexts"] == []
        assert service.snapshots[0]["fields"]["extra_user_content_parts"] == [
            {"type": "text", "text": "external user injection"}
        ]
        run_context = SimpleNamespace(
            messages=[Message(role="user", content=[TextPart(text=wrapped)])]
        )
        assert plugin._restore_current_user_message(run_context, wrapped, original) == 0
        assert run_context.messages[0].content[0].text == original

        missing_event = _FakeEvent()
        missing_request = ProviderRequest(prompt="[external replacement only]")
        await plugin.on_llm_request(missing_event, missing_request)
        assert missing_request.prompt == "[external replacement only]"
        assert missing_event.stopped
        assert (
            missing_event.get_extra("_humanize_protocol_error")
            == "context_application_failed"
        )

        ambiguous_event = _FakeEvent()
        ambiguous_request = ProviderRequest(prompt="hello\n[quoted]\nhello")
        await plugin.on_llm_request(ambiguous_event, ambiguous_request)
        assert ambiguous_request.prompt == "hello\n[quoted]\nhello"
        assert ambiguous_event.stopped
        assert (
            ambiguous_event.get_extra("_humanize_protocol_error")
            == "context_application_failed"
        )

        old_wrapped_event = _FakeEvent()
        old_wrapped_request = ProviderRequest(
            prompt="<Msg>hello</Msg>\n[old plugin context]\nhello"
        )
        await plugin.on_llm_request(old_wrapped_event, old_wrapped_request)
        assert not old_wrapped_event.stopped
        assert old_wrapped_request.prompt == (
            "<Msg>hello</Msg>\n[old plugin context]\n<Msg>hello</Msg>"
        )

        already_wrapped_event = _FakeEvent()
        already_wrapped_request = ProviderRequest(
            prompt="[time: 12:00]\n<Msg>hello</Msg>\n[external plugin note]"
        )
        await plugin.on_llm_request(already_wrapped_event, already_wrapped_request)
        assert not already_wrapped_event.stopped
        assert already_wrapped_request.prompt == (
            "[time: 12:00]\n<Msg>hello</Msg>\n[external plugin note]"
        )

    asyncio.run(scenario())


def test_invalid_header_is_repaired_once_without_rewriting_body() -> None:
    body = "<Messages><Message>第一行</Message><Message>第二行</Message></Messages>"
    raw_output = f"Action: Reply\r\nUnknownTerms: []\r\n{body}"

    class Service:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.parser = ProtocolParser(PluginConfig())

        async def process_final_response(self, context, raw, **kwargs):
            self.calls.append(raw)
            try:
                decision = self.parser.parse(raw)
            except ProtocolValidationError as exc:
                return FinalOutcome(
                    valid=False, error_code=exc.code, error_detail=exc.detail
                )
            return FinalOutcome(
                valid=True,
                action=decision.action,
                messages=decision.messages,
                unknown_terms=decision.unknown_terms,
            )

    class Provider:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return LLMResponse(
                role="assistant",
                completion_text=(
                    "<Action>Reply</Action>\n<UnknownTerms>[]</UnknownTerms>"
                ),
            )

    async def scenario() -> None:
        service = Service()
        provider = Provider()
        plugin = HumanizePlugin(
            SimpleNamespace(get_using_provider=lambda umo: provider), {}
        )
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_history_sync_required", False)
        response = LLMResponse(role="assistant", completion_text=raw_output)

        await plugin.enforce_response_protocol(event, response)

        assert response.completion_text == ""
        assert not event.stopped
        await plugin.synchronize_agent_history(
            event, SimpleNamespace(messages=[]), response
        )

        assert not event.stopped
        assert response.completion_text == "第一行\n第二行"
        assert event.get_extra("_humanize_state") == EventState.FINAL_VALID.value
        assert len(provider.calls) == 1
        call = provider.calls[0]
        assert call["func_tool"] is None
        assert call["contexts"] == []
        assert call["tool_calls_result"] is None
        assert call["extra_user_content_parts"] == []
        assert call["request_max_retries"] == 1
        assert service.calls[0] == raw_output
        assert service.calls[1].endswith(body)

    asyncio.run(scenario())


def test_protocol_repair_provider_failure_is_fail_closed() -> None:
    class Service:
        def __init__(self) -> None:
            self.failures: list[dict] = []

        async def process_final_response(self, context, raw, **kwargs):
            return FinalOutcome(
                valid=False,
                error_code="invalid_control_header",
                error_detail="missing header",
            )

        async def record_protocol_failure(self, context, **kwargs):
            self.failures.append(kwargs)
            return True

    class Provider:
        async def text_chat(self, **kwargs):
            raise RuntimeError("provider unavailable")

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(
            SimpleNamespace(get_using_provider=lambda umo: Provider()), {}
        )
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        response = LLMResponse(
            role="assistant",
            completion_text="Action: Reply\nUnknownTerms: []\n普通正文",
        )

        await plugin.enforce_response_protocol(event, response)
        await plugin.synchronize_agent_history(
            event, SimpleNamespace(messages=[]), response
        )

        assert event.stopped
        assert response.completion_text == ""
        assert (
            event.get_extra("_humanize_protocol_error")
            == "protocol_repair_request_failed"
        )
        assert len(service.failures) == 1
        assert service.failures[0]["error_code"] == "protocol_repair_request_failed"
        assert (
            service.failures[0]["error_detail"]
            == "Header repair provider request failed"
        )
        assert (
            service.failures[0]["raw_output"]
            == "Action: Reply\nUnknownTerms: []\n普通正文"
        )
        assert service.failures[0]["model"] == ""
        assert service.failures[0]["stage"] == "final"
        assert service.failures[0]["response_snapshot_complete"] is True
        assert (
            service.failures[0]["response_snapshot"]["final_response"]["response"][
                "fields"
            ]["completion_text"]
            == "Action: Reply\nUnknownTerms: []\n普通正文"
        )
        assert service.failures[0]["duration_ms"] >= 0

    asyncio.run(scenario())


def test_protocol_repair_never_reverses_a_valid_no_reply_action() -> None:
    class Service:
        async def process_final_response(self, context, raw, **kwargs):
            return FinalOutcome(
                valid=False,
                error_code="no_reply_has_text",
                error_detail="No Reply requires an empty response body",
            )

    class Provider:
        calls = 0

        async def text_chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("conflicting Action must not be repaired")

    async def scenario() -> None:
        provider = Provider()
        plugin = HumanizePlugin(
            SimpleNamespace(get_using_provider=lambda umo: provider), {}
        )
        plugin._container = SimpleNamespace(service=Service())
        event = _FakeEvent()
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        response = LLMResponse(
            role="assistant",
            completion_text=(
                "<Action>No Reply</Action>\n"
                "<UnknownTerms>[]</UnknownTerms>\n"
                "<Messages><Message>不得发送</Message></Messages>"
            ),
        )

        await plugin.enforce_response_protocol(event, response)

        assert event.stopped
        assert provider.calls == 0
        assert response.completion_text == ""
        assert event.get_extra("_humanize_protocol_error") == "no_reply_has_text"

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

    decorators = star_handlers_registry.get_handlers_by_event_type(
        EventType.OnDecoratingResultEvent
    )
    decorator_priorities = {
        handler.handler_name: handler.extras_configs.get("priority", 0)
        for handler in decorators
        if "astrbot_plugin_humanize" in handler.handler_module_path
    }
    assert decorator_priorities["dispatch_response"] < 0
    assert (
        decorator_priorities["finalize_decoration"]
        < decorator_priorities["dispatch_response"]
    )


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
        raw_output = "<Action>No Reply</Action>\n<UnknownTerms>[]</UnknownTerms>"
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


def test_dispatch_respects_prior_rejection_and_preserves_modified_long_text() -> None:
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
        assert modified.sent == ["这是其他插件修改后的较长回复"]

    asyncio.run(scenario())


def test_third_party_runner_skips_local_history_sync() -> None:
    class Service:
        def __init__(self) -> None:
            self.successes: list[dict] = []

        async def process_final_response(self, context, raw_output, **kwargs):
            return FinalOutcome(
                valid=True,
                action=Action.REPLY,
                messages=("收到",),
            )

        async def record_protocol_success(self, context, **kwargs):
            del context
            self.successes.append(kwargs)
            return True

    async def scenario() -> None:
        service = Service()
        plugin = HumanizePlugin(SimpleNamespace(), {})
        plugin._container = SimpleNamespace(service=service)
        event = _FakeEvent(MessageEventResult(chain=[Plain("raw")]))
        event.set_extra("_humanize_state", EventState.REQUESTED.value)
        event.set_extra("_humanize_context", _context())
        event.set_extra("_humanize_message_xml", "<Msg>hello</Msg>")
        event.set_extra("_humanize_history_sync_required", False)
        await plugin.prepare_message_event(event)
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
        assert event.sent == []

        await event.send(MessageChain([Plain(response.completion_text)]))

        assert event.sent == ["收到"]
        assert event.get_extra("_humanize_final_response_dispatched") is True
        assert service.successes[0]["messages"] == ("收到",)

    asyncio.run(scenario())
