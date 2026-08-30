"""Generate a human-readable state trace of the proactive full flow.

用法（父 venv 解释器）：
    .venv/Scripts/python.exe scripts/proactive_trace.py

复用 tests/test_proactive_e2e.py 的假数据马甲（假群事件、宿主 Context 替身、
注入式事件队列），用真实插件代码把四条链路各跑一遍，把每一步的状态变化
——窗口内容、注入的提示词、协议判定、状态机迁移、计时反馈——写成
插件根目录下的 proactive-e2e-trace.md 供人工审查。输出文件是本地审查
产物，不提交。
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = PLUGIN_ROOT.parent
for _path in (str(PLUGINS_ROOT), str(PLUGIN_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

OUTPUT_PATH = PLUGIN_ROOT / "proactive-e2e-trace.md"


def _load_harness() -> Any:
    spec = importlib.util.spec_from_file_location(
        "proactive_e2e_harness", PLUGIN_ROOT / "tests" / "test_proactive_e2e.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_harness()
SCOPE = _HARNESS.SCOPE
REPLY_RAW = _HARNESS.REPLY_RAW
NO_REPLY_RAW = _HARNESS.NO_REPLY_RAW
WAIT_RAW = _HARNESS.WAIT_RAW

from astrbot_plugin_humanize.main import (  # noqa: E402
    _CONTEXT_KEY,
    _CONTEXT_WINDOW_ACTIVE_KEY,
    _CONTEXT_WINDOW_PENDING_ACTION_KEY,
    _CONTEXT_WINDOW_PENDING_MESSAGES_KEY,
    _CONTEXT_WINDOW_TOKEN_BUDGET_KEY,
    _MESSAGES_KEY,
    _NO_REPLY_REASON_KEY,
    _PROACTIVE_KIND_KEY,
    _PROACTIVE_WAIT_KEY,
    _STATE_KEY,
)

from astrbot.api.provider import ProviderRequest  # noqa: E402


class Trace:
    """Markdown 轨迹收集器。"""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def raw(self, line: str = "") -> None:
        self._lines.append(line)

    def h2(self, text: str) -> None:
        self.raw()
        self.raw(f"## {text}")
        self.raw()

    def h3(self, text: str) -> None:
        self.raw(f"### {text}")
        self.raw()

    def bullets(self, *items: str) -> None:
        for item in items:
            self.raw(f"- {item}")
        self.raw()

    def block(self, body: str) -> None:
        self.raw("```text")
        self.raw(body if body.strip() else "（空）")
        self.raw("```")
        self.raw()

    def text(self) -> str:
        return "\n".join(self._lines).rstrip() + "\n"


def _patch_schedule_recorder(trace: Trace) -> None:
    """记录每一次计时器调度（类型与延迟），不改变任何行为。"""
    from astrbot_plugin_humanize.humanize.services.proactive import ProactiveService

    original = ProactiveService._schedule_window

    def traced(self: Any, scope_id: str, delay: float, *, kind: str) -> None:
        trace.bullets(f"【主动服务】调度计时器：kind={kind}，延迟 {delay:g} 秒")
        return original(self, scope_id, delay, kind=kind)

    ProactiveService._schedule_window = traced


def _state(event: Any) -> Any:
    return event.get_extra(_STATE_KEY)


async def _load(plugin: Any):
    return await plugin._container.context_window.load(
        _HARNESS._probe(), token_budget=6000
    )


async def _dump_window(trace: Trace, plugin: Any, label: str) -> None:
    window = await _load(plugin)
    trace.bullets(
        f"{label}：窗口回合数 {window.entry_count}"
        f"（渲染为 {len(window.contexts)} 条消息——一个回合含用户侧+助手侧）"
    )
    _dump_window_items(trace, window.contexts)


def _dump_window_items(trace: Trace, contexts: list[dict[str, Any]]) -> None:
    trace.raw(f"窗口当前内容（{len(contexts)} 条）：")
    for index, item in enumerate(contexts, 1):
        trace.raw(f"{index}. role={item.get('role')}")
        trace.block(str(item.get("content")))
    if not contexts:
        trace.raw()


def _dump_request(trace: Trace, plugin: Any, event: Any, req: Any) -> None:
    context = event.get_extra(_CONTEXT_KEY)
    trace.bullets(
        f"状态机：{_state(event)}",
        f"消息上下文：sender_name={context.sender_name}｜scope={context.scope_id}"
        f"｜conversation={context.conversation_id}｜agent={context.agent_id}"
        f"｜bot_name={context.bot_name}",
        f"req.conversation = {req.conversation!r}（None = 原生会话历史被剥离）",
        f"窗口激活：{event.get_extra(_CONTEXT_WINDOW_ACTIVE_KEY)}"
        f"｜token 预算：{event.get_extra(_CONTEXT_WINDOW_TOKEN_BUDGET_KEY)}",
    )
    for index, ref in enumerate(context.attachment_refs, 1):
        trace.bullets(
            f"附件引用 {index}：type={ref['type']}"
            f"｜hash={ref['content_hash'][:16]}…｜metadata={ref['metadata']}"
        )
    trace.raw("req.system_prompt（协议等段落追加后的全文）：")
    trace.block(req.system_prompt)
    parts = [getattr(part, "text", "") for part in req.extra_user_content_parts]
    trace.raw(f"req.extra_user_content_parts（临时注入段，共 {len(parts)} 段）：")
    for index, part in enumerate(parts, 1):
        trace.raw(f"第 {index} 段：")
        trace.block(part)
    if not parts:
        trace.raw()
    trace.raw("req.prompt（当前消息包装后）：")
    trace.block(req.prompt)
    trace.raw(f"req.contexts（受管历史，共 {len(req.contexts)} 条）：")
    for index, item in enumerate(req.contexts, 1):
        trace.raw(f"{index}. role={item.get('role')}")
        trace.block(str(item.get("content")))
    if not req.contexts:
        trace.raw()


async def _protocol_step(
    trace: Trace, plugin: Any, event: Any, raw_output: str, label: str
):
    from astrbot.api.provider import LLMResponse

    trace.bullets(f"模拟模型原始输出（{label}）：")
    trace.block(raw_output)
    response = LLMResponse(role="assistant", completion_text=raw_output)
    await plugin.enforce_response_protocol(event, response)
    lines = [f"协议校验后状态机：{_state(event)}"]
    messages = event.get_extra(_MESSAGES_KEY, ())
    lines.append(f"解析出的协议消息：{tuple(messages)}")
    reason = str(event.get_extra(_NO_REPLY_REASON_KEY, "") or "")
    if reason:
        lines.append(f"不回复原因：{reason}")
    wait_seconds = event.get_extra(_PROACTIVE_WAIT_KEY)
    if wait_seconds is not None:
        lines.append(f"等待秒数：{wait_seconds}")
    lines.append(f"响应文本改写为：{response.completion_text!r}")
    trace.bullets(*lines)
    return response


async def _agent_step(trace: Trace, plugin: Any, event: Any, response: Any) -> None:
    run_context = SimpleNamespace(messages=[])
    await plugin.synchronize_agent_history(event, run_context, response)
    await plugin.finalize_agent_history(event, run_context, response)
    pending_messages = event.get_extra(_CONTEXT_WINDOW_PENDING_MESSAGES_KEY, ())
    trace.bullets(
        f"agent 收尾后状态机：{_state(event)}",
        f"窗口待写回合：action={event.get_extra(_CONTEXT_WINDOW_PENDING_ACTION_KEY)!r}"
        f"｜待写消息数={len(tuple(pending_messages))}",
    )


async def _dispatch_step(trace: Trace, plugin: Any, event: Any, response: Any) -> None:
    before = await plugin._container.repository.get_proactive_state(scope_id=SCOPE)
    event.set_result(event.plain_result(response.completion_text))
    await plugin.dispatch_response(event)
    after = await plugin._container.repository.get_proactive_state(scope_id=SCOPE)
    sent = [chain.get_plain_text() for chain in event.sent_chains]
    waits = dict(plugin._container.proactive._waits)
    trace.bullets(
        f"分发后状态机：{_state(event)}",
        f"结果链已清空：{event.get_result() is None}",
        f"实际出站：{sent if sent else '（无——沉默/等待被发送闸门抑制）'}",
        f"计时反馈：window_seconds {before.get('window_seconds')!r} → "
        f"{after.get('window_seconds')!r}",
        f"等待计数：{waits if waits else '（无）'}",
        f"计时库状态：{after}",
    )


async def _new_plugin(tag: str):
    import pytest

    data_dir = Path(tempfile.mkdtemp(prefix=f"humanize-trace-{tag}-"))
    queue: asyncio.Queue = asyncio.Queue()
    plugin = _HARNESS._plugin(data_dir, pytest.MonkeyPatch(), queue)
    await plugin.initialize()
    # 与 harness 测试一致：这些场景需要完整主动路径（策略默认仅直接触发）。
    await plugin._container.repository.set_group_policy_mode(
        scope_id="global", mode="full"
    )
    return plugin, queue, data_dir


async def _scenario_no_reply(trace: Trace) -> None:
    trace.h2("场景一：群闲聊 → 窗口到期 → 模型沉默（No Reply）")
    plugin, queue, data_dir = await _new_plugin("no-reply")
    try:
        trace.h3("1. 小明在群里说话（未 @ 机器人）")
        chatter = _HARNESS._group_event(
            text="今天天气真好，有人一起出去玩吗", message_id="m-1"
        )
        await plugin.prepare_message_event(chatter)
        await _dump_window(trace, plugin, "入口钩子完成后")
        state = await plugin._container.repository.get_proactive_state(scope_id=SCOPE)
        trace.bullets(f"计时库状态：{state or '（空——首次开窗，用初始 1 秒）'}")

        trace.h3("2. 窗口到期，构造合成事件并入队")
        synthetic = await asyncio.wait_for(queue.get(), timeout=10)
        chain = synthetic.message_obj.message
        trace.bullets(
            f"消息链：[At(qq={chain[0].qq}), Plain(...)]——首段 At 保证核心唤醒",
            f"extras 主动标记：kind={synthetic.get_extra(_PROACTIVE_KIND_KEY)}"
            "｜结果回调已挂",
        )
        trace.raw("情况说明全文：")
        trace.block(chain[1].text)

        trace.h3("3. 合成事件过入口钩子（等价核心唤醒判定之后）")
        synthetic.is_at_or_wake_command = True
        await plugin.prepare_message_event(synthetic)
        await _dump_window(trace, plugin, "合成事件过闸后")
        trace.bullets("旁观记录没有重复追加：合成事件被视为 @ 唤醒回合。")

        trace.h3("4. on_llm_request：装配请求（模型将看到的一切）")
        req = ProviderRequest(
            prompt=synthetic.message_str, contexts=[], system_prompt=""
        )
        await plugin.on_llm_request(synthetic, req)
        _dump_request(trace, plugin, synthetic, req)

        trace.h3("5. 模型输出 → 协议校验")
        response = await _protocol_step(
            trace, plugin, synthetic, NO_REPLY_RAW, "选择沉默"
        )

        trace.h3("6. agent 收尾 → 结果分发 → 计时反馈")
        await _agent_step(trace, plugin, synthetic, response)
        await _dispatch_step(trace, plugin, synthetic, response)
        await _dump_window(trace, plugin, "回合落账后")
    finally:
        await plugin.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


async def _scenario_reply(trace: Trace) -> None:
    trace.h2("场景二：群闲聊 → 窗口到期 → 模型回复（Reply）")
    plugin, queue, data_dir = await _new_plugin("reply")
    try:
        trace.h3("1. 小明在群里说话（未 @ 机器人）")
        chatter = _HARNESS._group_event(text="周末有人去爬山吗", message_id="m-1")
        await plugin.prepare_message_event(chatter)
        await _dump_window(trace, plugin, "入口钩子完成后")

        trace.h3("2. 窗口到期 → 合成事件（情况说明全文）")
        synthetic = await asyncio.wait_for(queue.get(), timeout=10)
        trace.block(synthetic.message_obj.message[1].text)
        synthetic.is_at_or_wake_command = True

        trace.h3("3. on_llm_request：装配请求")
        req = ProviderRequest(
            prompt=synthetic.message_str, contexts=[], system_prompt=""
        )
        await plugin.on_llm_request(synthetic, req)
        _dump_request(trace, plugin, synthetic, req)

        trace.h3("4. 模型输出 → 协议校验")
        response = await _protocol_step(trace, plugin, synthetic, REPLY_RAW, "选择回复")

        trace.h3("5. agent 收尾 → 结果分发 → 计时反馈")
        await _agent_step(trace, plugin, synthetic, response)
        await _dispatch_step(trace, plugin, synthetic, response)
        await _dump_window(trace, plugin, "回合落账后（下一轮对话将看到机器人回复）")
    finally:
        await plugin.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


async def _scenario_wait(trace: Trace) -> None:
    trace.h2("场景三：群闲聊 → 窗口到期 → 等待（Wait 1）→ 重触发 → 回复")
    plugin, queue, data_dir = await _new_plugin("wait")
    try:
        trace.h3("1. 小明在群里说话（未 @ 机器人）")
        chatter = _HARNESS._group_event(text="这局谁来指挥", message_id="m-1")
        await plugin.prepare_message_event(chatter)
        await _dump_window(trace, plugin, "入口钩子完成后")

        trace.h3("2. 窗口到期 → 合成事件 → 装配请求")
        first = await asyncio.wait_for(queue.get(), timeout=10)
        first.is_at_or_wake_command = True
        req = ProviderRequest(prompt=first.message_str, contexts=[], system_prompt="")
        await plugin.on_llm_request(first, req)
        _dump_request(trace, plugin, first, req)

        trace.h3("3. 模型输出 Wait 1 → 协议校验")
        response = await _protocol_step(trace, plugin, first, WAIT_RAW, "暂不决定")

        trace.h3("4. agent 收尾 → 结果分发（等待回合不写窗口）→ 计时反馈")
        await _agent_step(trace, plugin, first, response)
        await _dispatch_step(trace, plugin, first, response)
        await _dump_window(trace, plugin, "等待回合后")

        trace.h3("5. 等待到期重触发（同一批消息再呈现一次）→ 这次回复")
        second = await asyncio.wait_for(queue.get(), timeout=10)
        second.is_at_or_wake_command = True
        req2 = ProviderRequest(prompt=second.message_str, contexts=[], system_prompt="")
        await plugin.on_llm_request(second, req2)
        response2 = await _protocol_step(trace, plugin, second, REPLY_RAW, "决定回复")
        await _agent_step(trace, plugin, second, response2)
        await _dispatch_step(trace, plugin, second, response2)
        await _dump_window(trace, plugin, "回合落账后")
    finally:
        await plugin.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


async def _scenario_at_reply(trace: Trace) -> None:
    trace.h2("场景四：普通 @ 回复（非主动回合）同样重置计时")
    plugin, _queue, data_dir = await _new_plugin("at-reply")
    try:
        trace.h3("1. 预置计时状态（模拟之前沉默累计到 120 秒）")
        await plugin._container.repository.update_proactive_state(
            scope_id=SCOPE, window_seconds=120
        )
        state = await plugin._container.repository.get_proactive_state(scope_id=SCOPE)
        trace.bullets(f"计时库状态：{state}")

        trace.h3("2. 小明 @ 机器人提问，完整 @ 回复链路")
        at_event = _HARNESS._group_event(
            text="bot 你怎么看", at_bot=True, message_id="m-2"
        )
        await plugin.prepare_message_event(at_event)
        req = ProviderRequest(
            prompt=at_event.message_str, contexts=[], system_prompt=""
        )
        await plugin.on_llm_request(at_event, req)
        _dump_request(trace, plugin, at_event, req)
        response = await _protocol_step(trace, plugin, at_event, REPLY_RAW, "回复")
        await _agent_step(trace, plugin, at_event, response)
        await _dispatch_step(trace, plugin, at_event, response)
        await _dump_window(trace, plugin, "回合落账后")
    finally:
        await plugin.terminate()
        shutil.rmtree(data_dir, ignore_errors=True)


async def _main() -> None:
    trace = Trace()
    trace.raw("# Humanize 主动参与：假数据全流程状态轨迹")
    trace.raw()
    trace.raw(f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}")
    trace.raw(
        "- 数据：全部假数据；除核心舞台（唤醒判定/Provider 调用/发送）用等价步骤模拟外，其余为真实插件代码与真实 SQLite/窗口存储。"
    )
    trace.raw(
        "- 计时：初始窗口压到下限 1 秒（线上默认 10 秒）；No Reply +10 秒、回复回 1 秒、Wait N 重触发，语义与线上一致。"
    )
    trace.raw("- 许可：白名单模式，仅群 100。")

    _patch_schedule_recorder(trace)
    await _scenario_no_reply(trace)
    await _scenario_reply(trace)
    await _scenario_wait(trace)
    await _scenario_at_reply(trace)

    OUTPUT_PATH.write_text(trace.text(), encoding="utf-8")
    print(f"trace written: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(_main())
