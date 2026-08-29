"""Regression test: OpenViking workspace init must not block the event loop."""

from __future__ import annotations

import asyncio
import threading

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.memory import _MIN_SECRET_BYTES, ChatMemoryService
from tests.test_memory import _memory_service


class _StubAdapter:
    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def initialize(self) -> None:
        self.threads.append(threading.current_thread())


class _StubRecall:
    pass


class _StubManagement:
    pass


def test_openviking_initialize_runs_off_event_loop(monkeypatch) -> None:
    """workspace.initialize may sleep on lock contention, so it must leave the loop."""
    secret = "x" * _MIN_SECRET_BYTES
    monkeypatch.setenv("HUMANIZE_MEMORY_SECRET", secret)

    config = PluginConfig()
    service: ChatMemoryService = _memory_service(object(), config)
    adapter = _StubAdapter()
    service._openviking = adapter  # type: ignore[assignment]
    service._openviking_recall = _StubRecall()  # type: ignore[assignment]
    service._openviking_management = _StubManagement()  # type: ignore[assignment]

    async def scenario() -> None:
        await asyncio.wait_for(service.initialize(), timeout=5.0)

    asyncio.run(scenario())

    assert service._openviking_ready is True
    assert len(adapter.threads) == 1
    assert adapter.threads[0] is not threading.main_thread()
