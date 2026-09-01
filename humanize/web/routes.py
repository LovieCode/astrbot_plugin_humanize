from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path
from typing import Any

from ..config import PluginConfig
from ..domain.prompts import PROMPT_TEMPLATE_SPEC_BY_KEY, PromptTemplates
from ..memory import ChatMemoryService
from ..ports import RepositoryPort
from ..protocol.envelope import EnvelopeBuilder
from ..provider_catalog import ProviderCatalog
from ..repositories.policy import DEFAULT_POLICY_MODE, GLOBAL_POLICY_SCOPE

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


_TYPE_LABELS = {
    bool: "bool",
    int: "int",
    float: "float",
    str: "string",
    list: "list",
}


def _validate_settings_values(values: dict[str, Any]) -> dict[str, Any]:
    """Validate one settings payload against the public config whitelist.

    Args:
        values: Flat key to value mapping submitted by the settings form.

    Returns:
        Validated flat key to value mapping.

    Raises:
        ValueError: If a key is unknown or its value has the wrong type.
    """
    public = PluginConfig().as_public_dict()
    validated: dict[str, Any] = {}
    for key, value in values.items():
        if key not in public:
            raise ValueError(f"未知配置项: {key}")
        expected = type(public[key])
        if not isinstance(value, expected):
            raise ValueError(
                f"配置项 {key} 的类型错误: 期望是 {_TYPE_LABELS.get(expected, expected.__name__)}, "
                f"得到了 {type(value).__name__}"
            )
        validated[key] = value
    return validated


def _write_plugin_config(values: dict[str, Any]) -> None:
    """Persist validated settings into the plugin config file without reloading.

    The plugin schema groups every key under general, reply_control or memory;
    runtime code reads the flattened mapping, so flat values must be written
    back into their declared groups.

    Args:
        values: Validated flat key to value mapping.

    Raises:
        RuntimeError: If the schema file or config file cannot be accessed.
    """
    from astrbot.core.config.astrbot_config import AstrBotConfig
    from astrbot.core.utils.astrbot_path import get_astrbot_config_path

    schema_path = Path(__file__).resolve().parents[2] / "_conf_schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("无法读取插件配置 schema") from exc
    config = AstrBotConfig(
        config_path=(
            Path(get_astrbot_config_path()) / "astrbot_plugin_humanize_config.json"
        ),
        schema=schema,
    )

    def assign(section: dict[str, Any], key: str, value: Any) -> bool:
        """Place one value into the matching nested schema group."""
        for nested in section.values():
            if not isinstance(nested, dict):
                continue
            if key in nested:
                nested[key] = value
                return True
            if assign(nested, key, value):
                return True
        return False

    for key, value in values.items():
        if not assign(config, key, value):
            raise ValueError(f"未知配置项: {key}")
    config.save_config()


_PAGE_DIR = Path(__file__).resolve().parents[2] / "pages" / "humanize"
_PAGE_FILES = {
    "": "dashboard.html",
    "dashboard.html": "dashboard.html",
    "memory.html": "memory.html",
    "jargon.html": "jargon.html",
    "examples.html": "examples.html",
    "context.html": "context.html",
    "prompts.html": "prompts.html",
    "settings.html": "settings.html",
}


