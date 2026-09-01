from __future__ import annotations

import json
from pathlib import Path

from astrbot_plugin_humanize.humanize.config import PluginConfig

from astrbot.core.config.astrbot_config import AstrBotConfig

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _schema() -> dict[str, object]:
    """Load the plugin schema used by AstrBot's configuration dialog.

    Returns:
        Parsed grouped configuration schema.
    """
    return json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


def _items(schema: dict[str, object], section: str) -> dict[str, object]:
    """Return the items declared for one schema section.

    Args:
        schema: Parsed plugin configuration schema.
        section: Section key to inspect.

    Returns:
        The section's item mapping.
    """
    metadata = schema[section]
    assert isinstance(metadata, dict)
    items = metadata["items"]
    assert isinstance(items, dict)
    return items


def _nested_items(items: dict[str, object], key: str) -> dict[str, object]:
    """Return items nested under an object-typed configuration value.

    Args:
        items: Parent section item mapping.
        key: Nested object key to inspect.

    Returns:
        Nested configuration item mapping.
    """
    metadata = items[key]
    assert isinstance(metadata, dict)
    nested_items = metadata["items"]
    assert isinstance(nested_items, dict)
    return nested_items


def test_schema_uses_typed_provider_selectors() -> None:
    """Provider fields must open the matching AstrBot provider picker."""
    schema = _schema()
    memory = _items(schema, "memory")

    assert memory["memory_extraction_provider_id"]["_special"] == "select_provider"
    assert (
        memory["memory_embedding_provider_id"]["_special"]
        == "select_provider:embedding"
    )
    assert memory["memory_rerank_provider_id"]["_special"] == "select_provider:rerank"


def test_schema_keeps_common_controls_visible_and_details_collapsed() -> None:
    """Keep the first-run form focused while retaining every tuning control."""
    schema = _schema()
    reply_control = _items(schema, "reply_control")
    memory = _items(schema, "memory")
    reply_examples = _nested_items(memory, "reply_examples")
    advanced_fields = ("min_confidence_for_injection",)

    assert reply_control["protocol_injection_mode"]["labels"] == [
        "仅用户消息（推荐）",
        "用户消息 + System",
    ]
    assert _items(schema, "general")["max_message_chars"]["slider"] == {
        "min": 1,
        "max": 200,
        "step": 1,
    }
    assert _items(schema, "general")["message_interval_seconds"]["slider"] == {
        "min": 0.0,
        "max": 10.0,
        "step": 0.1,
    }
    assert all(reply_control[field]["collapsed"] is True for field in advanced_fields)
    assert memory["memory_rerank_provider_id"]["collapsed"] is True
    assert memory["memory_recall_timeout_seconds"]["collapsed"] is True
    assert reply_examples["reply_examples_min_quality"]["collapsed"] is True
    assert "collapsed" not in memory["memory_enabled"]


def test_grouped_schema_defaults_are_flattened_for_runtime_config(
    tmp_path: Path,
) -> None:
    """Grouped UI values must preserve the flat runtime configuration contract."""
    schema = _schema()
    ui_config = AstrBotConfig(
        config_path=str(tmp_path / "humanize_config.json"),
        schema=schema,
    )
    config = PluginConfig.from_mapping(ui_config)

    assert list(ui_config) == ["general", "reply_control", "memory", "proactive"]
    assert config.max_message_chars == 10
    assert config.message_interval_seconds == 0.8
    assert config.protocol_injection_mode == "user"
    assert config.memory_enabled is True
    assert config.memory_embedding_provider_id == ""
    # 群聊许可与主动模式已迁移到 WebUI 群聊策略页（humanize.db），配置只留节奏与关键词
    assert config.proactive_window_initial_seconds == 10
    assert config.proactive_window_max_seconds == 300
    assert config.proactive_post_reply_cooldown_seconds == 20


def test_provider_ids_preserve_astrbot_path_segments() -> None:
    """Keep provider IDs emitted by AstrBot's provider picker intact."""
    provider_id = "siliconflow/deepseek-ai/DeepSeek-V4-Flash"

    config = PluginConfig.from_mapping(
        {
            "memory": {
                "memory_extraction_provider_id": provider_id,
                "memory_embedding_provider_id": provider_id,
                "memory_rerank_provider_id": provider_id,
            }
        }
    )

    assert config.memory_extraction_provider_id == provider_id
    assert config.memory_embedding_provider_id == provider_id
    assert config.memory_rerank_provider_id == provider_id


def test_provider_ids_reject_control_characters_and_allow_absent_values() -> None:
    """Keep invalid provider identifiers from reaching runtime provider lookup."""
    config = PluginConfig.from_mapping(
        {
            "memory": {
                "memory_extraction_provider_id": "provider\ninvalid",
                "memory_embedding_provider_id": "\x00embedding",
                "memory_rerank_provider_id": None,
            }
        }
    )

    assert config.memory_extraction_provider_id == ""
    assert config.memory_embedding_provider_id == ""
    assert config.memory_rerank_provider_id == ""
