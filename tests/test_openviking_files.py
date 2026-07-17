from __future__ import annotations

from datetime import UTC, datetime

from humanize.vendor.openviking_core.message import Message, TextPart
from humanize.vendor.openviking_core.session.memory import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
)
from humanize.vendor.openviking_core.session.memory.merge_op import FieldType
from humanize.vendor.openviking_core.session.memory.utils.memory_file_utils import (
    MemoryFileUtils,
)
from humanize.vendor.openviking_core.session.memory.utils.messages import (
    parse_memory_file_with_fields,
)
from humanize.vendor.openviking_core.session.memory.utils.uri import (
    generate_uri,
    validate_uri_template,
)


def test_message_round_trip_uses_private_models_and_safe_peer_ids() -> None:
    message = Message(
        id="message-1",
        role="user",
        parts=[TextPart(text="用户喜欢无糖茶")],
        peer_id="peer-1",
        created_at="2026-07-17T00:00:00+00:00",
    )

    restored = Message.from_dict(message.to_dict())
    unsafe = Message.from_dict(
        {
            "id": "message-2",
            "role": "user",
            "content": "legacy",
            "peer_id": "../unsafe",
        }
    )

    assert restored.content == "用户喜欢无糖茶"
    assert restored.peer_id == "peer-1"
    assert restored.estimated_tokens > 0
    assert unsafe.content == "legacy"
    assert unsafe.peer_id is None


def test_memory_file_round_trip_preserves_levels_and_metadata() -> None:
    updated_at = datetime(2026, 7, 17, tzinfo=UTC)
    memory_file = MemoryFile(
        uri="viking://user/demo/memories/preferences/tea.md",
        content="用户喜欢无糖茶",
        memory_type="preference",
        extra_fields={
            "version": 3,
            "abstract": "饮料偏好",
            "overview": "用户的饮料选择",
            "updated_at": updated_at,
        },
    )

    serialized = MemoryFileUtils.write(memory_file)
    restored = MemoryFileUtils.read(serialized, uri=memory_file.uri)

    assert restored.content == "用户喜欢无糖茶"
    assert restored.memory_type == "preference"
    assert restored.extra_fields["abstract"] == "饮料偏好"
    assert restored.extra_fields["overview"] == "用户的饮料选择"
    assert restored.extra_fields["version"] == 3
    assert restored.extra_fields["updated_at"] == updated_at


def test_invalid_memory_metadata_fails_open_to_visible_content() -> None:
    parsed = parse_memory_file_with_fields(
        "Visible memory\n\n<!-- MEMORY_FIELDS\n{broken\n-->"
    )

    assert parsed == {"content": "Visible memory"}


def test_memory_uri_generation_uses_declared_schema_fields() -> None:
    schema = MemoryTypeSchema(
        memory_type="preference",
        directory="viking://user/{{ user_space }}/memories/preferences",
        filename_template="{{ topic }}.md",
        fields=[MemoryField(name="topic", field_type=FieldType.STRING)],
    )

    assert validate_uri_template(schema) is True
    assert generate_uri(schema, {"topic": "tea"}, user_space="demo") == (
        "viking://user/demo/memories/preferences/tea.md"
    )
