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

_MAX_WAITS_PER_BATCH = 3
# 计时反馈：回复后自适应窗口回到 2 秒保底（真正的发言节奏由「回复后
# 静默期」约束）；拒绝（No Reply / 无效输出 / 等待耗尽）的增量依次为
# 1、3、5、10 秒，之后每步 ×√2 缓增（14, 20, 28, 40…），整体仍受
# max 窗口钳制。
_REPLY_RESET_SECONDS = 2
_MIN_WINDOW_SECONDS = 2
_NO_REPLY_STEP_SCHEDULE = (1, 3, 5, 10)


def _no_reply_step(rejections: int) -> int:
    """Return the stretch increment for the Nth consecutive rejection.

    Args:
        rejections: 连续拒绝次数，从 1 起。

    Returns:
        前四步取固定序列 1/3/5/10，之后每步 ×√2（四舍五入取整）。
    """
    schedule_len = len(_NO_REPLY_STEP_SCHEDULE)
    if rejections <= schedule_len:
        return _NO_REPLY_STEP_SCHEDULE[rejections - 1]
    return round(_NO_REPLY_STEP_SCHEDULE[-1] * 2 ** ((rejections - schedule_len) / 2))


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
    window (subject to the post-reply quiet period), a rejection (No Reply,
    invalid output, or exhausted waits) stretches it by 1/3/5/10 seconds
    and then by ×√2 per step, Wait re-runs the batch after N seconds (at
    most three times). Every synthetic event carries the scope's reply
    serial, so an evaluation that was queued while the bot sent another
    reply is dropped before the model is called. The service never calls
    a provider, sends a message, or decides what to say.
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
        self._rejections: dict[str, int] = {}
        # 并发防护（见 on_llm_request 的过期评估丢弃）：
        # - _reply_serials：每个群「Bot 已发出的回复条数」单调计数。合成事件
        #   触发时打上当前序号；评估真正开始时序号若已前进，说明这条评估
        #   排队期间 Bot 又说过话，再评估只会对同一段上下文重复发言。
        # - _quiet_until：Bot 回复后的静默期截止时间（事件循环单调时钟），
        #   主动窗口触发到点后若仍在静默期则顺延，直达触发不受限。
        # - _wait_entries：Wait 再检查触发时记录的窗口条目基线；到点后
        #   条目数没变（期间既无新发言也无 Bot 回复）就不再重复评估。
        self._reply_serials: dict[str, int] = {}
        self._quiet_until: dict[str, float] = {}
        self._wait_entries: dict[str, int] = {}
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
        if self._closed:
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
        """Trigger the reply pipeline immediately after a high-precision trigger.

        关键词命中 / 引用回复说明有人明确在叫 Bot，不做任何延迟，
        直接进入回复流水线。

        Args:
            scope_id: Group session identifier.
            event: The real message event, kept as the construction template.
        """
        if self._closed:
            return
        if event is not None:
            self._templates[scope_id] = event
        self._schedule_window(scope_id, 0.0, kind="direct")

    async def on_bot_reply(self, scope_id: str) -> None:
        """Re-arm the window right after the bot replied in this group.

        A reply — proactive or a normal @-answer — means the conversation
        is warm; the consecutive-rejection ladder is cleared, the reply
        serial advances (queued evaluations of the same moment become
        stale), and the next unaddressed message waits out the
        post-reply quiet period before the model decides whether to
        keep participating.

        Args:
            scope_id: Group session identifier.
        """
        if self._closed:
            return
        self._waits.pop(scope_id, None)
        self._rejections.pop(scope_id, None)
        self._wait_entries.pop(scope_id, None)
        # 回复打断当前窗口/Wait 计时：过期评估本会在 LLM 前被序号丢弃，
        # 取消计时器能少一次无用排队。下一条闲聊会按 2 秒保底重新开窗。
        self._cancel_timer(self._window_timers, scope_id)
        self._mark_bot_reply(scope_id)
        await self._remember(scope_id, window_seconds=_REPLY_RESET_SECONDS)

    def reply_serial(self, scope_id: str) -> int:
        """Return how many bot replies this scope has produced so far.

        Args:
            scope_id: Group session identifier.

        Returns:
            Monotonic reply counter; unknown scopes start at zero.
        """
        return self._reply_serials.get(scope_id, 0)

    def _mark_bot_reply(self, scope_id: str) -> None:
        """Advance the reply serial and arm the post-reply quiet period.

        Args:
            scope_id: Group session identifier.
        """
        self._reply_serials[scope_id] = self.reply_serial(scope_id) + 1
        cooldown = self._config.proactive_post_reply_cooldown_seconds
        if cooldown > 0:
            self._quiet_until[scope_id] = asyncio.get_running_loop().time() + cooldown

    async def on_wait_requested(
        self,
        scope_id: str,
        *,
        event: Any = None,
        wait_seconds: int = 0,
        window_entries: int = -1,
    ) -> None:
        """Schedule one window re-check after a normal group turn chose Wait.

        常驻 Wait 的落点：普通回合里模型暂不回应时，把这次等待计入同一批
        （与主动批共用 3 次上限），到点后触发一次 wait 复查重新决定。
        等待期间的新发言只更新模板、不打断计时，与窗口检查一致。再检查
        会带上当前窗口条目基线：到点后若历史毫无变化，这条再检查会在
        评估开始前被丢弃，不会对着同一段上下文重复调用模型。

        Args:
            scope_id: Group session identifier (``unified_msg_origin``).
            event: The triggering real message event, kept as the
                construction template fallback for groups without chatter.
            wait_seconds: Requested wait duration.
            window_entries: Managed-window entry count observed by the
                turn that requested the wait; ``-1`` marks it unknown.
        """
        if self._closed:
            return
        if event is not None:
            self._templates[scope_id] = event
        waits = self._waits.get(scope_id, 0) + 1
        if waits >= _MAX_WAITS_PER_BATCH:
            logger.info("[Humanize] wait batch exhausted scope=%s", scope_id)
            self._waits.pop(scope_id, None)
            await self._stretch_window(scope_id)
            return
        self._waits[scope_id] = waits
        self._remember_wait_entries(scope_id, window_entries)
        self._schedule_window(scope_id, float(max(1, wait_seconds)), kind="wait")

    async def shutdown(self) -> None:
        """Cancel every pending timer; called on plugin unload."""
        self._closed = True
        for task in self._window_timers.values():
            task.cancel()
        self._window_timers.clear()
        self._waits.clear()
        self._rejections.clear()
        self._reply_serials.clear()
        self._quiet_until.clear()
        self._wait_entries.clear()
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
        if kind != "direct":
            # 回复后静默期：到点时若 Bot 刚说过话，把这次评估顺延到静默期
            # 结束（直达触发代表有人明确在叫 Bot，不做顺延）。新发言只
            # 更新模板，不打断顺延计时。循环重算剩余时间：顺延期间若又有
            # 回复把静默期拉长，一次性 sleep 会提前醒来，必须再等一轮。
            try:
                while True:
                    remaining = self._quiet_until.get(scope_id, 0.0) - (
                        asyncio.get_running_loop().time()
                    )
                    if remaining <= 0:
                        break
                    await asyncio.sleep(remaining)
            except asyncio.CancelledError:
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
        through the outcome callback. The event carries the scope's
        current reply serial (plus the window entry baseline for Wait
        re-checks) so the pipeline can drop evaluations that went stale
        while sitting in the event queue.
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
        # Wait 基线只在入队成功后消费：构造/入队失败时保留，避免复查
        # 带着空基线 fail-open 对着同一段历史再评估一次。
        armed_entries = self._wait_entries.get(scope_id) if kind == "wait" else None
        try:
            event = self._event_builder(
                template,
                kind=kind,
                on_outcome=self._outcome(scope_id),
                armed_reply_serial=self.reply_serial(scope_id),
                armed_window_entries=armed_entries,
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
            return
        if kind == "wait":
            self._wait_entries.pop(scope_id, None)

    # ---------- 结果回传 ----------

    def _outcome(self, scope_id: str) -> Callable[..., Awaitable[None]]:
        """Build the per-scope outcome callback the pipeline reports to."""

        async def outcome(
            context: MessageContext,
            *,
            action: Action | None,
            wait_seconds: int = 0,
            window_entries: int = -1,
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
                self._remember_wait_entries(scope_id, window_entries)
                self._schedule_window(
                    scope_id, float(max(1, wait_seconds)), kind="wait"
                )
                return
            self._waits.pop(scope_id, None)
            if action is Action.REPLY:
                self._rejections.pop(scope_id, None)
                self._wait_entries.pop(scope_id, None)
                self._cancel_timer(self._window_timers, scope_id)
                self._mark_bot_reply(scope_id)
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

    def _remember_wait_entries(self, scope_id: str, window_entries: int) -> None:
        """Record the window-entry baseline for one scheduled Wait re-check.

        Args:
            scope_id: Group session identifier.
            window_entries: Entry count observed when the wait was
                requested; ``-1`` marks it unknown and clears the baseline.
        """
        try:
            count = int(window_entries)
        except (TypeError, ValueError):
            self._wait_entries.pop(scope_id, None)
            return
        if count < 0:
            self._wait_entries.pop(scope_id, None)
            return
        self._wait_entries[scope_id] = count

    async def _stretch_window(self, scope_id: str) -> None:
        state = await self._safe_state(scope_id)
        current = self._clamp_window(
            int(
                state.get("window_seconds")
                or self._config.proactive_window_initial_seconds
            )
        )
        rejections = self._rejections.get(scope_id, 0) + 1
        self._rejections[scope_id] = rejections
        await self._remember(
            scope_id,
            window_seconds=self._clamp_window(current + _no_reply_step(rejections)),
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

    # ---------- 状态辅助（续） ----------

    def _clamp_window(self, seconds: int) -> int:
        maximum = self._config.proactive_window_max_seconds
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = self._config.proactive_window_initial_seconds
        return max(_MIN_WINDOW_SECONDS, min(value, maximum))
