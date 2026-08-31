"""Behavior tests for the proactive group-participation trigger service.

The service is exercised through injected fakes for the event builder, the
event queue, and the repository, so each test pins one timing or access rule
without real model calls, timers, or an AstrBot runtime.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import Action, MessageContext
from astrbot_plugin_humanize.humanize.protocol.envelope import EnvelopeBuilder
from astrbot_plugin_humanize.humanize.services.proactive import (
    ProactiveService,
    matches_scope,
)

SCOPE = "aiocqhttp:GroupMessage:100"


def _config(**overrides: Any) -> PluginConfig:
    values: dict[str, Any] = {}
    values.update(overrides)
    return PluginConfig(**values)


def _ctx() -> MessageContext:
    return MessageContext(
        request_id="request-1",
        scope_type="group",
        scope_id=SCOPE,
        message_id="message-1",
        sender_id="user-1",
        sender_name="小明",
        user_text="群里的话",
        chat_scene="QQ群",
        admin_name="",
        admin_ids=(),
        conversation_id=SCOPE,
        occurred_at="2026-08-30T10:00:00+00:00",
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
        last_eval_at: str | None = None,
    ) -> None:
        state = self.states.setdefault(scope_id, {})
        if window_seconds is not None:
            state["window_seconds"] = window_seconds
        if last_eval_at is not None:
            state["last_eval_at"] = last_eval_at

    async def reset_proactive_state(self, *, scope_id: str) -> None:
        self.states.pop(scope_id, None)


class _FakeTemplateEvent:
    """Stand-in for a real platform event with an adapter-shaped ctor."""

    def __init__(
        self,
        message_str,
        message_obj,
        platform_meta,
        session_id,
        bot=None,
    ) -> None:
        self.message_str = message_str
        self.message_obj = message_obj
        self.platform_meta = platform_meta
        self.session_id = session_id
        self.bot = bot
        self.extras: dict[str, Any] = {}

    def set_extra(self, key: str, value: Any) -> None:
        self.extras[key] = value

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self.extras.get(key, default)

    def get_self_id(self) -> str:
        return str(self.message_obj.self_id)


def _template(bot: str = "bot-1") -> _FakeTemplateEvent:
    message_obj = SimpleNamespace(
        self_id=bot,
        type="GroupMessage",
        session_id="100",
        sender=SimpleNamespace(user_id="user-1", nickname="小明"),
        group=SimpleNamespace(group_id="100"),
    )
    return _FakeTemplateEvent(
        "",
        message_obj,
        SimpleNamespace(name="aiocqhttp"),
        "100",
        bot="CQBOT",
    )


class _FakeQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def put_nowait(self, event: Any) -> None:
        self.events.append(event)


class _FakeBuilder:
    def __init__(self, result: Any = "synthetic-event") -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = result

    def __call__(self, template: Any, *, kind: str, on_outcome: Any) -> Any:
        self.calls.append(
            {"template": template, "kind": kind, "on_outcome": on_outcome}
        )
        return self._result


def _service(
    *,
    config: PluginConfig | None = None,
    builder: _FakeBuilder | None = None,
    queue: _FakeQueue | None = None,
) -> tuple[ProactiveService, dict[str, Any]]:
    config = config or _config()
    repository = _FakeRepository()
    builder = builder if builder is not None else _FakeBuilder()
    queue = queue if queue is not None else _FakeQueue()
    service = ProactiveService(
        config,
        repository,  # type: ignore[arg-type]
        event_builder=builder,
        event_queue_getter=lambda: queue,
    )
    return service, {
        "repository": repository,
        "builder": builder,
        "queue": queue,
    }


async def _arm(service: ProactiveService, template: Any) -> None:
    """Store a template and drop the auto-started timer for direct triggering."""
    await service.on_group_chatter(SCOPE, event=template)
    task = service._window_timers.pop(SCOPE, None)
    if task is not None:
        task.cancel()


def test_window_trigger_builds_event_and_enqueues() -> None:
    async def scenario() -> None:
        service, parts = _service()
        template = _template()
        await _arm(service, template)

        await service._trigger(SCOPE, "window")
        await service.shutdown()

        call = parts["builder"].calls[0]
        assert call["template"] is template
        assert call["kind"] == "window"
        assert callable(call["on_outcome"])
        assert parts["queue"].events == ["synthetic-event"]
        assert "last_eval_at" in parts["repository"].states[SCOPE]

    asyncio.run(scenario())


def test_direct_trigger_uses_direct_kind() -> None:
    async def scenario() -> None:
        service, parts = _service()
        await _arm(service, _template())

        await service._trigger(SCOPE, "direct")
        await service.shutdown()

        assert parts["builder"].calls[0]["kind"] == "direct"
        assert len(parts["queue"].events) == 1

    asyncio.run(scenario())


def test_builder_refusal_skips_the_queue() -> None:
    async def scenario() -> None:
        service, parts = _service(builder=_FakeBuilder(result=None))
        await _arm(service, _template())

        await service._trigger(SCOPE, "window")
        await service.shutdown()

        assert parts["queue"].events == []

    asyncio.run(scenario())


def test_missing_template_skips_triggering() -> None:
    async def scenario() -> None:
        service, parts = _service()

        await service._trigger(SCOPE, "window")
        await service.shutdown()

        assert parts["builder"].calls == []
        assert parts["queue"].events == []

    asyncio.run(scenario())


def test_reply_outcome_resets_window_to_one_second() -> None:
    async def scenario() -> None:
        service, parts = _service()
        parts["repository"].states[SCOPE] = {"window_seconds": 120}
        outcome = service._outcome(SCOPE)

        await outcome(_ctx(), action=Action.REPLY)
        await service.shutdown()

        assert parts["repository"].states[SCOPE]["window_seconds"] == 1
        assert SCOPE not in service._waits

    asyncio.run(scenario())


def test_no_reply_outcome_adds_ten_seconds() -> None:
    async def scenario() -> None:
        service, parts = _service()
        outcome = service._outcome(SCOPE)

        await outcome(_ctx(), action=Action.NO_REPLY)
        await service.shutdown()

        assert parts["repository"].states[SCOPE]["window_seconds"] == 20

    asyncio.run(scenario())


def test_no_reply_window_grows_to_the_cap() -> None:
    async def scenario() -> None:
        service, parts = _service()
        parts["repository"].states[SCOPE] = {"window_seconds": 295}
        outcome = service._outcome(SCOPE)

        await outcome(_ctx(), action=Action.NO_REPLY)
        await service.shutdown()

        assert parts["repository"].states[SCOPE]["window_seconds"] == 300

    asyncio.run(scenario())


def test_invalid_outcome_also_stretches_window() -> None:
    async def scenario() -> None:
        service, parts = _service()
        outcome = service._outcome(SCOPE)

        await outcome(_ctx(), action=None)
        await service.shutdown()

        assert parts["repository"].states[SCOPE]["window_seconds"] == 20

    asyncio.run(scenario())


def test_wait_defers_and_exhausts_after_three() -> None:
    async def scenario() -> None:
        service, parts = _service()
        outcome = service._outcome(SCOPE)

        await outcome(_ctx(), action=Action.WAIT, wait_seconds=7)
        assert service._waits[SCOPE] == 1
        assert SCOPE in service._window_timers
        service._window_timers.pop(SCOPE).cancel()

        await outcome(_ctx(), action=Action.WAIT, wait_seconds=7)
        assert service._waits[SCOPE] == 2
        service._window_timers.pop(SCOPE).cancel()

        # 第三次等待被拒：按沉默处理，拉长窗口且不再排新计时器。
        await outcome(_ctx(), action=Action.WAIT, wait_seconds=7)
        assert SCOPE not in service._waits
        assert parts["repository"].states[SCOPE]["window_seconds"] == 20
        assert SCOPE not in service._window_timers
        await service.shutdown()

    asyncio.run(scenario())


def test_on_bot_reply_resets_window_and_waits() -> None:
    async def scenario() -> None:
        service, parts = _service()
        parts["repository"].states[SCOPE] = {"window_seconds": 120}
        service._waits[SCOPE] = 2

        await service.on_bot_reply(SCOPE)
        await service.shutdown()

        assert parts["repository"].states[SCOPE]["window_seconds"] == 1
        assert SCOPE not in service._waits

    asyncio.run(scenario())


def test_plugin_policy_gates_proactive_doors() -> None:
    """策略模式决定主动路径的门：全关 / 仅管理员触发 / 仅直接触发 / 全开。"""

    class _HookEvent:
        def __init__(self, *, text: str, raw: Any = None) -> None:
            self._text = text
            self.message_obj = SimpleNamespace(message=[], raw_message=raw)
            self.unified_msg_origin = SCOPE

        def is_private_chat(self) -> bool:
            return False

        @property
        def is_at_or_wake_command(self) -> bool:
            return False

        def get_self_id(self) -> str:
            return "bot-1"

        def get_sender_id(self) -> str:
            return "user-1"

        def get_message_str(self) -> str:
            return self._text

    class _RecordingProactive:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, Any]] = []

        async def on_group_chatter(self, scope_id: str, *, event: Any = None) -> None:
            self.calls.append(("window", scope_id, event))

        async def on_direct_trigger(self, scope_id: str, *, event: Any = None) -> None:
            self.calls.append(("direct", scope_id, event))

    async def scenario() -> None:
        from astrbot_plugin_humanize.main import HumanizePlugin

        plugin = HumanizePlugin(SimpleNamespace(), {"proactive_keywords": ["小助"]})
        proactive = _RecordingProactive()
        plugin._container = SimpleNamespace(proactive=proactive)

        # silent / no_proactive：所有门都关。
        for mode in ("silent", "no_proactive"):
            proactive.calls.clear()
            await plugin._maybe_schedule_proactive(
                _HookEvent(text="小助在吗"), policy_mode=mode
            )
            assert proactive.calls == []

        # mention：直接触发开门，闲聊不开窗。
        proactive.calls.clear()
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="小助在吗"), policy_mode="mention"
        )
        assert [call[0] for call in proactive.calls] == ["direct"]
        proactive.calls.clear()
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="今天天气不错"), policy_mode="mention"
        )
        assert proactive.calls == []

        # admin：管理员触发开门，普通成员同样的话不行。
        proactive.calls.clear()
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="小助在吗", raw={"sender": {"role": "admin"}}),
            policy_mode="admin",
        )
        assert [call[0] for call in proactive.calls] == ["direct"]
        proactive.calls.clear()
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="小助在吗", raw={"sender": {"role": "member"}}),
            policy_mode="admin",
        )
        assert proactive.calls == []

        # full：闲聊开窗 + 直接触发（现状行为）。
        proactive.calls.clear()
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="今天天气不错"), policy_mode="full"
        )
        assert [call[0] for call in proactive.calls] == ["window"]
        await plugin._maybe_schedule_proactive(
            _HookEvent(text="小助在吗"), policy_mode="full"
        )
        assert proactive.calls[-1][0] == "direct"

    asyncio.run(scenario())


def test_direct_trigger_replaces_pending_window_timer() -> None:
    async def scenario() -> None:
        service, parts = _service()
        try:
            await service.on_group_chatter(SCOPE, event=_template())
            assert SCOPE in service._window_timers
            await service.on_direct_trigger(SCOPE, event=_template())
            assert SCOPE in service._window_timers
            # 替换而非叠加
            assert len(service._window_timers) == 1
            # 直接触发零延迟：让一拍事件循环后立即排队，不等可感知的计时。
            await asyncio.sleep(0.01)
            assert parts["queue"].events == ["synthetic-event"]
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_chatter_during_pending_window_does_not_reset_timer() -> None:
    """Sustained chatter must not postpone the pending trigger forever."""

    async def scenario() -> None:
        service, _parts = _service()
        try:
            await service.on_group_chatter(SCOPE, event=_template())
            first = service._window_timers[SCOPE]
            await asyncio.sleep(0.01)
            await service.on_group_chatter(SCOPE, event=_template())
            await service.on_group_chatter(SCOPE, event=_template())
            assert service._window_timers[SCOPE] is first
        finally:
            await service.shutdown()

    asyncio.run(scenario())


def test_matches_scope_accepts_full_and_trailing_ids() -> None:
    assert matches_scope(("100",), SCOPE)
    assert matches_scope((SCOPE,), SCOPE)
    assert not matches_scope(("1100",), SCOPE)
    assert not matches_scope(("", "  "), SCOPE)


def test_plugin_hook_routes_group_messages_with_template() -> None:
    """The event hook splits unaddressed chatter into window and direct doors."""

    class _HookEvent:
        def __init__(self, *, text: str, is_at: bool = False, components=()) -> None:
            self._text = text
            self._is_at = is_at
            self.message_obj = SimpleNamespace(message=list(components))
            self.unified_msg_origin = SCOPE

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
            self.calls: list[tuple[str, str, Any]] = []

        async def on_group_chatter(self, scope_id: str, *, event: Any = None) -> None:
            self.calls.append(("window", scope_id, event))

        async def on_direct_trigger(self, scope_id: str, *, event: Any = None) -> None:
            self.calls.append(("direct", scope_id, event))

    class Reply:
        """Name matters: the hook matches ``type(x).__name__ == "Reply"``."""

        def __init__(self, sender_id: str) -> None:
            self.sender_id = sender_id

    class _PrivateEvent:
        unified_msg_origin = SCOPE

        def is_private_chat(self) -> bool:
            return True

    async def scenario() -> None:
        from astrbot_plugin_humanize.main import HumanizePlugin

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
        assert proactive.calls[0][0] == "window"
        assert proactive.calls[0][2] is not None  # 模板事件被保留

        await plugin._maybe_schedule_proactive(_HookEvent(text="小助 你在吗"))
        assert proactive.calls[-1][0] == "direct"

        await plugin._maybe_schedule_proactive(
            _HookEvent(text="说得好", components=(Reply(sender_id="bot-1"),))
        )
        assert proactive.calls[-1][0] == "direct"

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


def test_plugin_builds_synthetic_event_from_template() -> None:
    """The injected factory assembles a waking event with the template's session."""

    class _FakePlatformEvent:
        def __init__(
            self,
            message_str,
            message_obj,
            platform_meta,
            session_id,
            bot=None,
        ) -> None:
            self.message_str = message_str
            self.message_obj = message_obj
            self.platform_meta = platform_meta
            self.session_id = session_id
            self.bot = bot
            self.extras: dict[str, Any] = {}

        def set_extra(self, key: str, value: Any) -> None:
            self.extras[key] = value

    async def scenario() -> None:
        from astrbot_plugin_humanize.main import (
            _PROACTIVE_KIND_KEY,
            _PROACTIVE_OUTCOME_CALLBACK_KEY,
            HumanizePlugin,
        )

        from astrbot.core.message.components import At, Plain

        plugin = HumanizePlugin(
            SimpleNamespace(),
            {"proactive_mode": "whitelist", "proactive_whitelist": ["100"]},
        )
        plugin._container = SimpleNamespace(envelope=EnvelopeBuilder(_config()))
        message_obj = SimpleNamespace(
            self_id="bot-1",
            type="GroupMessage",
            session_id="100",
            sender=SimpleNamespace(user_id="user-1", nickname="小明"),
            group=SimpleNamespace(group_id="100"),
        )
        template = _FakePlatformEvent(
            "", message_obj, SimpleNamespace(name="aiocqhttp"), "100", bot="CQBOT"
        )
        outcome = lambda *args, **kwargs: None  # noqa: E731

        event = plugin._build_proactive_event(
            template, kind="window", on_outcome=outcome
        )
        assert isinstance(event, _FakePlatformEvent)
        assert event.session_id == "100"
        assert event.bot == "CQBOT"
        chain = event.message_obj.message
        assert isinstance(chain[0], At) and str(chain[0].qq) == "bot-1"
        assert isinstance(chain[1], Plain)
        # <Msg> 占位：窗口检查是行动指引（用户定稿），情况说明不进消息文本
        assert "群里正在聊天" in chain[1].text
        # Wait 规则本体仍跟随回复协议注入，不进入事件消息文本
        assert "最多等待 3 次" not in chain[1].text
        assert "没有 @ 你" not in chain[1].text
        assert event.extras[_PROACTIVE_KIND_KEY] == "window"
        assert event.extras[_PROACTIVE_OUTCOME_CALLBACK_KEY] is outcome

        # direct 场景的 <Msg> 同样是占位文本
        direct = plugin._build_proactive_event(
            template, kind="direct", on_outcome=outcome
        )
        assert direct is not None
        assert "没有附上用户消息" in direct.message_obj.message[1].text

        # 没有模板时拒绝构造
        assert (
            plugin._build_proactive_event(None, kind="window", on_outcome=outcome)
            is None
        )

    asyncio.run(scenario())


