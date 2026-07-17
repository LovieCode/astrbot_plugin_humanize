from __future__ import annotations

import logging
import re
from typing import Any, cast

from ..config import PluginConfig
from ..domain.prompts import PROMPT_TEMPLATE_SPEC_BY_KEY, PromptTemplates
from ..memory import ChatMemoryService
from ..ports import RepositoryPort
from ..protocol.envelope import EnvelopeBuilder
from ..provider_catalog import ProviderCatalog
from ..repositories.sqlite import SQLiteRepository
from ..services.control import ControlService

logger = logging.getLogger("astrbot")


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _required_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("缺少有效的词条 ID") from exc
    if parsed <= 0:
        raise ValueError("缺少有效的词条 ID")
    return parsed


def _required_memory_id(value: Any) -> int | str:
    """Accept a legacy integer or OpenViking SHA-256 memory identifier.

    Args:
        value: Untrusted memory identifier from the query string.

    Returns:
        Positive legacy integer or lowercase OpenViking digest.

    Raises:
        ValueError: If the identifier is missing or malformed.
    """
    text = str(value or "").strip().lower()
    if text.isdigit() and int(text) > 0:
        return int(text)
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    raise ValueError("缺少有效的记忆 ID")


def _required_request_id(value: Any) -> str:
    request_id = str(value or "").strip()
    if not request_id or len(request_id) > 200:
        raise ValueError("缺少有效的请求 ID")
    return request_id


