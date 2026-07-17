# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Canonical OpenViking memory-file metadata parsing."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MEMORY_FIELDS_PATTERN = re.compile(r"<!--\s*MEMORY_FIELDS\s*([\s\S]*?)\s*-->")


def parse_memory_file_with_fields(content: str) -> dict[str, Any]:
    """Parse canonical JSON metadata from an OpenViking memory file.

    Args:
        content: Raw memory file content.

    Returns:
        Parsed metadata with the visible body in ``content``. Invalid metadata is
        ignored so a damaged memory file cannot break chat recall.
    """
    if not content:
        return {"content": ""}

    result: dict[str, Any] = {}
    match = _MEMORY_FIELDS_PATTERN.search(content)
    if match:
        fields_json = match.group(1).strip()
        if fields_json:
            try:
                fields: object = json.loads(fields_json)
                if isinstance(fields, list) and fields:
                    fields = fields[0]
                if isinstance(fields, dict):
                    result.update(fields)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse OpenViking MEMORY_FIELDS JSON")

    result["content"] = _MEMORY_FIELDS_PATTERN.sub("", content).strip()
    return result
