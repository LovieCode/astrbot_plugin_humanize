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
    values: dict[str, Any] = {
        "proactive_mode": "whitelist",
        "proactive_whitelist": ("100",),
    }
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


def test_access_control_gates_every_entry() -> None:
    async def scenario() -> None:
        # 关闭模式：完全不触发。
        service, parts = _service(config=_config(proactive_mode="off"))
        await service.on_group_chatter(SCOPE, event=_template())
        await service.on_direct_trigger(SCOPE, event=_template())
        await service.on_bot_reply(SCOPE)
        await service.shutdown()
        assert parts["builder"].calls == []
        assert parts["repository"].states == {}

        # 白名单未命中：不触发，模板也不保留。
        service, parts = _service(
            config=_config(proactive_whitelist=("999",)),
        )
        await service.on_group_chatter(SCOPE, event=_template())
        await service.shutdown()
        assert parts["builder"].calls == []

        # 黑名单命中不触发，未命中的群正常进入。
        service, parts = _service(
            config=_config(
                proactive_mode="blacklist",
                proactive_blacklist=("100",),
            ),
        )
        await service.on_group_chatter(SCOPE, event=_template())
        assert SCOPE not in service._window_timers
        await service.on_group_chatter("aiocqhttp:GroupMessage:200", event=_template())
        assert "aiocqhttp:GroupMessage:200" in service._window_timers
        await service.shutdown()
        assert service._window_timers == {}

    asyncio.run(scenario())


def test_direct_trigger_replaces_pending_window_timer() -> None:
    async def scenario() -> None:
        service, _parts = _service()
        try:
            await service.on_group_chatter(SCOPE, event=_template())
            assert SCOPE in service._window_timers
            await service.on_direct_trigger(SCOPE, event=_template())
            assert SCOPE in service._window_timers
            # 替换而非叠加
            assert len(service._window_timers) == 1
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
        assert "没有 @ 你" in chain[1].text
        assert "Wait" in chain[1].text
        assert event.extras[_PROACTIVE_KIND_KEY] == "window"
        assert event.extras[_PROACTIVE_OUTCOME_CALLBACK_KEY] is outcome

        # direct 场景不携带等待说明
        direct = plugin._build_proactive_event(
            template, kind="direct", on_outcome=outcome
        )
        assert direct is not None
        assert "Wait" not in direct.message_obj.message[1].text

        # 没有模板时拒绝构造
        assert (
            plugin._build_proactive_event(None, kind="window", on_outcome=outcome)
            is None
        )

    asyncio.run(scenario())


def test_session_permitted_gates_reply_by_mode() -> None:
    class _Event:
        def __init__(self, *, umo: str, private: bool = False) -> None:
            self.unified_msg_origin = umo
            self._private = private

        def is_private_chat(self) -> bool:
            return self._private

    async def scenario() -> None:
        from astrbot_plugin_humanize.main import HumanizePlugin

        plugin = HumanizePlugin(SimpleNamespace(), {})
        assert plugin._session_permitted(_Event(umo=SCOPE)) is True
        assert plugin._session_permitted(_Event(umo=SCOPE, private=True)) is True

        plugin = HumanizePlugin(
            SimpleNamespace(),
            {
                "proactive_mode": "whitelist",
                "proactive_whitelist": ["100"],
            },
        )
        assert plugin._session_permitted(_Event(umo=SCOPE)) is True
        assert (
            plugin._session_permitted(_Event(umo="aiocqhttp:GroupMessage:200")) is False
        )
        # 私聊不受许可名单影响
        assert (
            plugin._session_permitted(
                _Event(umo="aiocqhttp:FriendMessage:200", private=True)
            )
            is True
        )

        plugin = HumanizePlugin(
            SimpleNamespace(),
            {
                "proactive_mode": "blacklist",
                "proactive_blacklist": ["100"],
            },
        )
        assert plugin._session_permitted(_Event(umo=SCOPE)) is False
        assert (
            plugin._session_permitted(_Event(umo="aiocqhttp:GroupMessage:200")) is True
        )

    asyncio.run(scenario())
