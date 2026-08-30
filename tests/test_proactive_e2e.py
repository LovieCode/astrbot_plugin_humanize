"""End-to-end proactive flow with fake data and the real plugin code.

从一条未 @ 的群消息开始，用真实插件代码跑完整条链路：旁观记录进入群共享
窗口 → 窗口计时到期 → 注入的事件构造器产出合成事件并入队 → 依次执行入口
钩子、on_llm_request、协议校验、agent 收尾与结果分发 → 结果经事件携带的
回调回报给计时服务。AstrBot 核心舞台（唤醒判定、Provider 调用、Respond
发送）用等价的少量步骤模拟；其余全部是真实实现：真实 SQLite、真实管理
窗口、真实计时器。持久化通过 monkeypatch 重定向到 pytest 临时目录，初始
窗口压到下限 1 秒以缩短真实等待。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from astrbot_plugin_humanize.humanize.domain.models import MessageContext
from astrbot_plugin_humanize.main import (
    _PROACTIVE_KIND_KEY,
    HumanizePlugin,
)

from astrbot.api.event import MessageChain
from astrbot.api.message_components import At, Plain
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

SCOPE = "aiocqhttp:GroupMessage:100"
REPLY_RAW = (
    "<Action>Reply</Action>\n<Messages>\n<Message>好呀，我也要去</Message>\n</Messages>"
)
NO_REPLY_RAW = (
    "<Action>No Reply</Action>\n"
    "<Messages>\n<Message>在忙，先不插话</Message>\n</Messages>"
)
WAIT_RAW = "<Action>Wait 1</Action>"

_PLATFORM_META = PlatformMetadata(name="aiocqhttp", description="", id="aiocqhttp")


class _PlatformEvent(AstrMessageEvent):
    """aiocqhttp 形状的最小事件：send 可观察，其余全部走基类实现。"""

    def __init__(self, message_str, message_obj, platform_meta, session_id):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.sent_chains: list[MessageChain] = []

    async def send(self, message: MessageChain) -> None:
        self.sent_chains.append(message)
        self._has_send_oper = True


def _group_event(
    *,
    text: str,
    sender_id: str = "1001",
    sender_name: str = "小明",
    at_bot: bool = False,
    message_id: str = "m-1",
) -> _PlatformEvent:
    message_obj = AstrBotMessage()
    message_obj.type = MessageType.GROUP_MESSAGE
    message_obj.self_id = "bot-1"
    message_obj.session_id = "100"
    message_obj.message_id = message_id
    message_obj.group = SimpleNamespace(group_id="100")
    message_obj.sender = MessageMember(user_id=sender_id, nickname=sender_name)
    chain: list[Any] = [At(qq="bot-1")] if at_bot else []
    chain.append(Plain(text))
    message_obj.message = chain
    message_obj.message_str = text
    message_obj.timestamp = int(time.time())
    event = _PlatformEvent(text, message_obj, _PLATFORM_META, "100")
    # is_at_or_wake_command 在真实流水线由 WakingCheckStage 判定；这里等价模拟。
    event.is_wake = True
    event.is_at_or_wake_command = at_bot
    return event


class _StubContext:
    """宿主 Context 的最小替身：只实现流水线必需的接口，其余走异常兜底。"""

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self.persona_manager = SimpleNamespace(
            resolve_selected_persona=self._resolve_persona
        )

    async def _resolve_persona(self, **kwargs: Any) -> tuple[Any, ...]:
        return "default", {"name": "小助"}, None, False

    def register_web_api(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_event_queue(self) -> asyncio.Queue:
        return self._queue

    def get_config(self, umo: str | None = None) -> dict[str, Any]:
        return {}

    def get_using_provider(self, umo: str | None = None) -> None:
        return None

    def get_platform_inst(self, platform_id: str) -> None:
        return None


def _plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, queue: asyncio.Queue
) -> HumanizePlugin:
    from astrbot.core.utils import astrbot_path

    monkeypatch.setattr(
        astrbot_path,
        "get_astrbot_plugin_data_path",
        lambda: str(tmp_path),
    )
    plugin = HumanizePlugin(
        _StubContext(queue),
        {
            "proactive_mode": "whitelist",
            "proactive_whitelist": ["100"],
            "memory_enabled": False,
        },
    )
    # 提速：初始窗口压到下限 1 秒（线上默认 10 秒），其余计时语义不变。
    plugin._plugin_config = replace(
        plugin._plugin_config, proactive_window_initial_seconds=0
    )
    return plugin


async def _run_request(
    plugin: HumanizePlugin,
    event: Any,
    raw_output: str,
    *,
    prepare: bool = True,
) -> tuple[ProviderRequest, LLMResponse]:
    """入口钩子 → 请求装配 → 协议校验 → agent 收尾；分发留给调用方。"""
    if prepare:
        await plugin.prepare_message_event(event)
    req = ProviderRequest(prompt=event.message_str, contexts=[], system_prompt="")
    await plugin.on_llm_request(event, req)
    response = LLMResponse(role="assistant", completion_text=raw_output)
    await plugin.enforce_response_protocol(event, response)
    run_context = SimpleNamespace(messages=[])
    await plugin.synchronize_agent_history(event, run_context, response)
    await plugin.finalize_agent_history(event, run_context, response)
    return req, response


async def _dispatch(plugin: HumanizePlugin, event: Any, response: LLMResponse) -> None:
    """等价于 Respond 阶段：装配结果链并交给分发钩子。"""
    event.set_result(event.plain_result(response.completion_text))
    await plugin.dispatch_response(event)


def _injected_prompt(req: ProviderRequest) -> str:
    """全部注入内容：system_prompt + 临时用户段。"""
    parts = "\n".join(
        str(getattr(part, "text", "") or "") for part in req.extra_user_content_parts
    )
    return f"{req.system_prompt}\n{parts}"


def _probe() -> MessageContext:
    return MessageContext(
        request_id="probe",
        scope_type="group",
        scope_id=SCOPE,
        message_id="probe",
        sender_id="1001",
        sender_name="小明",
        user_text="@bot 在吗",
        chat_scene="QQ群",
        admin_name="",
        admin_ids=(),
        conversation_id=SCOPE,
        agent_id="default",
    )


async def _load_window(plugin: HumanizePlugin):
    return await plugin._container.context_window.load(_probe(), token_budget=6000)


def _rendered(items: list[dict[str, Any]]) -> str:
    return "\n".join(str(item.get("content") or "") for item in items)


def test_window_no_reply_cycle_stretches_and_keeps_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闲聊开窗 → 到点触发 → No Reply：不外发、窗口拉长、回合落账。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            # 群聊策略默认 mention（仅直接触发）；这些场景需要完整主动路径。
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="今天天气真好，有人一起出去玩吗", message_id="m-1")
            )

            synthetic = await asyncio.wait_for(queue.get(), timeout=5)
            assert synthetic.get_extra(_PROACTIVE_KIND_KEY) == "window"
            chain = synthetic.message_obj.message
            assert isinstance(chain[0], At) and str(chain[0].qq) == "bot-1"
            assert "没有 @ 你" in chain[1].text

            # 合成事件也要过入口钩子，但不得再记一条旁观。
            synthetic.is_at_or_wake_command = True
            req, response = await _run_request(plugin, synthetic, NO_REPLY_RAW)
            assert (await _load_window(plugin)).entry_count == 1

            # 请求上下文 = 群共享管理窗口（含小明的闲聊）；原生会话被剥离。
            assert req.conversation is None
            rendered = _rendered(list(req.contexts))
            assert "小明" in rendered and "今天天气真好" in rendered
            # Wait 补充规则跟随回复协议注入，事件消息文本里没有
            assert "补充规则（仅本场景）" in _injected_prompt(req)
            assert "Wait" not in synthetic.message_obj.message[1].text

            await _dispatch(plugin, synthetic, response)
            assert synthetic.sent_chains == []
            state = await plugin._container.repository.get_proactive_state(
                scope_id=SCOPE
            )
            assert state["window_seconds"] == 11  # 1 秒窗口 + 沉默 +10
            assert (await _load_window(plugin)).entry_count == 2
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_window_reply_cycle_sends_resets_and_writes_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """闲聊开窗 → 到点触发 → Reply：逐条出站、计时回 1 秒、回合写进窗口。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            # 群聊策略默认 mention（仅直接触发）；这些场景需要完整主动路径。
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="周末有人去爬山吗", message_id="m-1")
            )

            synthetic = await asyncio.wait_for(queue.get(), timeout=5)
            synthetic.is_at_or_wake_command = True
            _req, response = await _run_request(plugin, synthetic, REPLY_RAW)
            await _dispatch(plugin, synthetic, response)

            # 协议消息逐条经原始 send 出站，控制标签不外漏，结果链已清空。
            assert [c.get_plain_text() for c in synthetic.sent_chains] == [
                "好呀，我也要去"
            ]
            assert synthetic.get_result() is None

            state = await plugin._container.repository.get_proactive_state(
                scope_id=SCOPE
            )
            assert state["window_seconds"] == 1

            window = await _load_window(plugin)
            assert window.entry_count == 2
            rendered = _rendered(list(window.contexts))
            assert "好呀，我也要去" in rendered
            assert "系统提示" in rendered  # 主动回合的历史发言者是系统提示
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_window_wait_cycle_defers_then_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wait 1：静默、不写窗口、按等待秒数重触发；第二轮回复合流。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            # 群聊策略默认 mention（仅直接触发）；这些场景需要完整主动路径。
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="这局谁来指挥", message_id="m-1")
            )

            first = await asyncio.wait_for(queue.get(), timeout=5)
            first.is_at_or_wake_command = True
            _req, response = await _run_request(plugin, first, WAIT_RAW)
            await _dispatch(plugin, first, response)

            assert first.sent_chains == []
            proactive = plugin._container.proactive
            assert proactive._waits[SCOPE] == 1
            assert SCOPE in proactive._window_timers
            assert (await _load_window(plugin)).entry_count == 1

            second = await asyncio.wait_for(queue.get(), timeout=5)
            assert second.get_extra(_PROACTIVE_KIND_KEY) == "window"
            second.is_at_or_wake_command = True
            _req2, response2 = await _run_request(plugin, second, REPLY_RAW)
            await _dispatch(plugin, second, response2)

            assert [c.get_plain_text() for c in second.sent_chains] == [
                "好呀，我也要去"
            ]
            assert SCOPE not in proactive._waits
            state = await plugin._container.repository.get_proactive_state(
                scope_id=SCOPE
            )
            assert state["window_seconds"] == 1
            assert (await _load_window(plugin)).entry_count == 2
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_normal_at_reply_resets_pending_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """普通 @ 回复同样算参与：无论当前窗口多长，计时回到 1 秒。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            # 群聊策略默认 mention（仅直接触发）；这些场景需要完整主动路径。
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin._container.repository.update_proactive_state(
                scope_id=SCOPE, window_seconds=120
            )

            at_event = _group_event(text="bot 你怎么看", at_bot=True, message_id="m-2")
            _req, response = await _run_request(plugin, at_event, REPLY_RAW)
            await _dispatch(plugin, at_event, response)

            assert at_event.get_extra(_PROACTIVE_KIND_KEY, "") == ""
            # 普通 @ 回合的回复协议不携带 Wait 补充规则
            assert "补充规则（仅本场景）" not in _injected_prompt(_req)
            assert [c.get_plain_text() for c in at_event.sent_chains] == [
                "好呀，我也要去"
            ]
            state = await plugin._container.repository.get_proactive_state(
                scope_id=SCOPE
            )
            assert state["window_seconds"] == 1
            assert queue.empty()
        finally:
            await plugin.terminate()

    asyncio.run(scenario())
