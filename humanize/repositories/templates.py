"""Prompt template persistence for the Humanize repository."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..domain.prompts import PROMPT_TEMPLATE_SPECS, PromptTemplates
from .base import _json_value, _now

__all__ = ["PromptTemplateRepository"]

logger = logging.getLogger("astrbot")

_template_warning_logged = False


def _stored_templates(row: sqlite3.Row) -> PromptTemplates:
    """Parse stored templates per key, degrading invalid keys to defaults.

    旧库可能存有引用已删除变量的模板（如 v2 之前的 ``{{version}}``）。
    校验按单键进行：只有失效的键回退为内置默认，其余自定义模板保持可用；
    写入路径仍走严格校验。告警每个进程只发一次，避免逐请求刷屏。
    """
    global _template_warning_logged
    values: dict[str, str] = {}
    for spec in PROMPT_TEMPLATE_SPECS:
        try:
            validated = PromptTemplates.from_mapping(
                {spec.key: row[f"{spec.key}_content"]}
            )
        except ValueError as exc:
            if not _template_warning_logged:
                logger.warning(
                    "[Humanize] stored prompt template %s is invalid (%s); "
                    "using built-in defaults for it",
                    spec.key,
                    exc,
                )
                _template_warning_logged = True
            values[spec.key] = getattr(PromptTemplates(), spec.key)
        else:
            values[spec.key] = getattr(validated, spec.key)
    return PromptTemplates(**values)


class PromptTemplateRepository:
    """Domain mixin: editable prompt template storage."""

    async def get_prompt_templates(self) -> dict[str, Any]:
        """Read the editable prompt templates from the shared database.

        Returns:
            Raw template content and the shared update timestamp.

        Raises:
            RuntimeError: If prompt template defaults are missing.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT rule_content, protocol_content, "
                "memory_extraction_content, reply_examples_content, updated_at "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            templates = _stored_templates(row)
            return {
                "templates": templates.as_dict(),
                "updated_at": str(row["updated_at"]),
            }

        return await self._run(operation)

    async def update_prompt_templates(
        self,
        value: dict[str, Any],
        *,
        actor: str = "web_admin",
        reason: str = "web update",
        action: str = "update",
    ) -> dict[str, Any]:
        """Merge prompt templates and record a dedicated audit entry.

        Args:
            value: Partial template content keyed by rule, protocol,
                memory_extraction, or reply_examples.
            actor: Audit actor label.
            reason: Audit reason.
            action: Audit action, either update or reset.

        Returns:
            Complete persisted templates and their update timestamp.

        Raises:
            ValueError: If the template payload or action is invalid.
            RuntimeError: If prompt template defaults are missing.
        """
        if action not in {"update", "reset"}:
            raise ValueError("unsupported prompt template action")
        clean_reason = str(reason or "web update").strip()[:500]
        clean_actor = str(actor or "web_admin").strip()[:120] or "web_admin"

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT rule_content, protocol_content, "
                "memory_extraction_content, reply_examples_content "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            current = _stored_templates(row)
            updated = PromptTemplates.from_mapping(value, base=current)
            now = _now()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # repair_content 是历史遗留列（协议修复功能已移除），
                # 不再读取也不再更新，仅保留原值满足 NOT NULL 约束。
                conn.execute(
                    """
                    UPDATE humanize_prompt_templates
                    SET rule_content = ?, protocol_content = ?,
                        memory_extraction_content = ?, reply_examples_content = ?,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        updated.rule,
                        updated.protocol,
                        updated.memory_extraction,
                        updated.reply_examples,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO humanize_prompt_template_audit (
                        action, actor, reason, before_json, after_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action,
                        clean_actor,
                        clean_reason,
                        json.dumps(
                            current.as_dict(), ensure_ascii=False, sort_keys=True
                        ),
                        json.dumps(
                            updated.as_dict(), ensure_ascii=False, sort_keys=True
                        ),
                        now,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return {"templates": updated.as_dict(), "updated_at": now}

        return await self._run(operation)

    async def list_prompt_template_audit(
        self, *, page: int, page_size: int
    ) -> dict[str, Any]:
        """Return paginated prompt template audit entries newest first.

        Args:
            page: One-based page number.
            page_size: Bounded result count.

        Returns:
            Paginated audit items with decoded before/after snapshots.
        """

        def operation(conn: sqlite3.Connection) -> dict[str, Any]:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM humanize_prompt_template_audit"
                ).fetchone()["count"]
            )
            rows = conn.execute(
                """
                SELECT id, action, actor, reason, before_json, after_json,
                       created_at
                FROM humanize_prompt_template_audit
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["before"] = _json_value(str(item.pop("before_json", "{}")), {})
                item["after"] = _json_value(str(item.pop("after_json", "{}")), {})
                items.append(item)
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        return await self._run(operation)
