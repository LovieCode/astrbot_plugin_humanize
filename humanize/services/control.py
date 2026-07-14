from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..repositories.sqlite import SQLiteRepository


class ControlService:
    """Coordinate validated WebUI control updates and their audit trail."""

    _SECTIONS = {"persona", "state", "behavior", "expression"}

    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    async def get_section(self, section: str) -> dict[str, Any]:
        """Return one supported control section."""
        self._check_section(section)
        return await self._repository.get_control_section(section)

    async def get_features(self) -> dict[str, Any]:
        """Return all controls for the WebUI feature panel."""
        return await self._repository.get_control_overview()

    async def update_section(
        self, section: str, payload: Mapping[str, Any], *, reason: str = "web update"
    ) -> dict[str, Any]:
        """Merge and persist one section, preserving fields omitted by the UI."""
        self._check_section(section)
        if not isinstance(payload, Mapping):
            raise ValueError("request body must be a JSON object")
        current = await self._repository.get_control_section(section)
        merged = {**current, **dict(payload)}
        return await self._repository.update_control_section(
            section, merged, reason=reason
        )

    async def reset(self, section: str, reason: str) -> dict[str, Any]:
        """Reset one section or all sections and return their defaults."""
        if section != "all":
            self._check_section(section)
        clean_reason = str(reason or "manual reset").strip() or "manual reset"
        return await self._repository.reset_control(
            section, actor="web_admin", reason=clean_reason
        )

    async def list_audit(self, *, page: int, page_size: int) -> dict[str, Any]:
        """Return paginated control changes for audit views."""
        return await self._repository.list_control_audit(page=page, page_size=page_size)

    @classmethod
    def _check_section(cls, section: str) -> None:
        if section not in cls._SECTIONS:
            raise ValueError("unsupported control section")
