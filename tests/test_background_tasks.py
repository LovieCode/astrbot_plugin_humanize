"""Regression tests for the fire-and-forget background task registry."""

from __future__ import annotations

import asyncio
import gc

import pytest
from astrbot_plugin_humanize import main as humanize_main


class _LoggerRecorder:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, msg: str, *args: object) -> None:
        self.errors.append(msg % args if args else msg)

    def exception(self, msg: str, *args: object) -> None:  # pragma: no cover
        self.errors.append(msg % args if args else msg)


def test_spawn_background_keeps_strong_reference_until_done() -> None:
    """A spawned task must survive GC pressure and register a strong ref."""

    async def scenario() -> None:
        started = asyncio.Event()
        finished = asyncio.Event()

        async def worker() -> None:
            started.set()
            for _ in range(5):
                await asyncio.sleep(0)
            finished.set()

        task = humanize_main._spawn_background(worker(), name="test-strong-ref")
        try:
            assert task in humanize_main._BACKGROUND_TASKS
            await asyncio.wait_for(started.wait(), timeout=1.0)

            # The event loop holds only a weak reference; GC pressure must
            # not collect the pending task.
            for _ in range(3):
                await asyncio.sleep(0)
            gc.collect()
            assert not task.done()
            assert not finished.is_set()

            await asyncio.wait_for(finished.wait(), timeout=1.0)
            await asyncio.sleep(0)
            assert task not in humanize_main._BACKGROUND_TASKS
        finally:
            humanize_main._BACKGROUND_TASKS.discard(task)

    asyncio.run(scenario())


def test_spawn_background_retrieves_and_logs_exception(monkeypatch) -> None:
    """An in-task failure must be retrieved (no 'never retrieved' warning)."""
    recorder = _LoggerRecorder()
    monkeypatch.setattr(humanize_main, "logger", recorder)

    async def scenario() -> None:
        async def boom() -> None:
            raise RuntimeError("background boom")

        task = humanize_main._spawn_background(boom(), name="test-boom")
        try:
            with pytest.raises(RuntimeError):
                await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
        finally:
            humanize_main._BACKGROUND_TASKS.discard(task)
        # Let the done callback scheduled by the completed task run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert any("background task" in line for line in recorder.errors), recorder.errors
