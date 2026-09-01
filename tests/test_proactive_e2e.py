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
# 回归测试里主动回合直接复用已校验的 No Reply 输出，绕开协议重放。
_LLM_NO_REPLY_RESPONSE = LLMResponse(role="assistant", completion_text=NO_REPLY_RAW)

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


class _StubConversationManager:
    """等价 conversation_manager：一个固定 UUID 的当前会话，人格随会话走。"""

    CONVERSATION_ID = "5f0c2a64-7b1e-4d3e-9a2f-6c8d0e4b1a77"
    PERSONA_ID = "luowei"

    async def get_curr_conversation_id(self, unified_msg_origin: str) -> str:
        del unified_msg_origin
        return self.CONVERSATION_ID

    async def get_conversation(
        self, unified_msg_origin: str, conversation_id: str
    ) -> Any:
        del unified_msg_origin
        assert conversation_id == self.CONVERSATION_ID
        return SimpleNamespace(persona_id=self.PERSONA_ID)


class _StubContext:
    """宿主 Context 的最小替身：只实现流水线必需的接口，其余走异常兜底。

    与线上一致的关键点：人格解析返回一个非 default 人格（真实部署里
    每个会话都绑定了人格），会话管理器给出 UUID 会话 ID。身份解析若
    在旁观/回合两条路径上出现漂移，两条路径会落进不同的窗口目录，
    所有窗口断言立刻失败——这正是 2026-08-30 主动回合丢历史的 bug。
    """

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self.persona_manager = SimpleNamespace(
            resolve_selected_persona=self._resolve_persona
        )
        self.conversation_manager = _StubConversationManager()

    async def _resolve_persona(self, **kwargs: Any) -> tuple[Any, ...]:
        # (persona_id, persona_obj, begin_dialogs, use_webchat_default)
        del kwargs
        return (
            _StubConversationManager.PERSONA_ID,
            {"name": "洛薇"},
            None,
            False,
        )

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
    # 提速：初始窗口压到下限（线上默认 10 秒），受 2 秒保底下限托底，
    # 回复后静默期关闭（线上默认 20 秒）；其余计时语义不变。
    plugin._plugin_config = replace(
        plugin._plugin_config,
        proactive_window_initial_seconds=0,
        proactive_post_reply_cooldown_seconds=0,
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
    # 真实核心在 agent 阶段总会挂上当前会话（astr_main_agent._get_session_conv，
    # cid 为 UUID、persona_id 随会话）；缺了它 on_llm_request 会退回 umo，
    # 与旁观路径的 UUID 会话漂移——线上不存在这种形状。
    req = ProviderRequest(
        prompt=event.message_str,
        contexts=[],
        system_prompt="",
        conversation=SimpleNamespace(
            cid=_StubConversationManager.CONVERSATION_ID,
            persona_id=_StubConversationManager.PERSONA_ID,
        ),
    )
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
    """窗口探针：身份与真实回合解析结果一致（人格 agent + UUID 会话）。"""
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
        conversation_id=_StubConversationManager.CONVERSATION_ID,
        agent_id=_StubConversationManager.PERSONA_ID,
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
            # <Msg> 占位：窗口检查的行动指引（用户定稿）；情况说明走协议段
            assert "群里正在聊天" in chain[1].text
            assert "没有 @ 你" not in chain[1].text

            # 合成事件也要过入口钩子，但不得再记一条旁观。
            synthetic.is_at_or_wake_command = True
            req, response = await _run_request(plugin, synthetic, NO_REPLY_RAW)
            assert (await _load_window(plugin)).entry_count == 1

            # 请求上下文 = 群共享管理窗口（含小明的闲聊）；原生会话被剥离。
            assert req.conversation is None
            rendered = _rendered(list(req.contexts))
            assert "小明" in rendered and "今天天气真好" in rendered
            injected = _injected_prompt(req)
            # 情况说明与 Wait 补充规则都在回复协议里，不在事件消息文本
            assert "没有 @ 你" in injected
            assert "话没说完" in injected
            # 通告走独立的 <SystemNotice> 标签：<Msg> 保留给真实用户消息
            assert "<SystemNotice>（群里正在聊天" in req.prompt
            assert "<Msg>" not in req.prompt
            assert "有群成员提到了你" not in req.prompt

            await _dispatch(plugin, synthetic, response)
            assert synthetic.sent_chains == []
            state = await plugin._container.repository.get_proactive_state(
                scope_id=SCOPE
            )
            assert (
                state["window_seconds"] == 3
            )  # 初始压到保底 2 秒 + 沉默 +1（阶梯首步）
            # 主动检查沉默收场不落账：窗口里只有小明的旁观条目，
            # 不会出现"系统提示"伪装的用户消息。
            assert (await _load_window(plugin)).entry_count == 1
            rendered = _rendered(list((await _load_window(plugin)).contexts))
            assert "系统提示" not in rendered
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
            assert state["window_seconds"] == 2  # 回复后回到 2 秒保底窗口

            window = await _load_window(plugin)
            assert window.entry_count == 2
            rendered = _rendered(list(window.contexts))
            assert "好呀，我也要去" in rendered
            # 主动回合不插用户侧占位（无系统提示伪装），但保留 run 里的
            # 工具序列与 Bot 发言（assistant_only 语义）。
            assert "洛薇 · " in rendered
            assert "系统提示" not in rendered
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

            # 等待期间群里来了新发言：再检查时窗口有新内容，评估继续。
            await plugin.prepare_message_event(
                _group_event(text="我先说我的", message_id="m-2")
            )

            second = await asyncio.wait_for(queue.get(), timeout=5)
            assert second.get_extra(_PROACTIVE_KIND_KEY) == "wait"
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
            assert state["window_seconds"] == 2  # 回复后回到 2 秒保底窗口
            # 开窗闲聊 + 等待期间新发言 + Bot 回复，共三条
            assert (await _load_window(plugin)).entry_count == 3
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_normal_turn_wait_schedules_window_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常驻 Wait：普通 @ 回合模型等待 → 静默、不落窗口、到点补一次检查。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            event = _group_event(text="等等，我话没说完", at_bot=True, message_id="m-1")
            req, response = await _run_request(plugin, event, WAIT_RAW)
            # Wait 规则随协议注入普通群聊回合
            assert "话没说完" in _injected_prompt(req)
            await _dispatch(plugin, event, response)

            assert event.sent_chains == []
            proactive = plugin._container.proactive
            assert proactive._waits[SCOPE] == 1
            assert SCOPE in proactive._window_timers
            # 等待不落窗口：回合里没有可记录的发言
            assert (await _load_window(plugin)).entry_count == 0

            # 等待期间群里来了新发言：再检查带着新内容继续。
            await plugin.prepare_message_event(
                _group_event(text="好了我说完了", message_id="m-2")
            )

            second = await asyncio.wait_for(queue.get(), timeout=5)
            assert second.get_extra(_PROACTIVE_KIND_KEY) == "wait"
            second.is_at_or_wake_command = True
            _req2, response2 = await _run_request(plugin, second, REPLY_RAW)
            await _dispatch(plugin, second, response2)

            assert [c.get_plain_text() for c in second.sent_chains] == [
                "好呀，我也要去"
            ]
            assert SCOPE not in proactive._waits
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_normal_turn_waits_capped_per_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常驻 Wait 限次：普通回合与补查共用计数，一批最多等 3 次后不再补查。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            # 第 1 次：普通 @ 回合等待。
            event = _group_event(text="先别急", at_bot=True, message_id="m-1")
            _req, response = await _run_request(plugin, event, WAIT_RAW)
            await _dispatch(plugin, event, response)

            # 第 2、3 次：两次补查都继续等待，第 3 次触顶。每次补查前群里
            # 都有新发言（窗口内容变了，再检查才会继续评估）。
            for index in range(2):
                await plugin.prepare_message_event(
                    _group_event(
                        text=f"又补了一句 {index}", message_id=f"m-wait-{index}"
                    )
                )
                recheck = await asyncio.wait_for(queue.get(), timeout=5)
                assert recheck.get_extra(_PROACTIVE_KIND_KEY) == "wait"
                recheck.is_at_or_wake_command = True
                _req2, response2 = await _run_request(plugin, recheck, WAIT_RAW)
                await _dispatch(plugin, recheck, response2)

            proactive = plugin._container.proactive
            assert SCOPE not in proactive._waits
            assert SCOPE not in proactive._window_timers
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(queue.get(), timeout=0.5)
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_speak_expectation_injected_only_on_window_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """期望发言概率：窗口回合注入 <Rule>，普通 @ 回合不注入。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin._container.repository.set_group_speak_probability(
                scope_id="global", probability=40
            )

            # 普通 @ 回合：有人点名要回应，不注入概率期望。
            event = _group_event(text="在吗", at_bot=True, message_id="m-1")
            req, _response = await _run_request(plugin, event, REPLY_RAW)
            assert "主动发言的概率" not in _injected_prompt(req)

            # 闲聊窗口回合：期望行落在 <Rule> 内部。
            await plugin.prepare_message_event(
                _group_event(text="今天天气真好", message_id="m-2")
            )
            window = await asyncio.wait_for(queue.get(), timeout=5)
            window.is_at_or_wake_command = True
            req2, _response2 = await _run_request(plugin, window, REPLY_RAW)
            prompt2 = _injected_prompt(req2)
            assert "主动发言的概率约为 40%" in prompt2
            assert prompt2.index("主动发言的概率约为 40%") < prompt2.index("<Rule/>")
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
            assert state["window_seconds"] == 2  # 回复后回到 2 秒保底窗口
            assert queue.empty()
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_stale_proactive_dropped_after_bot_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """排队期间 Bot 回复过 → 到点的主动评估直接丢弃，不调模型不落账。

    对应线上「触发评估 + @ 回复并发 → 两条上下文一致的回复」的缺陷：
    合成事件带着触发时刻的回复序号，评估真正开始时序号已前进（期间有
    @ 回复），说明上下文已经包含那条回复，再评估只会重复发言。
    """

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="这局谁来指挥", message_id="m-1")
            )
            synthetic = await asyncio.wait_for(queue.get(), timeout=5)

            # 评估排到队列里之后、真正开始之前，群里有人 @ Bot 并得到回复。
            at_event = _group_event(text="我来指挥", at_bot=True, message_id="m-2")
            _req, response = await _run_request(plugin, at_event, REPLY_RAW)
            await _dispatch(plugin, at_event, response)
            assert [c.get_plain_text() for c in at_event.sent_chains] == [
                "好呀，我也要去"
            ]

            # 现在轮到那条排队的主动评估：序号已过期 → 丢弃。
            outcomes: list[Any] = []

            async def _outcome(*args: Any, **kwargs: Any) -> None:
                outcomes.append((args, kwargs))

            synthetic.set_extra("_humanize_proactive_outcome_callback", _outcome)
            req2 = ProviderRequest(
                prompt=synthetic.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.prepare_message_event(synthetic)
            await plugin.on_llm_request(synthetic, req2)

            assert synthetic.is_stopped()
            assert req2.contexts == []
            assert req2.system_prompt == ""
            assert outcomes == []
            assert queue.empty()
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_wake_pending_defers_proactive_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有真实 @ 回合已进入管线但还没到自己的 LLM 阶段 → 主动评估先让路。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="这局谁来指挥", message_id="m-1")
            )
            synthetic = await asyncio.wait_for(queue.get(), timeout=5)

            # 唤醒回合即将排队等锁（置让路标记），还没轮到 on_llm_request。
            wake = _group_event(text="bot 指挥是我", at_bot=True, message_id="m-2")
            await plugin.on_waiting_llm_request(wake)

            synthetic.set_extra("_humanize_proactive_outcome_callback", None)
            req = ProviderRequest(
                prompt=synthetic.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.prepare_message_event(synthetic)
            await plugin.on_llm_request(synthetic, req)
            assert synthetic.is_stopped()
            assert req.contexts == []

            # 唤醒回合走到自己的 LLM 阶段：让路标记清除。
            wake_req = ProviderRequest(
                prompt=wake.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.on_llm_request(wake, wake_req)
            assert not wake.is_stopped()
            assert plugin._stale_proactive_reason(synthetic, SCOPE) is None
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_foreign_event_does_not_clear_wake_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """命令等非模型回合既不置让路标记，也不得抹掉在途 @ 的标记。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            await plugin.prepare_message_event(
                _group_event(text="这局谁来指挥", message_id="m-1")
            )
            synthetic = await asyncio.wait_for(queue.get(), timeout=5)

            # 命令是唤醒事件，但不调用模型：prepare 不再置让路标记。
            command = _group_event(text="/help", at_bot=True, message_id="m-cmd")
            await plugin.prepare_message_event(command)
            assert SCOPE not in plugin._wake_started_at

            # 真实 @ 即将排队等锁：置标记。
            wake = _group_event(text="bot 指挥是我", at_bot=True, message_id="m-2")
            await plugin.on_waiting_llm_request(wake)
            assert SCOPE in plugin._wake_started_at

            # 其他事件（含命令收尾）不得误清。
            await plugin.finalize_decoration(command)
            assert SCOPE in plugin._wake_started_at

            req = ProviderRequest(
                prompt=synthetic.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.on_llm_request(synthetic, req)
            assert synthetic.is_stopped()
            assert plugin._stale_proactive_reason(synthetic, SCOPE) == (
                "wake_turn_pending"
            )
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_wait_recheck_dropped_when_window_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wait 到点后历史毫无变化 → 再检查被丢弃，不再对着同样内容评估。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            event = _group_event(text="等等，我话没说完", at_bot=True, message_id="m-1")
            _req, response = await _run_request(plugin, event, WAIT_RAW)
            await _dispatch(plugin, event, response)
            assert dict(plugin._container.proactive._waits) == {SCOPE: 1}

            recheck = await asyncio.wait_for(queue.get(), timeout=5)
            assert recheck.get_extra(_PROACTIVE_KIND_KEY) == "wait"
            recheck.is_at_or_wake_command = True

            outcomes: list[Any] = []

            async def _outcome(*args: Any, **kwargs: Any) -> None:
                outcomes.append((args, kwargs))

            recheck.set_extra("_humanize_proactive_outcome_callback", _outcome)
            req2 = ProviderRequest(
                prompt=recheck.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.prepare_message_event(recheck)
            await plugin.on_llm_request(recheck, req2)

            assert recheck.is_stopped()
            assert req2.system_prompt == ""
            assert outcomes == []
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_wait_recheck_dropped_when_window_shrinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """压缩把条目数缩小也按过期处理：没有新发言，不值得再评估。"""

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )
            event = _group_event(text="等等，我话没说完", at_bot=True, message_id="m-1")
            _req, response = await _run_request(plugin, event, WAIT_RAW)
            await _dispatch(plugin, event, response)

            recheck = await asyncio.wait_for(queue.get(), timeout=5)
            assert recheck.get_extra(_PROACTIVE_KIND_KEY) == "wait"
            recheck.set_extra("_humanize_proactive_armed_window_entries", 5)

            original_load = plugin._container.context_window.load

            async def _shrunk_load(*args: Any, **kwargs: Any):
                loaded = await original_load(*args, **kwargs)
                return loaded.__class__(
                    available=loaded.available,
                    contexts=loaded.contexts,
                    entry_count=3,
                    estimated_tokens=loaded.estimated_tokens,
                    compacted=True,
                )

            plugin._container.context_window.load = _shrunk_load
            req2 = ProviderRequest(
                prompt=recheck.message_str,
                contexts=[],
                system_prompt="",
                conversation=SimpleNamespace(
                    cid=_StubConversationManager.CONVERSATION_ID,
                    persona_id=_StubConversationManager.PERSONA_ID,
                ),
            )
            await plugin.prepare_message_event(recheck)
            await plugin.on_llm_request(recheck, req2)
            assert recheck.is_stopped()
            assert req2.system_prompt == ""
        finally:
            await plugin.terminate()

    asyncio.run(scenario())


def test_chatter_and_turns_share_one_window_directory_under_persona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨路径不变量：非默认人格 + UUID 会话下，旁观与各回合落同一窗口目录。

    2026-08-30 线上事故的回归钉：旁观记录曾用默认身份（agent_id=default、
    conversation_id=umo），回复/主动回合解析人格后用（agent-人格、UUID
    会话），两套身份哈希进不同目录，主动回合的窗口里永远没有群聊历史。
    本测试在贴近线上的 stub（人格 luowei + 固定 UUID 会话）下验证：
    闲聊旁观 → @ 回合 → 主动窗口回合三者读写的都是同一个 context_window。
    """

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        plugin = _plugin(tmp_path, monkeypatch, queue)
        try:
            await plugin.initialize()
            await plugin._container.repository.set_group_policy_mode(
                scope_id="global", mode="full"
            )

            # 1) 未 @ 闲聊：旁观落账；此时窗口里只有这一条。
            await plugin.prepare_message_event(
                _group_event(text="晚上有人吃火锅吗", message_id="m-1")
            )
            window = await _load_window(plugin)
            assert window.entry_count == 1
            assert "晚上有人吃火锅吗" in _rendered(list(window.contexts))

            # 2) 普通 @ 回合：同一窗口追加回合账目。
            at_event = _group_event(text="我也想吃火锅", at_bot=True, message_id="m-2")
            _req, response = await _run_request(plugin, at_event, REPLY_RAW)
            # @ 回合的请求上下文必须能看到刚才的旁观条目
            rendered_request = _rendered(list(_req.contexts))
            assert "晚上有人吃火锅吗" in rendered_request
            await _dispatch(plugin, at_event, response)
            assert (await _load_window(plugin)).entry_count == 2

            # 3) 群里继续闲聊开窗 → 主动窗口回合：窗口仍能看到全部历史。
            await plugin.prepare_message_event(
                _group_event(text="火锅店选好了吗", message_id="m-3")
            )
            synthetic = await asyncio.wait_for(queue.get(), timeout=5)
            synthetic.is_at_or_wake_command = True
            req2, _response2 = await _run_request(plugin, synthetic, NO_REPLY_RAW)
            rendered_proactive = _rendered(list(req2.contexts))
            # 失败模式（回归 6906d97 之前）：这里只剩主动回合自己，看不到
            # 闲聊旁观与 @ 回合。
            assert "晚上有人吃火锅吗" in rendered_proactive
            assert "我也想吃火锅" in rendered_proactive
            assert "火锅店选好了吗" in rendered_proactive
            await _dispatch(plugin, synthetic, _LLM_NO_REPLY_RESPONSE)
            # 沉默的主动检查不落账：仍是旁观 + @ 回合 + Bot 主动发言三条。
            assert (await _load_window(plugin)).entry_count == 3

            # 磁盘事实：会话目录落在人格 agent 目录下，default 目录不存在。
            sessions_root = (
                tmp_path
                / "astrbot_plugin_humanize"
                / "openviking"
                / "sessions"
                / _StubConversationManager.PERSONA_ID
            )
            assert (sessions_root / "group").is_dir()
            default_root = (
                tmp_path
                / "astrbot_plugin_humanize"
                / "openviking"
                / "sessions"
                / "default"
            )
            assert not default_root.exists()
        finally:
            await plugin.terminate()

    asyncio.run(scenario())
