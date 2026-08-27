from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_humanize.humanize.config import PluginConfig
from astrbot_plugin_humanize.humanize.domain.models import MessageContext
from astrbot_plugin_humanize.humanize.memory import ChatMemoryService
from astrbot_plugin_humanize.humanize.openviking import (
    OpenVikingManagementAdapter,
    OpenVikingMemoryAdapter,
    OpenVikingRecallAdapter,
    OpenVikingWorkspace,
)
from astrbot_plugin_humanize.humanize.provider_catalog import ProviderCatalog
from astrbot_plugin_humanize.humanize.repositories.sqlite import SQLiteRepository
from astrbot_plugin_humanize.humanize.web.routes import WebApi


class _FakeRequest:
    """Provide the request attributes consumed by the plugin Web API."""

    def __init__(
        self,
        method: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
    ) -> None:
        self.method = method
        self.query = query or {}
        self._body = body

    async def json(self, default: Any = None) -> Any:
        """Return the configured JSON body.

        Args:
            default: Fallback value used when no body was configured.

        Returns:
            The configured body or the supplied fallback.
        """
        return default if self._body is None else self._body


def _response_payload(response: Any) -> dict[str, Any]:
    """Decode a Starlette JSON response.

    Args:
        response: Response returned by the Web API dispatcher.

    Returns:
        The decoded JSON object.
    """
    return json.loads(response.body.decode("utf-8"))


def test_provider_catalog_lists_astrbot_personas_without_prompts() -> None:
    """Persona choices expose only IDs and labels needed by recall debugging."""

    async def scenario() -> None:
        class PersonaManager:
            """Provide configured v3 and database-backed personas."""

            selected_default_persona_v3 = {"name": "眠汐", "prompt": "secret"}
            default_persona = "default"
            personas_v3 = [
                {"name": "眠汐", "prompt": "secret"},
                {"name": "default", "prompt": "secret"},
            ]

            async def get_all_personas(self) -> list[Any]:
                """Return a database persona not present in v3 configuration."""
                return [SimpleNamespace(persona_id="小助手", system_prompt="secret")]

        payload = await ProviderCatalog(
            SimpleNamespace(persona_manager=PersonaManager())
        ).list_memory_personas()

        assert payload["state"] == "ready"
        assert payload["default_id"] == "眠汐"
        assert payload["items"][0]["id"] == "眠汐"
        assert {item["id"] for item in payload["items"]} == {
            "眠汐",
            "default",
            "小助手",
        }
        assert "secret" not in json.dumps(payload, ensure_ascii=False)

    asyncio.run(scenario())


