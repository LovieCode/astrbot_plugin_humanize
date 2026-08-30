"""Behavior tests for the proactive group-participation service.

The service is exercised through fakes for the provider, sender, persona,
repository, and ambient window so each test pins one decision rule without
real model calls or timer waits.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import (
    Action,
    ContextSection,
    FinalOutcome,
    MessageContext,
    PreparedRequest,
)
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.services.proactive import ProactiveService

SCOPE = "aiocqhttp:GroupMessage:100"


def _config(**overrides: Any) -> PluginConfig:
    values: dict[str, Any] = {
        "proactive_mode": "whitelist",
        "proactive_whitelist": ("100",),
        "message_interval_seconds": 0,
    }
    values.update(overrides)
    return PluginConfig(**values)


def _section(key: str, ordinal: int, targets: tuple[str, ...]) -> ContextSection:
    return ContextSection(
        key=key,
        ordinal=ordinal,
        priority=90,
        source_type="test",
        source_refs=(),
        targets=targets,
        required=True,
        included=True,
        budget_tokens=None,
        estimated_tokens=1,
        applied_tokens=1,
        item_count=1,
        reason="test",
        content=f"内容-{key}",
    )


class _FakeRepository:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, Any]] = {}

    async def get_proactive_state(self, *, scope_id: str) -> dict[str, Any]:
        return dict(self.states.get(scope_id, {}))

    async def update_proactive_state(
        self,
        *,
        scope_id: str,
        window_seconds: int | None = None,
        last_reply_at: str | None = None,
        last_reply_text: str | None = None,
        last_eval_at: str | None = None,
    ) -> None:
        state = self.states.setdefault(scope_id, {})
        if window_seconds is not None:
            state["window_seconds"] = window_seconds
        if last_reply_at is not None:
            state["last_reply_at"] = last_reply_at
        if last_reply_text is not None:
            state["last_reply_text"] = last_reply_text
        if last_eval_at is not None:
            state["last_eval_at"] = last_eval_at

    async def reset_proactive_state(self, *, scope_id: str) -> None:
        self.states.pop(scope_id, None)


class _FakeWindow:
    def __init__(self) -> None:
        self.lines: dict[str, list[str]] = {}
        self.drops: list[str] = []
        self.loads: list[tuple[str, str, int]] = []
        self.history = [{"role": "system", "content": "历史轮次"}]

    async def read_ambient_lines(
        self, context: MessageContext, *, max_chars: int = 3_000
    ) -> tuple[str, ...]:
        return tuple(self.lines.get(context.scope_id, ()))

    async def drop_ambient(self, context: MessageContext) -> None:
        self.drops.append(context.scope_id)
        self.lines.pop(context.scope_id, None)

    async def load(self, context: MessageContext, *, token_budget: int) -> Any:
        self.loads.append((context.scope_id, context.agent_id, token_budget))
        return SimpleNamespace(contexts=list(self.history))


class _FakeService:
    def __init__(self, outcomes: list[FinalOutcome]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.prepared = PreparedRequest(
            protocol_prompt="协议",
            message_xml="<Msg />",
            known_terms_xml="<KnownTerms />",
            matched_terms=(),
            sections=(
                _section("current_message", 0, ("prompt",)),
                _section("known_terms", 1, ("temp_user",)),
                _section("response_protocol", 4, ("temp_user",)),
            ),
        )

    async def prepare_request(
        self, context: MessageContext, *, include_session_fallback: bool = True
    ) -> PreparedRequest:
        return self.prepared

    async def process_final_response(
        self, context: MessageContext, raw_output: str, **kwargs: Any
    ) -> FinalOutcome:
        self.calls.append({"raw": raw_output, **kwargs})
        return self.outcomes.pop(0)


class _FakeProvider:
    def __init__(self, completion: str = "") -> None:
        self.completion = completion
        self.calls: list[dict[str, Any]] = []

    async def text_chat(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=self.completion, model="test-model")


def _outcome(
    action: Action,
    *,
    wait_seconds: int = 0,
    valid: bool = True,
) -> FinalOutcome:
    return FinalOutcome(
        valid=valid,
        action=action,
        messages=("第一条", "第二条") if action is Action.REPLY else (),
        wait_seconds=wait_seconds,
    )


def _service(
    *,
    config: PluginConfig | None = None,
    provider: _FakeProvider | None = None,
    outcomes: list[FinalOutcome] | None = None,
    lines: list[str] | None = None,
) -> tuple[ProactiveService, dict[str, Any]]:
    config = config or _config()
    repository = _FakeRepository()
    window = _FakeWindow()
    if lines is not None:
        window.lines[SCOPE] = list(lines)
    provider = provider or _FakeProvider()
    fake_service = _FakeService(
        outcomes if outcomes is not None else [_outcome(Action.NO_REPLY)]
    )
    sends: list[tuple[str, str]] = []

    async def message_sender(umo: str, text: str) -> None:
        sends.append((umo, text))

    async def persona_getter(umo: str) -> tuple[str, str]:
        return "人格提示", "persona-1"

    service = ProactiveService(
        config,
        repository,
        window,
        fake_service,  # type: ignore[arg-type]
        EnvelopeBuilder(config),
        provider_getter=lambda umo: provider,
        message_sender=message_sender,
        persona_getter=persona_getter,
        window_budget_getter=lambda umo: 6_000,
    )
    return service, {
        "repository": repository,
        "window": window,
        "fake_service": fake_service,
        "provider": provider,
        "sends": sends,
    }


def test_window_reply_sends_drains_and_shrinks() -> None:
    async def scenario() -> None:
        service, parts = _service(
            config=_config(proactive_window_min_seconds=5),
            lines=["小明: 今天聊什么", "小红: 随便啊"],
            outcomes=[_outcome(Action.REPLY)],
        )
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["sends"] == [
            (SCOPE, "第一条"),
            (SCOPE, "第二条"),
        ]
        assert parts["window"].drops == [SCOPE]
        state = parts["repository"].states[SCOPE]
        assert state["window_seconds"] == 5  # 初始 10 减半，受最小值约束
        assert "第一条\n第二条" in state["last_reply_text"]
        # 评估调用带 Wait 规则与人格、协议分节；上下文取自受管窗口
        call = parts["provider"].calls[0]
        assert call["contexts"] == [{"role": "system", "content": "历史轮次"}]
        assert call["session_id"] == ""
        assert "人格提示" in call["system_prompt"]
        assert "Wait" in call["prompt"]
        # 受管窗口按人格对应的 agent 标识加载，与正常轮读取同一份历史
        assert parts["window"].loads == [(SCOPE, "persona-1", 6_000)]
        record = parts["fake_service"].calls[0]
        assert record["allow_wait"] is True
        assert record["stage"] == "proactive_window"

    asyncio.run(scenario())


def test_window_no_reply_doubles_window() -> None:
    async def scenario() -> None:
        service, parts = _service(
            lines=["小明: 在吗"],
            outcomes=[_outcome(Action.NO_REPLY)],
        )
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["sends"] == []
        assert parts["window"].drops == [SCOPE]
        state = parts["repository"].states[SCOPE]
        assert state["window_seconds"] == 20

    asyncio.run(scenario())


def test_window_empty_batch_skips_the_model_call() -> None:
    async def scenario() -> None:
        service, parts = _service(lines=[])
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["provider"].calls == []
        assert parts["window"].drops == []

    asyncio.run(scenario())


def test_wait_defers_without_draining_until_exhausted() -> None:
    async def scenario() -> None:
        service, parts = _service(
            lines=["小明: 还在吗"],
            outcomes=[_outcome(Action.WAIT, wait_seconds=7)] * 3,
        )
        try:
            await service._evaluate(SCOPE, "window")
            assert parts["window"].drops == []
            assert service._waits[SCOPE] == 1
            assert SCOPE in service._window_timers
            service._window_timers.pop(SCOPE)

            await service._evaluate(SCOPE, "window")
            assert parts["window"].drops == []
            assert service._waits[SCOPE] == 2

            await service._evaluate(SCOPE, "window")
            # 第三次等待被拒：按不回复处理，排空批次并拉长窗口
            assert parts["window"].drops == [SCOPE]
            assert parts["repository"].states[SCOPE]["window_seconds"] == 20
            assert SCOPE not in service._waits
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_invalid_output_stretches_window_without_draining() -> None:
    async def scenario() -> None:
        service, parts = _service(
            lines=["小明: 内容"],
            outcomes=[_outcome(Action.REPLY, valid=False)],
        )
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["sends"] == []
        assert parts["window"].drops == []
        assert parts["repository"].states[SCOPE]["window_seconds"] == 20

    asyncio.run(scenario())


def test_direct_trigger_ignores_min_interval_and_disallows_wait() -> None:
    async def scenario() -> None:
        config = _config(proactive_min_reply_interval_seconds=3_600)
        service, parts = _service(
            config=config,
            lines=["小明: 机器人帮我看看"],
            outcomes=[_outcome(Action.REPLY)],
        )
        parts["repository"].states[SCOPE] = {
            "last_reply_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            await service._evaluate(SCOPE, "direct")
        finally:
            await service.shutdown()

        assert parts["sends"] != []
        record = parts["fake_service"].calls[0]
        assert record["allow_wait"] is False
        assert record["stage"] == "proactive_direct"
        # 直接触发的提示词不包含 Wait 规则
        assert "Wait" not in parts["provider"].calls[0]["prompt"]

    asyncio.run(scenario())


def test_window_evaluation_respects_min_reply_interval() -> None:
    async def scenario() -> None:
        config = _config(proactive_min_reply_interval_seconds=3_600)
        service, parts = _service(
            config=config,
            lines=["小明: 内容"],
            outcomes=[_outcome(Action.NO_REPLY)],
        )
        parts["repository"].states[SCOPE] = {
            "last_reply_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["provider"].calls == []
        assert parts["window"].drops == []

    asyncio.run(scenario())


def test_min_interval_expires() -> None:
    async def scenario() -> None:
        config = _config(proactive_min_reply_interval_seconds=60)
        service, parts = _service(
            config=config,
            lines=["小明: 内容"],
            outcomes=[_outcome(Action.REPLY)],
        )
        parts["repository"].states[SCOPE] = {
            "last_reply_at": (datetime.now() - timedelta(minutes=5)).isoformat(
                timespec="seconds"
            ),
        }
        try:
            await service._evaluate(SCOPE, "window")
        finally:
            await service.shutdown()

        assert parts["sends"] != []

    asyncio.run(scenario())


def test_followup_requires_quiet_group_and_known_reply() -> None:
    async def scenario() -> None:
        # 群里有人说话：不跟进
        service, parts = _service(
            lines=["小明: 我又来了"],
            outcomes=[_outcome(Action.REPLY)],
        )
        parts["repository"].states[SCOPE] = {"last_reply_text": "先前的回复"}
        try:
            await service._evaluate(SCOPE, "followup")
        finally:
            await service.shutdown()
        assert parts["provider"].calls == []

        # 群里安静且有未回应的发言：允许评估，禁用 Wait，提示词带原文
        service, parts = _service(
            lines=[],
            outcomes=[_outcome(Action.REPLY)],
        )
        parts["repository"].states[SCOPE] = {"last_reply_text": "先前的回复"}
        try:
            await service._evaluate(SCOPE, "followup")
        finally:
            await service.shutdown()
        call = parts["provider"].calls[0]
        assert "先前的回复" in call["prompt"]
        assert "Wait" not in call["prompt"]
        record = parts["fake_service"].calls[0]
        assert record["allow_wait"] is False

    asyncio.run(scenario())


def test_access_control_gates_every_entry() -> None:
    async def scenario() -> None:
        service, parts = _service(config=_config(proactive_mode="off"))
        try:
            await service.on_group_chatter(SCOPE)
        finally:
            await service.shutdown()
        assert service._window_timers == {}

        service, parts = _service(
            config=_config(proactive_whitelist=("999",)),
        )
        try:
            await service.on_group_chatter(SCOPE)
            await service.on_direct_trigger(SCOPE)
            await service.record_bot_reply(SCOPE, "回复", interactive=True)
        finally:
            await service.shutdown()
        assert service._window_timers == {}
        assert service._followup_timers == {}
        assert parts["repository"].states == {}

        # 黑名单命中则不参与，未命中的群正常进入
        service, parts = _service(
            config=_config(
                proactive_mode="blacklist",
                proactive_blacklist=("100",),
            ),
        )
        try:
            await service.on_group_chatter(SCOPE)
            await service.on_group_chatter("aiocqhttp:GroupMessage:200")
            assert SCOPE not in service._window_timers
            assert "aiocqhttp:GroupMessage:200" in service._window_timers
        finally:
            await service.shutdown()
            assert service._window_timers == {}

    asyncio.run(scenario())


def test_direct_trigger_replaces_pending_window_timer() -> None:
    async def scenario() -> None:
        service, parts = _service(lines=[])
        try:
            await service.on_group_chatter(SCOPE)
            assert SCOPE in service._window_timers
            await service.on_direct_trigger(SCOPE)
            assert SCOPE in service._window_timers
            # 替换而非叠加
            assert len(service._window_timers) == 1
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_record_bot_reply_stores_text_and_schedules_followup() -> None:
    async def scenario() -> None:
        service, parts = _service(lines=[])
        try:
            await service.record_bot_reply(SCOPE, "  这是一次回复  ", interactive=True)
            state = parts["repository"].states[SCOPE]
            assert state["last_reply_text"] == "这是一次回复"
            assert SCOPE in service._followup_timers

            await service.record_bot_reply(SCOPE, "", interactive=True)
            assert service._followup_timers  # 空文本不影响既有状态
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_plugin_hook_routes_group_messages() -> None:
    """The event hook splits unaddressed chatter into window and direct doors."""
    from types import SimpleNamespace

    from astrbot_plugin_humanize.main import HumanizePlugin

    class _HookEvent:
        def __init__(
            self,
            *,
            text: str = "",
            components: tuple[object, ...] = (),
            is_at: bool = False,
        ) -> None:
            self.unified_msg_origin = SCOPE
            self.message_obj = SimpleNamespace(message=list(components))
            self._text = text
            self._is_at = is_at

        def is_private_chat(self) -> bool:
            return False

        @property
        def is_at_or_wake_command(self) -> bool:
            return self._is_at

        def get_self_id(self) -> str:
            return "bot-1"

        def get_sender_id(self) -> str:
            return "user-1"

        def get_message_str(self) -> str:
            return self._text

    class _RecordingProactive:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def on_group_chatter(self, scope_id: str) -> None:
            self.calls.append(("window", scope_id))

        async def on_direct_trigger(self, scope_id: str) -> None:
            self.calls.append(("direct", scope_id))

    class Reply:
        """Name matters: the hook matches ``type(x).__name__ == "Reply"``."""

        def __init__(self, sender_id: str) -> None:
            self.sender_id = sender_id

    class _PrivateEvent:
        unified_msg_origin = SCOPE

        def is_private_chat(self) -> bool:
            return True

    async def scenario() -> None:
        plugin = HumanizePlugin(
            SimpleNamespace(),
            {
                "proactive_mode": "whitelist",
                "proactive_whitelist": ["100"],
                "proactive_keywords": ["小助"],
            },
        )
        proactive = _RecordingProactive()
        plugin._container = SimpleNamespace(proactive=proactive)

        await plugin._maybe_schedule_proactive(_HookEvent(text="今天天气不错"))
        assert proactive.calls == [("window", SCOPE)]

        await plugin._maybe_schedule_proactive(_HookEvent(text="小助 你在吗"))
        assert proactive.calls[-1] == ("direct", SCOPE)

        await plugin._maybe_schedule_proactive(
            _HookEvent(text="说得好", components=(Reply(sender_id="bot-1"),))
        )
        assert proactive.calls[-1] == ("direct", SCOPE)

        # 正常唤醒与私聊不进入主动路径
        await plugin._maybe_schedule_proactive(_HookEvent(text="在吗", is_at=True))
        await plugin._maybe_schedule_proactive(_PrivateEvent())
        assert len(proactive.calls) == 3

        # 机器人自己的消息被忽略
        self_event = _HookEvent(text="自言自语")
        self_event.get_sender_id = lambda: "bot-1"  # type: ignore[assignment]
        await plugin._maybe_schedule_proactive(self_event)
        assert len(proactive.calls) == 3

    asyncio.run(scenario())


def test_chatter_during_pending_window_does_not_reset_timer() -> None:
    """Sustained chatter must not postpone the pending evaluation forever."""

    async def scenario() -> None:
        service, _parts = _service(lines=[])
        try:
            await service.on_group_chatter(SCOPE)
            first = service._window_timers[SCOPE]
            await asyncio.sleep(0.01)
            await service.on_group_chatter(SCOPE)
            await service.on_group_chatter(SCOPE)
            assert service._window_timers[SCOPE] is first
        finally:
            await service.shutdown()

    asyncio.run(scenario())
