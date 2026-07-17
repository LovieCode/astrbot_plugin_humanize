from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import Any

_MAX_DEPTH = 64
_SAFE_DUMP_MODULES = ("anthropic.", "astrbot.", "google.", "mcp.", "openai.")
_SENSITIVE_FIELD_NAMES = {
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "access_key",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "private_key",
    "secret_key",
    "signing_key",
    "session_key",
    "auth",
    "authentication",
    "proxy_authorization",
    "set_cookie",
    "token",
    "bearer",
    "credential",
    "credentials",
}


def serialize_attachment_reference(component: Any) -> tuple[dict[str, Any], bool]:
    """Serialize a message attachment as a bounded, non-binary reference.

    Args:
        component: AstrBot message component that is not plain text.

    Returns:
        A stable reference containing a content hash and safe metadata, plus a
        flag indicating whether the component could be serialized without a
        fallback. Binary payloads are never persisted in the reference.
    """
    issues: list[dict[str, str]] = []
    snapshot, complete = _snapshot_value(
        component,
        depth=0,
        seen=set(),
        path="$",
        issues=issues,
    )
    try:
        canonical = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        canonical = _type_name(component)
        complete = False
    reference: dict[str, Any] = {
        "type": _type_name(component),
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "metadata": _bounded_attachment_value(snapshot),
    }
    if issues:
        reference["serialization_issues"] = issues[:16]
    return reference, complete