def test_policy_mode_resolution_prefers_group_override() -> None:
    """会话模式解析：按群覆盖优先，其余套用 global 行，缺行回退代码默认。"""

    class _PolicyRepo:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows

        async def list_group_policies(self) -> list[dict[str, Any]]:
            return [dict(row) for row in self.rows]

    async def scenario() -> None:
        from astrbot_plugin_humanize.main import HumanizePlugin

        plugin = HumanizePlugin(SimpleNamespace(), {})
        # 没有仓库（未初始化）：回退代码默认。
        assert await plugin._policy_mode_for(SCOPE) == "mention"

        # global 行生效。
        plugin._container = SimpleNamespace(
            repository=_PolicyRepo([{"scope_id": "global", "mode": "full"}])
        )
        assert await plugin._policy_mode_for(SCOPE) == "full"

        # 按群覆盖优先，支持裸群号后缀匹配；未覆盖的群套用 global。
        plugin._container = SimpleNamespace(
            repository=_PolicyRepo(
                [
                    {"scope_id": "global", "mode": "full"},
                    {"scope_id": "100", "mode": "silent"},
                ]
            )
        )
        assert await plugin._policy_mode_for(SCOPE) == "silent"
        assert await plugin._policy_mode_for("aiocqhttp:GroupMessage:200") == "full"

        # 空行与空模式被忽略，落回代码默认。
        plugin._container = SimpleNamespace(
            repository=_PolicyRepo(
                [
                    {"scope_id": "", "mode": "silent"},
                    {"scope_id": "global", "mode": ""},
                ]
            )
        )
        assert await plugin._policy_mode_for(SCOPE) == "mention"

    asyncio.run(scenario())
