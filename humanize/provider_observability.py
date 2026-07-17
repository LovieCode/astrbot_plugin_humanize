from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def _canonical_value(value: Any) -> Any:
    """Convert structured data to a deterministic JSON-compatible value.

    Args:
        value: Value used to build an observability fingerprint.

    Returns:
        A JSON-compatible value with sorted mapping keys and ordered sequences.

    Raises:
        TypeError: If the value cannot be represented without a lossy fallback.
        ValueError: If a float is not finite.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprint cannot contain non-finite floats")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, set | frozenset):
        normalized = [_canonical_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ),
        )
    raise TypeError(f"unsupported fingerprint value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize structured data without changing ordered semantic content.

    Args:
        value: JSON-compatible structured data.

    Returns:
        Canonical compact JSON text.

    Raises:
        TypeError: If a value would require a lossy serialization fallback.
        ValueError: If a float is not finite.
    """
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: Any, *, namespace: str = "humanize-observability-v1") -> str:
    """Create a namespaced SHA-256 fingerprint for structured data.

    Args:
        value: Structured payload to fingerprint.
        namespace: Versioned namespace used to separate fingerprint contracts.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    payload = f"{namespace}\x00{canonical_json(value)}".encode()
    return hashlib.sha256(payload).hexdigest()


def usage_dict(usage: Any) -> dict[str, int] | None:
    """Read AstrBot TokenUsage-compatible values without core imports.

    Args:
        usage: Provider usage object or mapping.

    Returns:
        Normalized usage mapping, or ``None`` when usage was not supplied.
    """
    if usage is None:
        return None
    if isinstance(usage, Mapping):
        getter = usage.get
    else:

        def getter(key: str, default: int = 0) -> Any:
            return getattr(usage, key, default)

    values: dict[str, int] = {}
    for key in ("input_cached", "input_other", "output"):
        try:
            values[key] = max(0, int(getter(key, 0) or 0))
        except (TypeError, ValueError):
            values[key] = 0
    return values


def usage_observed(usage: Any, *, raw_usage: Any = None) -> bool:
    """Determine whether a remote Provider supplied measurable token usage.

    Args:
        usage: Normalized or raw Provider usage object.
        raw_usage: Optional untouched usage object from the Provider response.

    Returns:
        ``True`` when usage fields were reported or contain a positive value.
    """
    if raw_usage is not None:
        return True
    if isinstance(usage, Mapping) and any(
        key in usage for key in ("input_cached", "input_other", "output")
    ):
        return True
    normalized = usage_dict(usage)
    return any(
        (normalized or {}).get(key, 0) > 0
        for key in ("input_cached", "input_other", "output")
    )


def provider_identity(provider: Any) -> dict[str, Any]:
    """Extract stable, non-secret identity fields from a remote Provider.

    Args:
        provider: AstrBot chat Provider instance.

    Returns:
        Provider identity suitable for diagnostics and usage observations.
    """
    provider_config = getattr(provider, "provider_config", {})
    if not isinstance(provider_config, Mapping):
        provider_config = {}
    meta = None
    meta_fn = getattr(provider, "meta", None)
    if callable(meta_fn):
        try:
            meta = meta_fn()
        except Exception:
            meta = None
    provider_id = getattr(meta, "id", None) or provider_config.get("id", "")
    provider_type = getattr(meta, "type", None) or provider_config.get("type", "")
    model = getattr(meta, "model", None) or getattr(provider, "model", None)
    if not model:
        model = provider_config.get("model")
    if not model:
        get_model = getattr(provider, "get_model", None)
        model = get_model() if callable(get_model) else ""
    model_revision = (
        getattr(provider, "model_revision", None)
        or provider_config.get("model_revision")
        or provider_config.get("revision")
        or ""
    )
    model_generation = (
        getattr(provider, "model_generation", None)
        or provider_config.get("model_generation")
        or provider_config.get("generation")
        or ""
    )
    capability = (
        getattr(provider, "prompt_cache_capability", None)
        or provider_config.get("prompt_cache_capability")
        or provider_config.get("prompt_cache_mode")
        or "unknown"
    )
    capability = str(capability).strip().lower()
    if capability not in {"implicit", "explicit", "unsupported", "unknown"}:
        capability = "unknown"
    return {
        "provider_id": str(provider_id or ""),
        "provider_type": str(provider_type or ""),
        "model": str(model or ""),
        "model_revision": str(model_revision or ""),
        "model_generation": str(model_generation or ""),
        "prompt_cache_capability": capability,
    }


def first_difference(left: Any, right: Any, path: str = "$") -> str | None:
    """Find the first deterministic structural difference between payloads.

    Args:
        left: Previous structured payload.
        right: Current structured payload.
        path: Internal path prefix.

    Returns:
        JSON-like path of the first difference, or ``None`` when equal.
    """
    if type(left) is not type(right):
        return path
    if isinstance(left, Mapping):
        left_keys = sorted(str(key) for key in left)
        right_keys = sorted(str(key) for key in right)
        if left_keys != right_keys:
            return f"{path}.keys"
        for key in left_keys:
            difference = first_difference(left[key], right[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(left, (list, tuple)):
        if len(left) != len(right):
            return f"{path}.length"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            difference = first_difference(left_item, right_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    return None if left == right else path
