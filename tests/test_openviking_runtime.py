from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from humanize.config import PluginConfig
from humanize.domain.models import MessageContext
from humanize.memory import ChatMemoryService
from humanize.openviking import (
    OpenVikingManagementAdapter,
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)


class _RepositoryWithoutLegacyMemory:
    """Repository stub intentionally exposing no legacy memory CRUD methods."""


def _context(*, agent_id: str = "default") -> MessageContext:
    return MessageContext(
        request_id="request-a",
        scope_type="private",
        scope_id="private-a",
        message_id="message-a",
        sender_id="user-a",
        sender_name="测试用户",
        user_text="我喜欢无糖乌龙茶",
        chat_scene="QQ 私聊",
        admin_name="管理员",
        admin_ids=(),
        conversation_id="conversation-a",
        occurred_at="2026-07-17T00:00:00+00:00",
        agent_id=agent_id,
    )


def test_runtime_write_recall_and_management_use_only_openviking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = PluginConfig()
        monkeypatch.setenv(config.memory_identity_secret_env, "s" * 32)
        workspace = OpenVikingWorkspace(tmp_path / "plugin-data")
        memory = OpenVikingMemoryAdapter(workspace)
        management = OpenVikingManagementAdapter(memory, workspace)
        service = ChatMemoryService(
            config,
            _RepositoryWithoutLegacyMemory(),  # type: ignore[arg-type]
            openviking_adapter=memory,
            openviking_recall_adapter=OpenVikingRecallAdapter(workspace),
            openviking_management_adapter=management,
        )
        await service.initialize()
        agent_id = "webchat default"
        context = _context(agent_id=agent_id)
        job = await service.build_turn_job(
            context,
            action="Reply",
            messages=("记住了",),
        )
        assert job is not None

        await service._extract_turn_batch([job])
        recalled = await service.recall_memories(context)
        identity = service.identity_for(context)
        scope_token = service.encode_scope_token(
            scope_type=identity.primary_scope_type,
            scope_hash=identity.primary_scope_hash,
            subject_hash=identity.subject_hash,
        )
        listing = await service.list_memories(
            scope_token=scope_token,
            agent_id=agent_id,
            status="active",
            page=1,
            page_size=20,
        )
        detail = await service.get_memory_detail(str(listing["items"][0]["id"]))
        debug = await service.debug_recall(
            query="无糖乌龙茶",
            scope_token=scope_token,
            kind="memory",
            agent_id=agent_id,
            memory_type="preference",
        )

        assert recalled.included is True
        assert recalled.source_refs[0].startswith("viking://")
        assert listing["total"] == 1
        assert detail is not None
        assert debug["included"] is True
        assert (workspace.root / "memories").is_dir()

    asyncio.run(scenario())


def test_runtime_openviking_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingMemoryAdapter:
        def initialize(self) -> None:
            raise RuntimeError("workspace unavailable")

    class UnusedAdapter:
        async def recall(self, **filters: Any) -> None:
            del filters

    async def scenario() -> None:
        config = PluginConfig()
        monkeypatch.setenv(config.memory_identity_secret_env, "s" * 32)
        service = ChatMemoryService(
            config,
            _RepositoryWithoutLegacyMemory(),  # type: ignore[arg-type]
            openviking_adapter=FailingMemoryAdapter(),  # type: ignore[arg-type]
            openviking_recall_adapter=UnusedAdapter(),  # type: ignore[arg-type]
            openviking_management_adapter=UnusedAdapter(),  # type: ignore[arg-type]
        )

        await service.initialize()
        result = await service.recall_memories(_context())
        status = await service.get_status()

        assert result.included is False
        assert result.reason == "source_error"
        assert status["state"] == "ready"
        assert status["openviking_state"] == "error"
        assert status["openviking_error"] == "RuntimeError"

    asyncio.run(scenario())
