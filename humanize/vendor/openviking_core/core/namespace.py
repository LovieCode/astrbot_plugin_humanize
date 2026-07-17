# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Minimal OpenViking namespace helpers required by chat memory."""

from __future__ import annotations


def uri_parts(uri: str) -> list[str]:
    """Return normalized Viking URI path segments without query parameters.

    Args:
        uri: Full or short-form Viking URI.

    Returns:
        URI path segments without the scheme or query string.
    """
    normalized = uri.split("?", 1)[0]
    if not normalized.startswith("viking://"):
        normalized = f"viking://{normalized.lstrip('/')}"
    normalized = normalized.rstrip("/")
    if normalized == "viking:":
        normalized = "viking://"
    if normalized == "viking://":
        return []
    return [part for part in normalized[len("viking://") :].split("/") if part]


def uri_depth(uri: str) -> int:
    """Return the number of normalized Viking URI path segments.

    Args:
        uri: Full or short-form Viking URI.

    Returns:
        Number of path segments.
    """
    return len(uri_parts(uri))


def uri_leaf_name(uri: str) -> str:
    """Return the final normalized Viking URI path segment.

    Args:
        uri: Full or short-form Viking URI.

    Returns:
        Final segment, or an empty string for the root URI.
    """
    parts = uri_parts(uri)
    return parts[-1] if parts else ""


def relative_uri_path(root_uri: str, uri: str) -> str:
    """Return a URI path relative to a containing root.

    Args:
        root_uri: Expected parent URI.
        uri: Candidate child URI.

    Returns:
        Slash-separated child path, or an empty string when not nested.
    """
    root_parts = uri_parts(root_uri)
    parts = uri_parts(uri)
    if parts == root_parts or parts[: len(root_parts)] != root_parts:
        return ""
    return "/".join(parts[len(root_parts) :])
