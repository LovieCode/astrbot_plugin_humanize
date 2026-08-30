"""Proactive group-participation trigger service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from ..config import PluginConfig
from ..domain.models import Action, MessageContext
from ..ports import RepositoryPort

logger = logging.getLogger("astrbot")

# 直接触发（提到名字 / 引用回复）仍稍等片刻，让紧跟着的补充消息并入同一批。
_DIRECT_TRIGGER_DELAY_SECONDS = 2.0
_MAX_WAITS_PER_BATCH = 3
# 计时反馈：回复后立刻回到 1 秒窗口；沉默/无效则拉长 10 秒。
_REPLY_RESET_SECONDS = 1
_NO_REPLY_STEP_SECONDS = 10


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def matches_scope(entries: tuple[str, ...] | list[str], scope_id: str) -> bool:
    """Match a session against configured entries.

    An entry matches on the full ``unified_msg_origin`` or on its trailing
    session segment, so a bare group id ``123456`` matches
    ``aiocqhttp:GroupMessage:123456`` but never a longer id ending in the
    same digits.
    """
    for entry in entries:
        token = str(entry or "").strip()
        if not token:
            continue
        if scope_id == token or scope_id.endswith(f":{token}"):
            return True
    return False


class ProactiveService:
    """Trigger the normal reply pipeline for unaddressed group chat.

    The service owns only timing and access control. A group's first
    unaddressed message starts the adaptive window; when it expires the
    service hands a synthetic event to the injected builder and pushes it
    into the host event queue, where the ordinary reply pipeline runs —
    full managed context, protocol validation, sending. The model's single
    Action is reported back through the outcome callback: Reply re-arms the
    window at 1 second, No Reply adds 10 seconds, Wait re-runs the batch
    after N seconds (at most three times). The service never calls a
    provider, sends a message, or decides what to say.
    """

    def __init__(
        self,
        config: PluginConfig,
        repository: RepositoryPort,
        *,
        event_builder: Callable[..., Any | None] | None = None,
        event_queue_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config
        self._repository = repository
        self._event_builder = event_builder
        self._event_queue_getter = event_queue_getter
        self._templates: dict[str, Any] = {}
        self._window_timers: dict[str, asyncio.Task[None]] = {}
        self._waits: dict[str, int] = {}
        self._closed = False

    # ---------- 入口（由消息钩子调用） ----------

    async def on_group_chatter(self, scope_id: str, *, event: Any = None) -> None:
        """Note one unaddressed group message; start the window if idle.

        A message arriving while a trigger is already pending only joins
        the history — the running timer is never reset, or sustained
        chatter could postpone the trigger indefinitely.

        Args:
            scope_id: Group session identifier (``unified_msg_origin``).
            event: The real message event, kept as the construction template
                for the synthetic proactive event.
        """
        if self._closed or not self._proactive_allowed(scope_id):
            return
        if event is not None:
            self._templates[scope_id] = event
        if scope_id in self._window_timers:
            return
        state = await self._safe_state(scope_id)
        window = self._clamp_window(
            int(
                state.get("window_seconds")
                or self._config.proactive_window_initial_seconds
            )
        )
        self._schedule_window(scope_id, float(window), kind="window")

    async def on_direct_trigger(self, scope_id: str, *, event: Any = None) -> None:
        """Trigger almost immediately after a high-precision trigger.

        Args:
            scope_id: Group session identifier.
            event: The real message event, kept as the construction template.
        """
        if self._closed or not self._proactive_allowed(scope_id):
            return
        if event is not None:
            self._templates[scope_id] = event
        self._schedule_window(scope_id, _DIRECT_TRIGGER_DELAY_SECONDS, kind="direct")

    async def on_bot_reply(self, scope_id: str) -> None:
        """Re-arm the window right after the bot replied in this group.

        A reply — proactive or a normal @-answer — means the conversation
        is warm; the next unaddressed message triggers after 1 second and
        the model decides whether to keep participating.

        Args:
            scope_id: Group session identifier.
        """
        if self._closed or not self._proactive_allowed(scope_id):
            return
        self._waits.pop(scope_id, None)
        await self._remember(scope_id, window_seconds=_REPLY_RESET_SECONDS)

    async def shutdown(self) -> None:
        """Cancel every pending timer; called on plugin unload."""
        self._closed = True
        for task in self._window_timers.values():
            task.cancel()
        self._window_timers.clear()
        self._waits.clear()
        self._templates.clear()

    # ---------- 调度 ----------

    def _schedule_window(self, scope_id: str, delay: float, *, kind: str) -> None:
        self._cancel_timer(self._window_timers, scope_id)
        task = asyncio.create_task(
            self._timer_entry(scope_id, delay, kind, self._window_timers),
            name=f"humanize-proactive-{kind}",
        )
        self._window_timers[scope_id] = task

    @staticmethod
    def _cancel_timer(slot: dict[str, asyncio.Task[None]], scope_id: str) -> None:
        task = slot.pop(scope_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _timer_entry(
        self,
        scope_id: str,
        delay: float,
        kind: str,
        slot: dict[str, asyncio.Task[None]],
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # 被更新的计时器替换；不得触碰槽位里可能已存在的新任务。
            return
        try:
            await self._trigger(scope_id, kind)
        except Exception:
            logger.exception(
                "[Humanize] proactive trigger failed (kind=%s scope=%s)",
                kind,
                scope_id,
            )
        finally:
            if slot.get(scope_id) is asyncio.current_task():
                slot.pop(scope_id, None)

    # ---------- 触发主流程 ----------

    async def _trigger(self, scope_id: str, kind: str) -> None:
        """Build one synthetic event and hand it to the reply pipeline.

        There is no separate evaluation step: the queued event runs the
        ordinary reply flow, and the model's single Action comes back
        through the outcome callback.
        """
        if self._closed:
            return
        if self._event_builder is None or self._event_queue_getter is None:
            logger.debug(
                "[Humanize] proactive triggering unavailable (no injected builder)"
            )
            return
        template = self._templates.get(scope_id)
        if template is None:
            logger.debug(
                "[Humanize] proactive trigger skipped: no template for %s", scope_id
            )
            return
        await self._remember(scope_id, last_eval_at=_iso_now())
        try:
            event = self._event_builder(
                template,
                kind=kind,
                on_outcome=self._outcome(scope_id),
            )
        except Exception:
            logger.exception("[Humanize] proactive event construction failed")
            return
        if event is None:
            logger.debug(
                "[Humanize] proactive trigger skipped: builder refused %s", scope_id
            )
            return
        try:
            queue = self._event_queue_getter()
            queue.put_nowait(event)
        except Exception:
            logger.exception("[Humanize] failed to queue the proactive event")

    # ---------- 结果回传 ----------

    def _outcome(self, scope_id: str) -> Callable[..., Awaitable[None]]:
        """Build the per-scope outcome callback the pipeline reports to."""

        async def outcome(
            context: MessageContext,
            *,
            action: Action | None,
            wait_seconds: int = 0,
        ) -> None:
            if self._closed:
                return
            if action is Action.WAIT:
                waits = self._waits.get(scope_id, 0) + 1
                if waits >= _MAX_WAITS_PER_BATCH:
                    logger.info(
                        "[Humanize] proactive batch exhausted waits scope=%s",
                        scope_id,
                    )
                    self._waits.pop(scope_id, None)
                    await self._stretch_window(scope_id)
                    return
                self._waits[scope_id] = waits
                self._schedule_window(
                    scope_id, float(max(1, wait_seconds)), kind="window"
                )
                return
            self._waits.pop(scope_id, None)
            if action is Action.REPLY:
                await self._remember(
                    scope_id,
                    window_seconds=_REPLY_RESET_SECONDS,
                    last_eval_at=_iso_now(),
                )
                logger.info("[Humanize] proactive reply sent scope=%s", scope_id)
                return
            # No Reply 或输出无效：同样拉长窗口；无效输出不做额外重试，
            # 下一次触发时模型会自然重新看到同样的历史。
            await self._stretch_window(scope_id)
            logger.info(
                "[Humanize] proactive trigger stayed silent (valid=%s scope=%s)",
                action is Action.NO_REPLY,
                scope_id,
            )

        return outcome

    # ---------- 状态辅助 ----------

    async def _stretch_window(self, scope_id: str) -> None:
        state = await self._safe_state(scope_id)
        current = self._clamp_window(
            int(
                state.get("window_seconds")
                or self._config.proactive_window_initial_seconds
            )
        )
        await self._remember(
            scope_id,
            window_seconds=self._clamp_window(current + _NO_REPLY_STEP_SECONDS),
            last_eval_at=_iso_now(),
        )

    async def _remember(self, scope_id: str, **fields: Any) -> None:
        try:
            await self._repository.update_proactive_state(scope_id=scope_id, **fields)
        except Exception:
            logger.exception("[Humanize] failed to persist proactive state")

    async def _safe_state(self, scope_id: str) -> dict[str, Any]:
        try:
            return await self._repository.get_proactive_state(scope_id=scope_id)
        except Exception:
            logger.exception("[Humanize] failed to read proactive state")
            return {}

    # ---------- 访问控制 ----------

    def _proactive_allowed(self, scope_id: str) -> bool:
        """Whether proactive triggering is enabled for this session.

        Mode ``off`` means unrestricted normal participation but no
        proactive triggering; whitelist and blacklist select the sessions
        where triggering (and the whole proactive timing) runs at all.
        """
        mode = self._config.proactive_mode
        if mode == "whitelist":
            return matches_scope(self._config.proactive_whitelist, scope_id)
        if mode == "blacklist":
            return not matches_scope(self._config.proactive_blacklist, scope_id)
        return False

    def _clamp_window(self, seconds: int) -> int:
        maximum = self._config.proactive_window_max_seconds
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = self._config.proactive_window_initial_seconds
        return max(1, min(value, maximum))