def test_web_api_manages_internal_memory_and_reply_examples_end_to_end(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Memory and reviewed examples use the shared repository and opaque scopes."""

    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        class PersonaCatalog:
            """Return configured personas for the Web API merge contract."""

            async def list_memory_personas(self) -> dict[str, Any]:
                """Return one configured default persona."""
                return {
                    "state": "ready",
                    "default_id": "persona-configured",
                    "items": [
                        {
                            "id": "persona-configured",
                            "label": "配置人格",
                            "source": "astrbot_persona",
                        }
                    ],
                }

        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        config = PluginConfig()
        workspace = OpenVikingWorkspace(tmp_path)
        openviking = OpenVikingMemoryAdapter(workspace)
        openviking.initialize()
        memory = ChatMemoryService(
            config,
            repository,
            openviking_adapter=openviking,
            openviking_recall_adapter=OpenVikingRecallAdapter(workspace),
            openviking_management_adapter=OpenVikingManagementAdapter(
                openviking, workspace
            ),
        )
        memory._secret = b"web-memory-integration-secret-32-bytes"
        memory._state = "ready"
        memory._openviking_ready = True
        memory._reason = "test_identity_secret"
        api = WebApi(
            repository,
            config,
            provider_catalog=PersonaCatalog(),  # type: ignore[arg-type]
            memory=memory,
        )
        context = MessageContext(
            request_id="web-memory-request",
            scope_type="private",
            scope_id="private-1",
            message_id="message-1",
            sender_id="user-1",
            sender_name="测试用户",
            user_text="无糖乌龙茶",
            chat_scene="QQ 私聊",
            admin_name="管理员",
            admin_ids=("admin-1",),
            conversation_id="conversation-1",
            agent_id="agent-a",
        )
        scope = memory.identity_for(context).scopes[0]
        scope_token = memory.encode_scope_token(
            scope_type=scope["scope_type"],
            scope_hash=scope["scope_hash"],
            subject_hash=scope["subject_hash"],
        )

        for path, body in (
            (
                "memory-action",
                {
                    "action": "create",
                    "memory_type": "preference",
                    "memory_key": "隐式全局记忆",
                    "canonical_text": "不应在未选作用域时创建",
                },
            ),
            (
                "reply-example-action",
                {
                    "action": "create",
                    "title": "隐式全局样例",
                    "turns": [{"role": "user", "content": "测试"}],
                    "ideal_reply": "不应创建。",
                },
            ),
        ):
            monkeypatch.setattr(
                astrbot_web,
                "request",
                _FakeRequest("POST", body=body),
            )
            assert (await api.dispatch(path)).status_code == 400

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "action": "create",
                    "scope_token": scope_token,
                    "type": "preference",
                    "memory_key": "喜欢的饮料",
                    "content": "用户喜欢无糖乌龙茶",
                    "agent_id": "agent-a",
                    "status": "active",
                    "confidence": 0.96,
                    "importance": 0.8,
                    "evidence": {"excerpt": "我喜欢无糖乌龙茶"},
                },
            ),
        )
        created_memory_response = await api.dispatch("memory-action")
        created_memory = _response_payload(created_memory_response)["data"]
        assert created_memory_response.status_code == 200
        assert created_memory["scope_token"]
        assert "scope_hash" not in created_memory
        assert "subject_hash" not in created_memory

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "1", "page_size": "20"}),
        )
        memories = _response_payload(await api.dispatch("memories"))["data"]
        assert memories["total"] == 1
        assert memories["items"][0]["memory_key"] == "喜欢的饮料"

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"id": str(created_memory["id"])}),
        )
        detail = _response_payload(await api.dispatch("memory-detail"))["data"]
        assert detail["evidence"][0]["quote"] == "用户喜欢无糖乌龙茶"

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={"query": "无糖乌龙茶", "scope_token": scope_token},
            ),
        )
        assert (await api.dispatch("memory-recall-debug")).status_code == 400

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "query": "无糖乌龙茶",
                    "scope_token": scope_token,
                    "agent_id": "agent-a",
                },
            ),
        )
        memory_recall = _response_payload(await api.dispatch("memory-recall-debug"))[
            "data"
        ]
        assert memory_recall["included"] is True
        assert memory_recall["items"][0]["id"] == created_memory["id"]
        assert "<MemoryContext>" in memory_recall["content"]

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "action": "create",
                    "scope_token": scope_token,
                    "title": "自然回应摸鱼",
                    "agent_id": "agent-a",
                    "topic": "摸鱼",
                    "intent": "轻松闲聊",
                    "keywords": ["摸鱼"],
                    "turns": [{"role": "user", "content": "怎么摸鱼"}],
                    "ideal_reply": "先把最烦的事清掉。",
                    "status": "approved",
                    "enabled": True,
                    "quality_score": 0.95,
                },
            ),
        )
        created_example_response = await api.dispatch("reply-example-action")
        created_example = _response_payload(created_example_response)["data"]
        assert created_example_response.status_code == 200
        assert created_example["scope_token"]
        assert "scope_hash" not in created_example

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest("GET", query={"page": "1", "page_size": "20"}),
        )
        examples = _response_payload(await api.dispatch("reply-examples"))["data"]
        assert examples["total"] == 1
        assert examples["items"][0]["title"] == "自然回应摸鱼"

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "GET",
                query={
                    "topic": "摸鱼",
                    "intent": "轻松闲聊",
                    "page": "1",
                    "page_size": "20",
                },
            ),
        )
        filtered_examples = _response_payload(await api.dispatch("reply-examples"))[
            "data"
        ]
        assert filtered_examples["total"] == 1
        assert filtered_examples["items"][0]["id"] == created_example["id"]

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "GET",
                query={
                    "topic": "摸鱼",
                    "intent": "错误意图",
                    "page": "1",
                    "page_size": "20",
                },
            ),
        )
        assert (
            _response_payload(await api.dispatch("reply-examples"))["data"]["total"]
            == 0
        )

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "query": "摸鱼",
                    "scope_token": scope_token,
                    "agent_id": "*",
                },
            ),
        )
        assert (await api.dispatch("reply-example-recall-debug")).status_code == 400

        monkeypatch.setattr(
            astrbot_web,
            "request",
            _FakeRequest(
                "POST",
                body={
                    "query": "摸鱼",
                    "scope_token": scope_token,
                    "agent_id": "agent-a",
                },
            ),
        )
        example_recall = _response_payload(
            await api.dispatch("reply-example-recall-debug")
        )["data"]
        assert example_recall["included"] is True
        assert example_recall["items"][0]["id"] == created_example["id"]
        assert "不要照抄" in example_recall["content"]

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        persona_options = _response_payload(await api.dispatch("memory-agent-options"))[
            "data"
        ]
        assert persona_options["meaning"] == "AstrBot 当前会话最终生效的人格 ID"
        assert persona_options["default_id"] == "persona-configured"
        assert persona_options["items"][0] == {
            "id": "persona-configured",
            "label": "配置人格",
            "source": "astrbot_persona",
            "configured": True,
            "observed": False,
            "observed_count": 0,
            "last_seen_at": "",
            "debuggable": True,
        }
        observed_agent = next(
            item for item in persona_options["items"] if item["id"] == "agent-a"
        )
        assert observed_agent["configured"] is False
        assert observed_agent["observed"] is True
        assert observed_agent["observed_count"] >= 1
        assert observed_agent["debuggable"] is True

        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        removed_route = await api.dispatch("openviking-status")
        assert removed_route.status_code == 404
        assert list(tmp_path.glob("*.db")) == [tmp_path / "humanize.db"]
        assert workspace.root.is_dir()

    asyncio.run(scenario())


def test_web_api_returns_bounded_public_errors(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Invalid and failed requests expose stable status codes without internals."""

    async def scenario() -> None:
        import astrbot.api.web as astrbot_web

        repository = SQLiteRepository(tmp_path / "humanize.db")
        await repository.initialize()
        api = WebApi(repository, PluginConfig())

        cases = [
            (
                _FakeRequest("POST", body=["not", "an", "object"]),
                "prompt-templates",
                400,
                "请求体必须是 JSON 对象",
            ),
            (_FakeRequest("GET"), "missing", 404, "未找到该接口"),
            (_FakeRequest("PATCH"), "prompt-templates", 405, "不支持的请求方法"),
        ]
        for request, path, status_code, message in cases:
            monkeypatch.setattr(astrbot_web, "request", request)
            response = await api.dispatch(path)
            payload = _response_payload(response)
            assert response.status_code == status_code
            assert payload["status"] == "error"
            assert message in payload["message"]

        for method in ("GET", "POST"):
            for path in (
                "features",
                "control-overview",
                "persona",
                "state",
                "behavior",
                "expression",
                "control-audit",
                "control/reset",
                "control-reset",
            ):
                monkeypatch.setattr(astrbot_web, "request", _FakeRequest(method))
                response = await api.dispatch(path)
                assert response.status_code == 404

        class BrokenRepository:
            """Raise an internal error from the retained overview route."""

            async def get_overview(self) -> dict[str, Any]:
                """Simulate a storage failure.

                Raises:
                    RuntimeError: Always raised to exercise the 500 boundary.
                """
                raise RuntimeError("database password and internal path")

        broken_api = WebApi(
            BrokenRepository(),  # type: ignore[arg-type]
            PluginConfig(),
        )
        monkeypatch.setattr(astrbot_web, "request", _FakeRequest("GET"))
        response = await broken_api.dispatch("overview")
        payload = _response_payload(response)
        assert response.status_code == 500
        assert payload == {
            "status": "error",
            "message": "插件内部错误",
            "data": None,
        }
        assert "password" not in response.body.decode("utf-8")

    asyncio.run(scenario())
