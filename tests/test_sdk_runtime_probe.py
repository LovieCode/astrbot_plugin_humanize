"""Exercise Humanize's protocol gate through the AstrBot SDK test runtime.

The production plugin still targets the current in-process AstrBot hook API.
This probe deliberately keeps that integration boundary intact while loading the
real protocol parser from an isolated SDK ``PluginHarness`` worker.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def _plugin_harness():
    sdk_root = Path(os.environ.get("ASTRBOT_SDK_PATH", "")).expanduser()
    sdk_source = sdk_root / "src"
    if not sdk_source.is_dir():
        pytest.skip(
            "set ASTRBOT_SDK_PATH to an astrbot-sdk checkout to run the SDK probe"
        )
    if str(sdk_source) not in sys.path:
        sys.path.insert(0, str(sdk_source))
    from astrbot_sdk.testing import PluginHarness

    return PluginHarness


def _write_protocol_probe(plugin_dir: Path) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(
        "\n".join(
            (
                "_schema_version: 2",
                "name: humanize_sdk_probe",
                "display_name: Humanize SDK Probe",
                "author: tests",
                "repo: AstrBotDevs/humanize-sdk-probe",
                "version: 0.1.0",
                "desc: SDK runtime probe for Humanize protocol parsing",
                "runtime:",
                '  python: "3.10"',
                "components:",
                "  - class: main:HumanizeProtocolProbe",
                "",
            )
        ),
        encoding="utf-8",
    )
    (plugin_dir / "requirements.txt").write_text("", encoding="utf-8")
    (plugin_dir / "main.py").write_text(
        "\n".join(
            (
                "from astrbot_sdk import Context, MessageEvent, Star",
                "from astrbot_sdk.decorators import on_command",
                "from humanize.config import PluginConfig",
                "from humanize.domain.errors import ProtocolValidationError",
                "from humanize.protocol.parser import ProtocolParser",
                "",
                "",
                "class HumanizeProtocolProbe(Star):",
                '    @on_command("humanize_valid")',
                "    async def valid(self, event: MessageEvent, ctx: Context) -> None:",
                "        del ctx",
                "        decision = ProtocolParser(PluginConfig()).parse(",
                '            "<Action>Reply</Action>\\n"',
                '            "<UnknownTerms>[]</UnknownTerms>\\n"',
                '            "<Reply><Message>first</Message><Message>second</Message></Reply>"',
                "        )",
                "        await event.reply(f\"{decision.action.value}:{'|'.join(decision.messages)}\")",
                "",
                '    @on_command("humanize_invalid")',
                "    async def invalid(self, event: MessageEvent, ctx: Context) -> None:",
                "        del ctx",
                "        try:",
                '            ProtocolParser(PluginConfig()).parse("missing control header")',
                "        except ProtocolValidationError as exc:",
                '            await event.reply(f"blocked:{exc.code}")',
                "            return",
                '        await event.reply("unexpectedly accepted")',
                "",
            )
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_sdk_harness_executes_humanize_protocol_gate(tmp_path: Path) -> None:
    """Run current protocol code through the SDK loader and message dispatcher."""
    plugin_harness = _plugin_harness()
    plugin_dir = tmp_path / "humanize_sdk_probe"
    _write_protocol_probe(plugin_dir)

    async with plugin_harness.from_plugin_dir(
        plugin_dir,
        session_id="sdk-test:group:one",
        user_id="user-one",
        platform="test",
        group_id="one",
    ) as harness:
        valid = await harness.dispatch_text("humanize_valid")
        invalid = await harness.dispatch_text("humanize_invalid")

    assert [(item.kind, item.session_id, item.text) for item in valid] == [
        ("text", "sdk-test:group:one", "Reply:first|second"),
    ]
    assert [(item.kind, item.session_id, item.text) for item in invalid] == [
        ("text", "sdk-test:group:one", "blocked:invalid_control_header"),
    ]
