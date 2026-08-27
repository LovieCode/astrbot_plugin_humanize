"""Reproduction: image+text message with an LLM tool call is not saved to the
managed context window.

Simulates the exact AstrBot hook sequence for one turn:
on_llm_request -> on_tool_start -> on_tool_end -> on_llm_response(final)
-> on_agent_done(synchronize/finalize) -> on_decorating_result(dispatch)
and inspects what ContextWindowService would persist.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from astrbot_plugin_humanize.humanize.domain.models import (
    Action,
    EventState,
    FinalOutcome,
)
from astrbot_plugin_humanize.main import HumanizePlugin
from tests.test_adapter import _context, _FakeEvent

from astrbot.api.message_components import Plain
from astrbot.api.provider import LLMResponse
from astrbot.core.agent.message import ImageURLPart, Message, TextPart
from astrbot.core.message.message_event_result import MessageEventResult

RAW_OUTPUT = (
    "<Action>Reply</Action>\n<Messages><Message>这是图的说明</Message></Messages>"
)
WRAPPED_PROMPT = "<Msg>看看这张图</Msg>"


class RecordingWindow:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def append(self, context, **kwargs):
        self.calls.append({"context": context, **kwargs})
        return SimpleNamespace(context_ref="ctx-AAAAAAAA", duplicate=False)


class MemoryStub:
    def __init__(self) -> None:
        self.committed: list[dict] = []

    async def commit_context_turn(self, context, **kwargs):
        self.committed.append(kwargs)


class Service:
    async def process_final_response(self, context, raw_output, **kwargs):
        return FinalOutcome(
            valid=True,
            action=Action.REPLY,
            messages=("这是图的说明",),
        )

    async def record_protocol_success(self, context, **kwargs):
        return True


def _make_plugin():
    plugin = HumanizePlugin(SimpleNamespace(), {})
    window = RecordingWindow()
    memory = MemoryStub()
    plugin._container = SimpleNamespace(
        service=Service(),
        context_window=window,
        memory=memory,
    )
    return plugin, window, memory


def _run_messages(with_cached_tool_image: bool) -> list[Message]:
    """run_context.messages as AstrBot's tool-loop runner leaves them."""
    current_user = Message(
        role="user",
        content=[
            TextPart(text=WRAPPED_PROMPT),
            ImageURLPart(
                image_url=ImageURLPart.ImageURL(url="data:image/png;base64,AAAA")
            ),
        ],
    )
    tool_assistant = Message(
        role="assistant",
        content=[TextPart(text="我先看看这张图")],
        tool_calls=[
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "humanize_transcribe_image",
                    "arguments": '{"index": 1}',
                },
            }
        ],
    )
    tool_result = Message(role="tool", content="图片转述结果……", tool_call_id="call-1")
    final_assistant = Message(role="assistant", content=[TextPart(text=RAW_OUTPUT)])
    messages = [
        Message(role="system", content="persona"),
        current_user,
        tool_assistant,
        tool_result,
    ]
    if with_cached_tool_image:
        # AstrBot appends a user message holding images returned by tools
        # (tool_loop_agent_runner.py line ~1062) before the final response.
        messages.append(
            Message(
                role="user",
                content=[
                    TextPart(text="[Image from tool 'draw', path='/tmp/out.png']"),
                    ImageURLPart(
                        image_url=ImageURLPart.ImageURL(
                            url="data:image/png;base64,BBBB", id="/tmp/out.png"
                        )
                    ),
                ],
            )
        )
    messages.append(final_assistant)
    return messages


async def _drive_turn(plugin, event, run_context, response):
    await plugin.on_tool_start(event, SimpleNamespace(), {})
    await plugin.on_tool_end(event, SimpleNamespace(), {}, None)
    await plugin.enforce_response_protocol(event, response)
    await plugin.synchronize_agent_history(event, run_context, response)
    await plugin.finalize_agent_history(event, run_context, response)


def _base_extras(event, context, image_count=1):
    event.set_extra("_humanize_state", EventState.REQUESTED.value)
    event.set_extra("_humanize_context", context)
    event.set_extra("_humanize_message_xml", WRAPPED_PROMPT)
    event.set_extra("_humanize_context_window_active", True)
    event.set_extra("_humanize_context_window_image_count", image_count)
    event.set_extra("_humanize_context_window_token_budget", 6000)


def _persisted_user_view(window) -> tuple:
    """Rebuild what ContextWindowService._current_turn_messages would save."""
    from astrbot_plugin_humanize.humanize.context.window import ContextWindowService

    if not window.calls:
        return ()
    call = window.calls[0]
    service = object.__new__(ContextWindowService)
    normalized = service._current_turn_messages(
        call["context"],
        call["run_messages"],
        service._image_descriptions(call["image_cache"]),
        call["image_count"],
    )
    return tuple(normalized)


def test_image_text_tool_turn_reaches_window_append() -> None:
    async def scenario() -> None:
        plugin, window, memory = _make_plugin()
        event = _FakeEvent(MessageEventResult(chain=[Plain("这是图的说明")]))
        context = _context("看看这张图")
        _base_extras(event, context)
        event.set_extra("_humanize_image_cache", ())  # model echoed no ImageCache
        response = LLMResponse(role="assistant", completion_text=RAW_OUTPUT)

        await _drive_turn(
            plugin, event, SimpleNamespace(messages=_run_messages(False)), response
        )
        await plugin.dispatch_response(event)

        assert event.sent == ["这是图的说明"], "final reply must dispatch"
        assert window.calls, "context window append must be called"
        assert memory.committed, "memory turn commit must be called"
        saved = _persisted_user_view(window)
        user_messages = [m for m in saved if m["role"] == "user"]
        assert user_messages, "the user message must be saved into the window"
        assert "看看这张图" in user_messages[0]["content"]

    asyncio.run(scenario())


@pytest.mark.xfail(
    reason="bug: tool-cached-image user message makes _current_turn_messages "
    "drop the entire tool history (see docs/context-persistence-report.md)",
    strict=True,
)
def test_image_tool_turn_with_tool_cached_image_reaches_window_append() -> None:
    async def scenario() -> None:
        plugin, window, memory = _make_plugin()
        event = _FakeEvent(MessageEventResult(chain=[Plain("这是图的说明")]))
        context = _context("看看这张图")
        _base_extras(event, context)
        event.set_extra("_humanize_image_cache", ())
        response = LLMResponse(role="assistant", completion_text=RAW_OUTPUT)

        await _drive_turn(
            plugin, event, SimpleNamespace(messages=_run_messages(True)), response
        )
        await plugin.dispatch_response(event)

        assert window.calls, "context window append must be called"
        saved = _persisted_user_view(window)
        user_messages = [m for m in saved if m["role"] == "user"]
        assert user_messages, "the user message must be saved into the window"
        assert "看看这张图" in user_messages[0]["content"]
        # The tool history (assistant tool_calls + tool result) must survive too.
        roles = [m["role"] for m in saved]
        assert "tool" in roles, "tool result should remain in the saved turn"

    asyncio.run(scenario())
