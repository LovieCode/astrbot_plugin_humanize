from __future__ import annotations

from astrbot_plugin_humanize.humanize.domain.models import (
    MessageContext as PluginContext,
)
from astrbot_plugin_humanize.main import MessageContext as MainContext


def test_message_context_uses_plugin_package_identity() -> None:
    assert MainContext is PluginContext
    assert PluginContext.__module__ == "astrbot_plugin_humanize.humanize.domain.models"
