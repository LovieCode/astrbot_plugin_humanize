"""Prompt template persistence for the Humanize repository."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..domain.prompts import PromptTemplates
from .base import _now

__all__ = ["PromptTemplateRepository"]


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
                "SELECT rule_content, protocol_content, repair_content, "
                "memory_extraction_content, reply_examples_content, updated_at "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            templates = PromptTemplates.from_mapping(
                {
                    "rule": row["rule_content"],
                    "protocol": row["protocol_content"],
                    "repair": row["repair_content"],
                    "memory_extraction": row["memory_extraction_content"],
                    "reply_examples": row["reply_examples_content"],
                }
            )
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
            value: Partial template content keyed by rule, protocol, or repair.
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
                "SELECT rule_content, protocol_content, repair_content, "
                "memory_extraction_content, reply_examples_content "
                "FROM humanize_prompt_templates WHERE id = 1"
            ).fetchone()
            if row is None:
                raise RuntimeError("missing prompt template defaults")
            current = PromptTemplates.from_mapping(
                {
                    "rule": row["rule_content"],
                    "protocol": row["protocol_content"],
                    "repair": row["repair_content"],
                    "memory_extraction": row["memory_extraction_content"],
                    "reply_examples": row["reply_examples_content"],
                }
            )
            updated = PromptTemplates.from_mapping(value, base=current)
            now = _now()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    UPDATE humanize_prompt_templates
                    SET rule_content = ?, protocol_content = ?, repair_content = ?,
                        memory_extraction_content = ?, reply_examples_content = ?,
                        updated_at = ?
                    WHERE id = 1
                    """,
                    (
                        updated.rule,
                        updated.protocol,
                        updated.repair,
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
