from __future__ import annotations

import logging
from typing import Any, cast

from ..config import PluginConfig
from ..ports import RepositoryPort
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


class WebApi:
    def __init__(
        self,
        repository: RepositoryPort,
        config: PluginConfig,
        control_service: ControlService | None = None,
    ) -> None:
        self._repository = repository
        self._config = config
        self._control = control_service or ControlService(
            cast(SQLiteRepository, repository)
        )

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
        if path == "protocol-logs":
            page = _positive_int(request.query.get("page"), 1, 100_000)
            page_size = _positive_int(request.query.get("page_size"), 20, 100)
            return self._ok(
                await self._repository.list_protocol_logs(
                    page=page, page_size=page_size
                )
            )
        if path == "settings":
            settings = self._config.as_public_dict()
            settings["control_sections"] = [
                "persona",
                "state",
                "behavior",
                "expression",
            ]
            return self._ok(settings)
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
        if path != "jargon-action":
            return error_response("未找到该接口", status_code=404)
        entry_id = _required_id(body.get("id"))
        action = str(body.get("action", "")).strip()
        if action == "update_meaning":
            action = "update"
        meaning = str(body.get("meaning", ""))
        if action not in {"confirm", "reject", "update", "delete"}:
            raise ValueError("不支持的词条操作")
        updated = await self._repository.apply_jargon_action(entry_id, action, meaning)
        if not updated:
            return error_response("词条不存在", status_code=404)
        return self._ok({"updated": True})

    @staticmethod
    def _ok(data: Any):
        from astrbot.api.web import json_response

        return json_response({"success": True, "data": data})