def _static_path(path: str) -> Path | None:
    """Resolve one static asset inside the page directory without traversal.

    Args:
        path: URL path relative to the page root.

    Returns:
        Resolved page asset, or ``None`` when the path escapes the root.
    """
    if not path or path.startswith(("/", "\\")):
        return None
    candidate = (_PAGE_DIR / path).resolve()
    try:
        candidate.relative_to(_PAGE_DIR)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class WebApi:
    def __init__(
        self,
        repository: RepositoryPort,
        config: PluginConfig,
        envelope: EnvelopeBuilder | None = None,
        provider_catalog: ProviderCatalog | None = None,
        memory: ChatMemoryService | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._envelope = envelope or EnvelopeBuilder(config)
        self._provider_catalog = provider_catalog
        self._memory = memory

    async def dispatch(self, subpath: str = ""):
        from astrbot.api.web import error_response, file_response, request

        path = (subpath or "").strip("/")
        try:
            if request.method == "GET":
                if path in _PAGE_FILES:
                    return file_response(_PAGE_DIR / _PAGE_FILES[path])
                static = _static_path(path)
                if static is not None:
                    return file_response(static)
                return await self._handle_get(path)
            if request.method == "POST":
                return await self._handle_post(path)
            return error_response("不支持的请求方法", status_code=405)
        except ValueError as exc:
            logger.exception(
                "[Humanize] Web API bad request: %s %s: %s\n%s",
                request.method,
                path,
                exc,
                traceback.format_exc(),
            )
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

        if path == "policy":
            rows = await self._repository.list_group_policies()
            sessions = await self._repository.list_known_sessions()
            names = {
                str(row.get("scope_id") or ""): str(row.get("display_name") or "")
                for row in sessions
            }

            def display_name_for(scope_id: str) -> str:
                """按完整标识或末尾群号把已知会话名对到策略行上。"""
                exact = names.get(scope_id)
                if exact:
                    return exact
                for known_scope, known_name in names.items():
                    if known_scope == scope_id or known_scope.endswith(f":{scope_id}"):
                        return known_name
                return ""

            global_mode = DEFAULT_POLICY_MODE
            global_speak_probability: int | None = None
            groups: list[dict[str, Any]] = []
            for row in rows:
                scope_id = str(row.get("scope_id") or "").strip()
                if not scope_id:
                    continue
                if scope_id == GLOBAL_POLICY_SCOPE:
                    mode = str(row.get("mode") or "").strip()
                    if mode:
                        global_mode = mode
                    probability = row.get("speak_probability")
                    if probability is not None:
                        global_speak_probability = int(probability)
                    continue
                probability = row.get("speak_probability")
                groups.append(
                    {
                        "scope_id": scope_id,
                        "mode": str(row.get("mode") or ""),
                        "speak_probability": (
                            int(probability) if probability is not None else None
                        ),
                        "display_name": display_name_for(scope_id),
                        "updated_at": str(row.get("updated_at") or ""),
                    }
                )
            known_sessions = [
                {"scope_id": scope_id, "display_name": display_name}
                for scope_id, display_name in names.items()
                if scope_id
            ]
            return self._ok(
                {
                    "global_mode": global_mode,
                    "global_speak_probability": global_speak_probability,
                    "groups": groups,
                    "known_sessions": known_sessions,
                    "proactive_keywords": list(self._config.proactive_keywords),
                }
            )
        if path == "overview":
            return self._ok(await self._repository.get_overview())
        if path == "usage-overview":
            days = _positive_int(request.query.get("days"), 7, 90)
            return self._ok(await self._repository.get_usage_overview(days=days))
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
            return self._ok(self._config.as_public_dict())
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
        if path == "prompt-template-audit":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._repository.list_prompt_template_audit(
                    page=page, page_size=page_size
                )
            )
        return error_response("未找到该接口", status_code=404)

    async def _handle_post(self, path: str):
        from astrbot.api.web import error_response, request

        body = await request.json(default={})
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")
        if path == "settings":
            raw_values = body.get("values")
            if not isinstance(raw_values, dict) or not raw_values:
                raise ValueError("settings 请求必须包含非空的 values 对象")
            validated = _validate_settings_values(raw_values)
            _write_plugin_config(validated)
            # 同步更新内存配置（frozen dataclass，用 object.__setattr__ 原地修改，
            # 保持与插件主逻辑同一对象引用），使随后的读回立即反映新值。
            try:
                for key, value in validated.items():
                    if hasattr(self._config, key):
                        object.__setattr__(self._config, key, value)
            except Exception:
                logger.exception("[Humanize] settings in-memory refresh failed")
            return self._ok({"updated": list(validated), "restart_required": True})
        if path == "policy-keywords":
            # 复用 settings 的校验与落盘路径（白名单校验 + 分组写回 + 内存
            # object.__setattr__ 刷新），保证关键词与设置页改法行为一致。
            keywords = body.get("proactive_keywords")
            if not isinstance(keywords, list):
                raise ValueError("proactive_keywords 必须是字符串列表")
            validated = _validate_settings_values({"proactive_keywords": keywords})
            _write_plugin_config(validated)
            try:
                for key, value in validated.items():
                    if hasattr(self._config, key):
                        object.__setattr__(self._config, key, tuple(value))
            except Exception:
                logger.exception("[Humanize] policy-keywords in-memory refresh failed")
            return self._ok(
                {"proactive_keywords": list(self._config.proactive_keywords)}
            )
        if path == "policy-set":
            scope_id = str(body.get("scope_id") or "").strip()
            mode = str(body.get("mode") or "").strip()
            await self._repository.set_group_policy_mode(scope_id=scope_id, mode=mode)
            if "speak_probability" in body:
                # 期望发言概率（软性提示）：显式传入才改，null 表示清除回退。
                await self._repository.set_group_speak_probability(
                    scope_id=scope_id,
                    probability=body.get("speak_probability"),
                )
            return self._ok({"scope_id": scope_id, "mode": mode})
        if path == "policy-clear":
            scope_id = str(body.get("scope_id") or "").strip()
            if scope_id == GLOBAL_POLICY_SCOPE:
                raise ValueError("全局默认模式请直接修改，不能清除")
            await self._repository.clear_group_policy(scope_id=scope_id)
            return self._ok({"cleared": scope_id})
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
        action = str(body.get("action", "")).strip()
        if action == "update_meaning":
            action = "update"
        if action == "create_entry":
            if not await self._repository.apply_jargon_action(0, action, payload=body):
                return error_response("词条创建失败", status_code=500)
            listing = await self._repository.list_jargons(
                search=str(body.get("term") or "").strip(),
                status="",
                scope_id=str(body.get("scope_id") or "").strip(),
                scope_type=str(body.get("scope_type") or "").strip(),
                page=1,
                page_size=1,
            )
            created = listing["items"][0] if listing["items"] else None
            detail = (
                await self._repository.get_jargon_detail(int(created["id"]))
                if created is not None
                else None
            )
        else:
            entry_id = _required_id(body.get("id"))
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