def _bounded_attachment_value(value: Any, *, depth: int = 0) -> Any:
    """Remove binary payloads and bound attachment metadata before persistence."""
    if depth > 8:
        return {"type": "truncated"}
    if isinstance(value, Mapping):
        if value.get("encoding") == "base64" and "data" in value:
            data = value.get("data")
            raw = (
                data.encode("ascii", errors="replace") if isinstance(data, str) else b""
            )
            return {
                "type": value.get("type", "binary"),
                "encoding": "base64",
                "size": (len(raw) * 3) // 4 if raw else 0,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        bounded: dict[str, Any] = {}
        for key, item in value.items():
            clean_key = str(key)
            if any(
                token in clean_key.lower()
                for token in ("authorization", "cookie", "password", "secret", "token")
            ):
                continue
            bounded[clean_key] = _bounded_attachment_value(item, depth=depth + 1)
        return bounded
    if isinstance(value, (list, tuple)):
        return [_bounded_attachment_value(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, str) and len(value) > 512:
        raw = value.encode("utf-8", errors="replace")
        return {
            "prefix": value[:512],
            "length": len(value),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    return {"type": _type_name(value)}


def serialize_provider_request(request: Any) -> tuple[dict[str, Any], bool]:
    """Serialize every declared and dynamic provider-request field.

    Args:
        request: Mutable AstrBot ``ProviderRequest`` at the end of request hooks.

    Returns:
        JSON-compatible request data and whether every runtime value was retained
        without a safe-serialization fallback.
    """
    issues: list[dict[str, str]] = []
    values: dict[str, Any] = {}
    complete = True
    for name in _field_names(request):
        value = getattr(request, name, None)
        if _is_sensitive_field_name(name):
            serialized, value_complete = _redacted_value(
                value,
                path=f"$.fields.{name}",
                issues=issues,
            )
        elif name == "func_tool" and value is not None:
            serialized, value_complete = _snapshot_tool_set(
                value,
                path=f"$.fields.{name}",
                issues=issues,
            )
        else:
            serialized, value_complete = _snapshot_value(
                value,
                depth=0,
                seen=set(),
                path=f"$.fields.{name}",
                issues=issues,
            )
        values[name] = serialized
        complete = complete and value_complete
    snapshot = {
        "type": _type_name(request),
        "fields": values,
        "field_complete": True,
        "value_complete": complete,
        "serialization_issues": issues,
    }
    return snapshot, complete


def serialize_llm_response(response: Any) -> tuple[dict[str, Any] | None, bool]:
    """Serialize an untouched AstrBot LLM response without truncation.

    Args:
        response: Provider response received by the response hook, or ``None``.

    Returns:
        JSON-compatible response data and a lossless-serialization flag.
    """
    if response is None:
        return None, True
    issues: list[dict[str, str]] = []
    values: dict[str, Any] = {}
    complete = True
    for name in _field_names(response):
        value = getattr(response, name, None)
        if _is_sensitive_field_name(name):
            serialized, value_complete = _redacted_value(
                value,
                path=f"$.fields.{name}",
                issues=issues,
            )
        else:
            serialized, value_complete = _snapshot_value(
                value,
                depth=0,
                seen=set(),
                path=f"$.fields.{name}",
                issues=issues,
            )
        values[name] = serialized
        complete = complete and value_complete
    completion_text, text_complete = _snapshot_value(
        getattr(response, "completion_text", ""),
        depth=0,
        seen=set(),
        path="$.fields.completion_text",
        issues=issues,
    )
    values["completion_text"] = completion_text
    complete = complete and text_complete
    snapshot = {
        "type": _type_name(response),
        "fields": values,
        "field_complete": True,
        "value_complete": complete,
        "serialization_issues": issues,
    }
    return snapshot, complete


def _snapshot_tool_set(
    tool_set: Any,
    *,
    path: str,
    issues: list[dict[str, str]],
) -> tuple[dict[str, Any], bool]:
    """Capture provider-visible tool schemas without traversing handlers.

    Args:
        tool_set: AstrBot ToolSet-like object.
        path: JSON-style location used in serialization diagnostics.
        issues: Mutable diagnostic collection for incomplete values.

    Returns:
        Tool schema snapshot and whether no runtime-only values were excluded.
    """
    tools_value = getattr(tool_set, "tools", None)
    if not isinstance(tools_value, Sequence) or isinstance(tools_value, str):
        issues.append(
            {
                "path": f"{path}.tools",
                "type": _type_name(tools_value),
                "code": "invalid_tool_collection",
            }
        )
        return {"type": _type_name(tool_set), "tools": []}, False

    tools: list[dict[str, Any]] = []
    complete = True
    for index, tool in enumerate(tools_value):
        tool_path = f"{path}.tools[{index}]"
        item: dict[str, Any] = {"type": _type_name(tool)}
        for name in (
            "name",
            "description",
            "parameters",
            "active",
            "is_background_task",
            "handler_module_path",
        ):
            serialized, field_complete = _snapshot_value(
                getattr(tool, name, None),
                depth=0,
                seen=set(),
                path=f"{tool_path}.{name}",
                issues=issues,
            )
            item[name] = serialized
            complete = complete and field_complete
        handler = getattr(tool, "handler", None)
        if handler is None:
            item["handler"] = None
        else:
            item["handler"] = {
                "type": _type_name(handler),
                "serialization_error": "callable_excluded",
            }
            issues.append(
                {
                    "path": f"{tool_path}.handler",
                    "type": _type_name(handler),
                    "code": "callable_excluded",
                }
            )
            complete = False
        tools.append(item)
    return {
        "type": _type_name(tool_set),
        "serialization_scope": "provider_visible_tool_schema",
        "tools": tools,
    }, complete


def _snapshot_value(
    value: Any,
    *,
    depth: int,
    seen: set[int],
    path: str,
    issues: list[dict[str, str]],
) -> tuple[Any, bool]:
    """Convert one runtime value into a safe JSON-compatible representation.

    Args:
        value: Runtime value to serialize.
        depth: Current recursion depth.
        seen: Object identities in the active recursion path.
        path: JSON-style value location used in diagnostics.
        issues: Mutable diagnostic collection for incomplete values.

    Returns:
        Serialized value and whether conversion was lossless.
    """
    if value is None or isinstance(value, str | bool | int):
        return value, True
    if isinstance(value, float):
        if math.isfinite(value):
            return value, True
        if math.isnan(value):
            label = "NaN"
        else:
            label = "Infinity" if value > 0 else "-Infinity"
        return {"type": "float", "value": label}, True
    if isinstance(value, Enum):
        return _snapshot_value(
            value.value,
            depth=depth + 1,
            seen=seen,
            path=path,
            issues=issues,
        )
    if isinstance(value, Path):
        return {"type": _type_name(value), "value": str(value)}, True
    if isinstance(value, datetime | date | time):
        return {"type": _type_name(value), "value": value.isoformat()}, True
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        return {
            "type": _type_name(value),
            "encoding": "base64",
            "data": base64.b64encode(raw).decode("ascii"),
        }, True
    if depth >= _MAX_DEPTH:
        return _serialization_error(
            value,
            path=path,
            code="maximum_depth_exceeded",
            issues=issues,
        )

    identity = id(value)
    if identity in seen:
        return _serialization_error(
            value,
            path=path,
            code="cycle_detected",
            issues=issues,
        )
    next_seen = {*seen, identity}

    context_dump = getattr(value, "model_dump_for_context", None)
    if callable(context_dump) and _is_safe_dump_type(value):
        try:
            dumped = context_dump()
        except Exception:
            dumped = None
        if dumped is not None:
            return _snapshot_value(
                dumped,
                depth=depth + 1,
                seen=next_seen,
                path=path,
                issues=issues,
            )

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump) and _is_safe_dump_type(value):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
        except Exception:
            dumped = None
        if dumped is not None:
            return _snapshot_value(
                dumped,
                depth=depth + 1,
                seen=next_seen,
                path=path,
                issues=issues,
            )

    for method_name in ("to_json_dict", "to_dict", "toDict", "dict"):
        dump_method = getattr(value, method_name, None)
        if not callable(dump_method) or not _is_safe_dump_type(value):
            continue
        try:
            dumped = dump_method()
        except Exception:
            continue
        return _snapshot_value(
            dumped,
            depth=depth + 1,
            seen=next_seen,
            path=path,
            issues=issues,
        )

    if is_dataclass(value):
        result: dict[str, Any] = {}
        complete = True
        for name in _field_names(value):
            item = getattr(value, name, None)
            if _is_sensitive_field_name(name):
                serialized, field_complete = _redacted_value(
                    item,
                    path=f"{path}.{name}",
                    issues=issues,
                )
            else:
                serialized, field_complete = _snapshot_value(
                    item,
                    depth=depth + 1,
                    seen=next_seen,
                    path=f"{path}.{name}",
                    issues=issues,
                )
            result[name] = serialized
            complete = complete and field_complete
        return {"type": _type_name(value), "fields": result}, complete

    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            result: dict[str, Any] = {}
            complete = True
            for key, item in value.items():
                if _is_sensitive_field_name(key):
                    serialized, item_complete = _redacted_value(
                        item,
                        path=f"{path}.{key}",
                        issues=issues,
                    )
                else:
                    serialized, item_complete = _snapshot_value(
                        item,
                        depth=depth + 1,
                        seen=next_seen,
                        path=f"{path}.{key}",
                        issues=issues,
                    )
                result[key] = serialized
                complete = complete and item_complete
            return result, complete

        entries: list[dict[str, Any]] = []
        complete = True
        for index, (key, item) in enumerate(value.items()):
            serialized_key, key_complete = _snapshot_value(
                key,
                depth=depth + 1,
                seen=next_seen,
                path=f"{path}.entries[{index}].key",
                issues=issues,
            )
            if isinstance(key, str) and _is_sensitive_field_name(key):
                serialized_item, item_complete = _redacted_value(
                    item,
                    path=f"{path}.entries[{index}].value",
                    issues=issues,
                )
            else:
                serialized_item, item_complete = _snapshot_value(
                    item,
                    depth=depth + 1,
                    seen=next_seen,
                    path=f"{path}.entries[{index}].value",
                    issues=issues,
                )
            entries.append({"key": serialized_key, "value": serialized_item})
            complete = complete and key_complete and item_complete
        return {"type": "mapping", "entries": entries}, complete

    if isinstance(value, Sequence) and not isinstance(value, str):
        result = []
        complete = True
        for index, item in enumerate(value):
            serialized, item_complete = _snapshot_value(
                item,
                depth=depth + 1,
                seen=next_seen,
                path=f"{path}[{index}]",
                issues=issues,
            )
            result.append(serialized)
            complete = complete and item_complete
        return result, complete

    if isinstance(value, set | frozenset):
        result = []
        complete = True
        for index, item in enumerate(value):
            serialized, item_complete = _snapshot_value(
                item,
                depth=depth + 1,
                seen=next_seen,
                path=f"{path}.items[{index}]",
                issues=issues,
            )
            result.append(serialized)
            complete = complete and item_complete
        return {
            "type": _type_name(value),
            "ordering": "unordered",
            "items": result,
        }, complete

    if callable(value):
        return _serialization_error(
            value,
            path=path,
            code="callable_excluded",
            issues=issues,
        )
    return _serialization_error(
        value,
        path=path,
        code="unsupported_type",
        issues=issues,
    )


def _field_names(value: Any) -> list[str]:
    """Return declared dataclass fields followed by dynamic attributes.

    Args:
        value: Runtime object whose stored attributes should be listed.

    Returns:
        Stable field names without duplicates.
    """
    names = [field.name for field in fields(value)] if is_dataclass(value) else []
    try:
        dynamic_names = list(vars(value))
    except TypeError:
        dynamic_names = []
    names.extend(name for name in dynamic_names if name not in names)
    return names


def _is_sensitive_field_name(name: str) -> bool:
    """Return whether a structured field name normally contains credentials.

    Args:
        name: Mapping key or object field name.

    Returns:
        ``True`` for credential-bearing names that must never enter snapshots.
    """
    normalized = str(name).strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(
        (
            "_password",
            "_secret",
            "_api_key",
            "_access_token",
            "_refresh_token",
            "_token",
            "_credential",
            "_credentials",
            "_cookie",
            "_authorization",
            "_private_key",
            "_secret_key",
            "_signing_key",
            "_session_key",
        )
    )


def _redacted_value(
    value: Any,
    *,
    path: str,
    issues: list[dict[str, str]],
) -> tuple[dict[str, str], bool]:
    """Retain a sensitive field's presence without persisting its value.

    Args:
        value: Credential-bearing runtime value.
        path: JSON-style value location.
        issues: Mutable diagnostic collection.

    Returns:
        Redaction marker and a false lossless-serialization flag.
    """
    type_name = _type_name(value)
    issues.append(
        {
            "path": path,
            "type": type_name,
            "code": "sensitive_value_redacted",
        }
    )
    return {
        "type": type_name,
        "serialization_error": "sensitive_value_redacted",
    }, False


def _is_safe_dump_type(value: Any) -> bool:
    """Return whether known serialization methods may be invoked safely.

    Args:
        value: Runtime object exposing a dump method.

    Returns:
        True for AstrBot and supported provider SDK value types.
    """
    module = type(value).__module__
    return module == "astrbot" or module.startswith(_SAFE_DUMP_MODULES)


def _type_name(value: Any) -> str:
    """Return a stable type identifier without invoking object repr.

    Args:
        value: Runtime value whose type should be identified.

    Returns:
        Fully qualified type name.
    """
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _serialization_error(
    value: Any,
    *,
    path: str,
    code: str,
    issues: list[dict[str, str]],
) -> tuple[dict[str, str], bool]:
    """Create a safe incomplete-value marker and diagnostic.

    Args:
        value: Runtime value that could not be serialized losslessly.
        path: JSON-style value location.
        code: Stable failure code.
        issues: Mutable diagnostic collection.

    Returns:
        JSON-compatible marker and False completeness flag.
    """
    type_name = _type_name(value)
    issues.append({"path": path, "type": type_name, "code": code})
    return {"type": type_name, "serialization_error": code}, False
