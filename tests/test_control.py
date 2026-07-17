from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from humanize.repositories.sqlite import _SCHEMA_VERSION, SQLiteRepository
from humanize.services.control import ControlService


async def _service(db_path: Path) -> ControlService:
    repository = SQLiteRepository(db_path)
    await repository.initialize()
    return ControlService(repository)


def test_control_defaults_share_humanize_database(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = await _service(tmp_path / "humanize.db")
        features = await service.get_features()

        assert features["persona"]["name"] == "小助手"
        assert features["state"]["mood"] == 0.5
        assert features["behavior"]["enabled"] is True
        assert features["expression"]["integration_status"] == "disabled"

        with sqlite3.connect(tmp_path / "humanize.db") as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert "humanize_state" in tables
            assert "humanize_control_audit" in tables
            assert not any("relationship" in table for table in tables)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION

    asyncio.run(scenario())


def test_control_update_clamps_state_and_records_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = await _service(tmp_path / "humanize.db")
        updated = await service.update_section(
            "state",
            {"mood": 0.9, "energy": 3, "interest": -1, "stress": 0.4567},
            reason="manual calibration",
        )
        assert updated["mood"] == 0.9
        assert updated["energy"] == 1.0
        assert updated["interest"] == 0.0
        assert updated["stress"] == 0.457

        audit = await service.list_audit(page=1, page_size=20)
        assert audit["total"] == 1
        assert audit["items"][0]["section"] == "state"
        assert audit["items"][0]["action"] == "update"
        assert audit["items"][0]["reason"] == "manual calibration"

    asyncio.run(scenario())


def test_control_boolean_values_survive_repository_reopen(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "humanize.db"
        first = await _service(db_path)
        await first.update_section(
            "behavior",
            {"enabled": False, "allow_proactive": True},
        )
        await first.update_section("expression", {"enabled": True, "mode": "observe"})

        second = await _service(db_path)
        behavior = await second.get_section("behavior")
        expression = await second.get_section("expression")
        assert behavior["enabled"] is False
        assert behavior["allow_proactive"] is True
        assert expression["enabled"] is True

    asyncio.run(scenario())


def test_control_reset_is_auditable_and_validates_expression_mode(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = await _service(tmp_path / "humanize.db")
        await service.update_section("persona", {"name": "测试助手"})
        await service.update_section("expression", {"mode": "inject", "enabled": True})

        with pytest.raises(ValueError, match="expression mode"):
            await service.update_section("expression", {"mode": "invalid"})

        reset = await service.reset("all", "restore defaults")
        assert set(reset) == {"persona", "state", "behavior", "expression"}
        assert reset["persona"]["name"] == "小助手"
        assert reset["expression"]["enabled"] is False

        audit = await service.list_audit(page=1, page_size=20)
        assert audit["total"] == 6
        assert sum(item["action"] == "reset" for item in audit["items"]) == 4

    asyncio.run(scenario())
