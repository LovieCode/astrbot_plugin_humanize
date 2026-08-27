from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot_plugin_humanize.humanize.snapshots import (
    serialize_attachment_reference,
    serialize_llm_response,
    serialize_provider_request,
)

from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.tool import FunctionTool, ToolSet


@dataclass
class _Attachment:
    name: str
    data: bytes


class _ExplosiveRepr:
    def __repr__(self) -> str:
        raise AssertionError("snapshot serialization must not call repr")


def test_request_snapshot_keeps_dynamic_fields_without_calling_repr() -> None:
    request = ProviderRequest(prompt="hello")
    request.external_plugin_data = {
        "nested": [1, float("nan"), _ExplosiveRepr()],
        7: "numeric key",
    }

    snapshot, complete = serialize_provider_request(request)

    assert complete is False
    assert "external_plugin_data" in snapshot["fields"]
    external = snapshot["fields"]["external_plugin_data"]
    assert external["type"] == "mapping"
    assert external["entries"][1] == {"key": 7, "value": "numeric key"}
    assert external["entries"][0]["key"] == "nested"
    unknown = external["entries"][0]["value"][2]
    assert unknown["serialization_error"] == "unsupported_type"
    assert snapshot["serialization_issues"]
    json.dumps(snapshot, ensure_ascii=False, allow_nan=False)


def test_request_snapshot_marks_cycles_and_tool_handlers_as_excluded() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    async def handler() -> str:
        raise AssertionError("tool handler must never run during serialization")

    request = ProviderRequest(
        prompt="hello",
        contexts=[{"role": "user", "content": cyclic}],
        func_tool=ToolSet(
            [
                FunctionTool(
                    name="lookup",
                    description="Lookup data",
                    parameters={"type": "object", "properties": {}},
                    handler=handler,
                )
            ]
        ),
    )

    snapshot, complete = serialize_provider_request(request)

    assert complete is False
    assert (
        snapshot["fields"]["contexts"][0]["content"][0]["serialization_error"]
        == "cycle_detected"
    )
    tool = snapshot["fields"]["func_tool"]["tools"][0]
    assert tool["handler"]["serialization_error"] == "callable_excluded"
    assert tool["name"] == "lookup"
    assert tool["parameters"] == {"type": "object", "properties": {}}
    json.dumps(snapshot, ensure_ascii=False, allow_nan=False)


def test_request_snapshot_redacts_credentials_but_keeps_field_location() -> None:
    request = ProviderRequest(prompt="hello")
    request.api_key = "must-not-be-persisted"
    request.external_plugin_data = {
        "headers": {"Authorization": "Bearer must-not-be-persisted"},
        "input_tokens": 123,
    }

    snapshot, complete = serialize_provider_request(request)

    assert complete is False
    assert snapshot["fields"]["api_key"]["serialization_error"] == (
        "sensitive_value_redacted"
    )
    headers = snapshot["fields"]["external_plugin_data"]["headers"]
    assert headers["Authorization"]["serialization_error"] == (
        "sensitive_value_redacted"
    )
    assert snapshot["fields"]["external_plugin_data"]["input_tokens"] == 123
    serialized = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    assert "must-not-be-persisted" not in serialized


def test_request_snapshot_redacts_sensitive_values_in_mixed_key_mappings() -> None:
    request = ProviderRequest(prompt="hello")
    request.external_plugin_data = {
        "proxy-authorization": "Bearer mixed-map-secret",
        "nested": {"provider_token": "nested-secret", "output_tokens": 17},
        7: "numeric key",
    }

    snapshot, complete = serialize_provider_request(request)

    assert complete is False
    external = snapshot["fields"]["external_plugin_data"]
    assert external["type"] == "mapping"
    assert external["entries"][0]["value"]["serialization_error"] == (
        "sensitive_value_redacted"
    )
    nested = external["entries"][1]["value"]
    assert nested["provider_token"]["serialization_error"] == (
        "sensitive_value_redacted"
    )
    assert nested["output_tokens"] == 17
    serialized = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
    assert "mixed-map-secret" not in serialized
    assert "nested-secret" not in serialized


def test_response_snapshot_is_frozen_before_response_mutation() -> None:
    response = LLMResponse(
        role="assistant",
        completion_text="raw provider response",
        reasoning_content="raw reasoning",
    )
    response.external_provider_field = {"trace": "original"}

    snapshot, complete = serialize_llm_response(response)
    response.completion_text = "rewritten response"
    response.external_provider_field["trace"] = "mutated"

    assert complete is True
    assert snapshot is not None
    assert snapshot["fields"]["completion_text"] == "raw provider response"
    assert snapshot["fields"]["external_provider_field"] == {"trace": "original"}
    assert snapshot["fields"]["reasoning_content"] == "raw reasoning"
    json.dumps(snapshot, ensure_ascii=False, allow_nan=False)


def test_attachment_reference_hashes_binary_without_persisting_payload() -> None:
    reference, complete = serialize_attachment_reference(
        _Attachment(name="image.png", data=b"private image bytes")
    )

    assert complete is True
    assert reference["type"].endswith("_Attachment")
    assert len(reference["content_hash"]) == 64
    metadata = reference["metadata"]["fields"]
    assert metadata["name"] == "image.png"
    assert metadata["data"]["encoding"] == "base64"
    assert "data" not in metadata["data"]
    json.dumps(reference, ensure_ascii=False, allow_nan=False)