class WebApi:
    def __init__(
        self,
        repository: RepositoryPort,
        config: PluginConfig,
        control_service: ControlService | None = None,
        envelope: EnvelopeBuilder | None = None,
        provider_catalog: ProviderCatalog | None = None,
        memory: ChatMemoryService | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._control = control_service or ControlService(
            cast(SQLiteRepository, repository)
        )
        self._envelope = envelope or EnvelopeBuilder(config)
        self._provider_catalog = provider_catalog
        self._memory = memory

    async def dispatch(self, subpath: str = ""):
        from astrbot.api.web import error_response, request

        path = (subpath or "").strip("/")
        try:
            if request.method == "GET":
                return await self._handle_get(path)
            if request.method == "POST":
                return await self._handle_post(path)
            return error_response("不支持的请求方法", status_code=405)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        except PermissionError as exc:
            return error_response(str(exc), status_code=403)
        except LookupError as exc:
            return error_response(str(exc), status_code=404)
        except RuntimeError as exc:
            if "conflict" in str(exc).lower() or "lease" in str(exc).lower():
                return error_response(str(exc), status_code=409)
            logger.exception(
                "[Humanize] Web API runtime failure: %s %s", request.method, path
            )
            return error_response("插件内部错误", status_code=500)
        except Exception:
            logger.exception("[Humanize] Web API failed: %s %s", request.method, path)
            return error_response("插件内部错误", status_code=500)

    async def _handle_get(self, path: str):
        from astrbot.api.web import error_response, request

        if path == "overview":
            return self._ok(await self._repository.get_overview())
        if path == "jargons":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            data = await self._repository.list_jargons(
                search=str(request.query.get("search", "")).strip(),
                status=str(request.query.get("status", "")).strip(),
                scope_id=str(request.query.get("scope_id", "")).strip(),
                scope_type=str(request.query.get("scope_type", "")).strip(),
                page=page,
                page_size=page_size,
            )
            return self._ok(data)
        if path == "jargon-detail":
            entry_id = _required_id(request.query.get("id"))
            data = await self._repository.get_jargon_detail(entry_id)
            if data is None:
                return error_response("词条不存在", status_code=404)
            return self._ok(data)
        if path == "jargon-export":
            return self._ok(
                await self._repository.export_jargons(
                    search=str(request.query.get("search", "")).strip(),
                    scope_type=str(request.query.get("scope_type", "")).strip(),
                    scope_id=str(request.query.get("scope_id", "")).strip(),
                    status=str(request.query.get("status", "")).strip(),
                )
            )
        if path == "protocol-logs":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._repository.list_protocol_logs(
                    page=page, page_size=page_size
                )
            )
        if path == "context-runs":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._repository.list_context_runs(
                    scope_type=str(request.query.get("scope_type", "")).strip(),
                    scope_id=str(request.query.get("scope_id", "")).strip(),
                    section_key=str(request.query.get("section_key", "")).strip(),
                    page=page,
                    page_size=page_size,
                )
            )
        if path == "context-run":
            request_id = _required_request_id(
                request.query.get("request_id") or request.query.get("id")
            )
            data = await self._repository.get_context_run(request_id)
            if data is None:
                return error_response("上下文追踪不存在", status_code=404)
            return self._ok(data)
        if path == "context-stats":
            days = _positive_int(request.query.get("days"), 7, 365)
            return self._ok(await self._repository.get_context_stats(days=days))
        if path == "provider-cache-capabilities":
            return self._ok(
                {"items": await self._repository.list_provider_cache_capabilities()}
            )
        if path == "memory-status":
            if self._memory is None:
                return self._ok(
                    {
                        "enabled": self._config.memory_enabled,
                        "state": "not_initialized",
                        "reason": "memory_service_not_initialized",
                    }
                )
            return self._ok(await self._memory.get_status())
        if path == "memory-overview":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            return self._ok(await self._memory.get_memory_overview())
        if path == "memory-agent-options":
            configured = {
                "state": "not_initialized",
                "default_id": "default",
                "items": [
                    {
                        "id": "default",
                        "label": "默认人格",
                        "source": "fallback",
                    }
                ],
            }
            if self._provider_catalog is not None:
                configured = await self._provider_catalog.list_memory_personas()

            observed: list[dict[str, Any]] = []
            observed_getter = getattr(
                self._repository, "list_memory_agent_options", None
            )
            if callable(observed_getter):
                observed = await observed_getter()

            default_id = str(configured.get("default_id") or "default").strip()
            merged: dict[str, dict[str, Any]] = {}
            raw_configured = configured.get("items")
            if isinstance(raw_configured, list):
                for raw in raw_configured:
                    if not isinstance(raw, dict):
                        continue
                    persona_id = str(raw.get("id") or "").strip()[:160]
                    if not persona_id:
                        continue
                    merged[persona_id] = {
                        "id": persona_id,
                        "label": str(raw.get("label") or persona_id),
                        "source": str(raw.get("source") or "astrbot_persona"),
                        "configured": True,
                        "observed": False,
                        "observed_count": 0,
                        "last_seen_at": "",
                        "debuggable": persona_id != "*",
                    }
            merged.setdefault(
                default_id,
                {
                    "id": default_id,
                    "label": "默认人格" if default_id == "default" else default_id,
                    "source": "astrbot_default",
                    "configured": True,
                    "observed": False,
                    "observed_count": 0,
                    "last_seen_at": "",
                    "debuggable": True,
                },
            )
            for raw in observed:
                persona_id = str(raw.get("id") or "").strip()[:160]
                if not persona_id:
                    continue
                entry = merged.setdefault(
                    persona_id,
                    {
                        "id": persona_id,
                        "label": (
                            "共享记忆"
                            if persona_id == "*"
                            else "WebChat 默认人格"
                            if persona_id == "_chatui_default_"
                            else persona_id
                        ),
                        "source": "history",
                        "configured": False,
                        "observed": False,
                        "observed_count": 0,
                        "last_seen_at": "",
                        "debuggable": persona_id != "*",
                    },
                )
                entry["observed"] = True
                entry["observed_count"] = int(raw.get("observed_count") or 0)
                entry["last_seen_at"] = str(raw.get("last_seen_at") or "")
                if entry["configured"]:
                    entry["source"] = "astrbot_persona_and_history"

            items = list(merged.values())
            items.sort(key=lambda item: str(item["last_seen_at"]), reverse=True)
            items.sort(
                key=lambda item: (
                    0
                    if item["id"] == default_id
                    else 1
                    if item["configured"]
                    else 3
                    if item["id"] == "*"
                    else 2
                )
            )
            return self._ok(
                {
                    "meaning": "AstrBot 当前会话最终生效的人格 ID",
                    "configured_state": configured.get("state", "not_initialized"),
                    "default_id": default_id,
                    "items": items,
                }
            )
        if path == "memories":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._memory.list_memories(
                    search=str(request.query.get("search", "")).strip(),
                    status=str(request.query.get("status", "")).strip(),
                    memory_type=str(
                        request.query.get("type")
                        or request.query.get("memory_type")
                        or ""
                    ).strip(),
                    scope_type=str(request.query.get("scope_type", "")).strip(),
                    scope_token=str(request.query.get("scope_token", "")).strip(),
                    agent_id=str(request.query.get("agent_id", "")).strip(),
                    page=page,
                    page_size=page_size,
                )
            )
        if path == "memory-detail":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            data = await self._memory.get_memory_detail(
                _required_memory_id(request.query.get("id"))
            )
            if data is None:
                return error_response("记忆不存在", status_code=404)
            return self._ok(data)
        if path == "memory-jobs":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            status = str(request.query.get("status", "")).strip()
            if status and status not in {
                "pending",
                "running",
                "retry",
                "completed",
                "dead",
            }:
                raise ValueError("不支持的记忆任务状态")
            return self._ok(
                await self._memory.list_memory_jobs(
                    status=status,
                    job_type=str(request.query.get("job_type", "")).strip(),
                    agent_id=str(request.query.get("agent_id", "")).strip(),
                    page=page,
                    page_size=page_size,
                )
            )
        if path == "reply-examples":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._memory.list_reply_examples(
                    search=str(request.query.get("search", "")).strip(),
                    status=str(request.query.get("status", "")).strip(),
                    scope_type=str(request.query.get("scope_type", "")).strip(),
                    scope_token=str(request.query.get("scope_token", "")).strip(),
                    agent_id=str(request.query.get("agent_id", "")).strip(),
                    topic=str(request.query.get("topic", "")).strip(),
                    intent=str(request.query.get("intent", "")).strip(),
                    enabled=str(request.query.get("enabled", "")).strip(),
                    page=page,
                    page_size=page_size,
                )
            )
        if path == "reply-example-detail":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            data = await self._memory.get_reply_example_detail(
                _required_id(request.query.get("id"))
            )
            if data is None:
                return error_response("回复样例不存在", status_code=404)
            return self._ok(data)
        if path == "chat-providers":
            if self._provider_catalog is None:
                return self._ok({"state": "not_initialized", "providers": []})
            return self._ok(await self._provider_catalog.list_chat_providers())
        if path == "memory-providers":
            if self._provider_catalog is None:
                return self._ok(
                    {
                        "state": "not_initialized",
                        "chat": [],
                        "embedding": [],
                        "rerank": [],
                    }
                )
            return self._ok(await self._provider_catalog.list_memory_providers())
        if path == "settings":
            settings = self._config.as_public_dict()
            settings["control_sections"] = [
                "persona",
                "state",
                "behavior",
                "expression",
            ]
            return self._ok(settings)
        if path == "prompt-templates":
            stored = await self._repository.get_prompt_templates()
            templates = PromptTemplates.from_mapping(stored["templates"])
            return self._ok(
                {
                    "items": templates.as_items(updated_at=stored["updated_at"]),
                    "templates": templates.as_dict(),
                    "updated_at": stored["updated_at"],
                }
            )
        if path in {"features", "control-overview"}:
            return self._ok(await self._control.get_features())
        if path in {"persona", "state", "behavior", "expression"}:
            return self._ok(await self._control.get_section(path))
        if path == "control-audit":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._control.list_audit(page=page, page_size=page_size)
            )
        return error_response("未找到该接口", status_code=404)

    async def _handle_post(self, path: str):
        from astrbot.api.web import error_response, request

        body = await request.json(default={})
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        if path in {"persona", "state", "behavior", "expression"}:
            return self._ok(
                await self._control.update_section(
                    path, body, reason=str(body.get("reason") or "web update")
                )
            )
        if path in {"control/reset", "control-reset"}:
            section = str(body.get("section") or "").strip().lower()
            reason = str(body.get("reason") or "").strip()
            return self._ok(
                {
                    "sections": await self._control.reset(section, reason),
                    "reset": [section]
                    if section != "all"
                    else [
                        "persona",
                        "state",
                        "behavior",
                        "expression",
                    ],
                }
            )
        if path == "prompt-templates":
            action = str(body.get("action") or "update").strip().lower()
            reason = str(body.get("reason") or "").strip()
            if action == "reset":
                key = str(body.get("key") or "").strip().lower()
                if key == "all":
                    updated_keys = list(PROMPT_TEMPLATE_SPEC_BY_KEY)
                elif key in PROMPT_TEMPLATE_SPEC_BY_KEY:
                    updated_keys = [key]
                else:
                    raise ValueError("缺少有效的提示词模板 key")
                defaults = PromptTemplates().as_dict()
                updates = {key: defaults[key] for key in updated_keys}
                audit_action = "reset"
                if not reason:
                    reason = "restore prompt template default"
            elif action in {"update", "save"}:
                if "templates" in body:
                    raw_templates = body["templates"]
                    if not isinstance(raw_templates, dict) or not raw_templates:
                        raise ValueError("templates 必须是非空 JSON 对象")
                    updates = dict(raw_templates)
                    updated_keys = list(updates)
                else:
                    key = str(body.get("key") or "").strip().lower()
                    if key not in PROMPT_TEMPLATE_SPEC_BY_KEY:
                        raise ValueError("缺少有效的提示词模板 key")
                    if "content" not in body:
                        raise ValueError("缺少提示词模板 content")
                    updates = {key: body["content"]}
                    updated_keys = [key]
                audit_action = "update"
                if not reason:
                    reason = "web update"
            else:
                raise ValueError("不支持的提示词模板操作")

            stored = await self._repository.update_prompt_templates(
                updates,
                actor="web_admin",
                reason=reason,
                action=audit_action,
            )
            self._envelope.set_templates(stored["templates"])
            templates = PromptTemplates.from_mapping(stored["templates"])
            items = templates.as_items(updated_at=stored["updated_at"])
            result = {
                "items": items,
                "templates": templates.as_dict(),
                "updated_at": stored["updated_at"],
                "updated": updated_keys if audit_action == "update" else [],
                "reset": updated_keys if audit_action == "reset" else [],
            }
            if len(updated_keys) == 1:
                result["item"] = next(
                    item for item in items if item["key"] == updated_keys[0]
                )
            return self._ok(result)
        if path == "memory-action":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            if (
                str(body.get("action") or "update").strip().lower() == "create"
                and not str(body.get("scope_token") or "").strip()
            ):
                raise ValueError("新增记忆必须明确选择作用域")
            return self._ok(
                await self._memory.apply_memory_action(body, actor="web_admin")
            )
        if path == "memory-recall-debug":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            scope_token = str(body.get("scope_token") or "").strip()
            agent_id = str(body.get("agent_id") or "").strip()
            if not scope_token:
                raise ValueError("记忆召回测试必须明确选择作用域")
            if not agent_id or agent_id == "*":
                raise ValueError("记忆召回测试必须指定具体 Agent")
            return self._ok(
                await self._memory.debug_recall(
                    query=str(body.get("query") or ""),
                    scope_token=scope_token,
                    kind="memory",
                    agent_id=agent_id,
                    limit=_positive_int(
                        body.get("limit"), self._config.memory_recall_limit, 20
                    ),
                    memory_type=str(body.get("type") or "").strip(),
                )
            )
        if path == "reply-example-action":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            if (
                str(body.get("action") or "update").strip().lower() == "create"
                and not str(body.get("scope_token") or "").strip()
            ):
                raise ValueError("新增回复样例必须明确选择作用域")
            return self._ok(
                await self._memory.apply_reply_example_action(body, actor="web_admin")
            )
        if path == "reply-example-recall-debug":
            if self._memory is None:
                return error_response("记忆服务尚未初始化", status_code=409)
            agent_id = str(body.get("agent_id") or "").strip()
            scope_token = str(body.get("scope_token") or "").strip()
            if not scope_token:
                raise ValueError("回复样例召回测试必须明确选择作用域")
            if not agent_id or agent_id == "*":
                raise ValueError("回复样例召回测试必须指定具体 Agent")
            return self._ok(
                await self._memory.debug_recall(
                    query=str(body.get("query") or ""),
                    scope_token=scope_token,
                    kind="example",
                    agent_id=agent_id,
                    limit=_positive_int(
                        body.get("limit"),
                        self._config.reply_examples_limit or 1,
                        10,
                    ),
                )
            )
        if path != "jargon-action":
            return error_response("未找到该接口", status_code=404)
        entry_id = _required_id(body.get("id"))
        action = str(body.get("action", "")).strip()
        if action == "update_meaning":
            action = "update"
        meaning = str(body.get("meaning", ""))
        if action not in {
            "confirm",
            "reject",
            "update",
            "delete",
            "update_entry",
            "replace_aliases",
            "create_sense",
            "update_sense",
            "confirm_sense",
            "reject_sense",
            "set_preferred",
            "set_preferred_sense",
            "merge_sense",
            "delete_sense",
            "sense_create",
            "sense_update",
            "sense_confirm",
            "sense_reject",
            "sense_preferred",
            "sense_merge",
            "sense_delete",
        }:
            raise ValueError("不支持的词条操作")
        updated = await self._repository.apply_jargon_action(
            entry_id,
            action,
            meaning,
            payload=body,
        )
        if not updated:
            return error_response("词条不存在", status_code=404)
        detail = await self._repository.get_jargon_detail(entry_id)
        return self._ok({"updated": True, "deleted": detail is None, "detail": detail})

    @staticmethod
    def _ok(data: Any):
        from astrbot.api.web import json_response

        return json_response({"success": True, "data": data})
