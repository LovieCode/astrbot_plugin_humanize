"""Type-quota recall helpers ported from OpenViking.

This module is a pure-algorithm port of OpenViking's ``type_quota_recall``
retrieval strategy (search memory subtrees independently by type, then render
a bounded context block that degrades from full content to summary to
URI-only entries). No service, storage, or telemetry dependencies are used:
candidates are provided by the plugin's own scoped workspace reader.

The original module is AGPL-3.0 (c) 2026 Beijing Volcano Engine Technology
Co., Ltd. This port keeps the same behavior while replacing the service-level
``RequestContext`` / ``canonical_user_root`` concepts with the plugin's scope
descriptors.

Origin mapping to plugin scopes:

- ``global`` scope -> ``self`` (global memories written by any peer)
- ``private_user`` / ``group_member`` -> ``actor_peer`` (the acting user)
- ``group`` -> ``other_peer`` (shared group scope, treated as another peer)

Public API: :func:`select_type_quota` and the :class:`TypeQuotaResult` data
class, plus small helpers (:func:`normalize_quotas`,
:func:`normalize_penalties`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

TYPE_ORDER = ("event", "entity", "preference")
DEFAULT_QUOTAS = {"event": 10, "entity": 10, "preference": 3}
DEFAULT_OTHER_PEER_PENALTIES = {
    "event": 0.1,
    "entity": 0.1,
    "preference": 0.02,
}
DEFAULT_MAX_CHARS = 6500
DEFAULT_MIN_SCORE = 0.1
EVENTS_BUDGET_RATIO = 0.75
PREFERENCE_FULL_LIMIT = 3
OTHER_PEER_OVERFETCH = 4
ORIGIN_ORDER = ("actor_peer", "self", "other_peer")

_SCOPE_ORIGIN = {
    "global": "self",
    "private_user": "actor_peer",
    "group_member": "actor_peer",
    "group": "other_peer",
}


@dataclass
class TypeQuotaEntry:
    """One selected memory entry with its render mode."""

    uri: str
    score: float
    memory_type: str
    mode: str  # "full" | "summary" | "uri"
    origin: str
    content: str = ""
    summary: str = ""
    rank: int = 0
    abstract: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary.

        Returns:
            Dictionary with stable keys for diagnostics and WebUI.
        """
        data: dict[str, Any] = {
            "uri": self.uri,
            "score": self.score,
            "type": self.memory_type,
            "mode": self.mode,
            "rank": self.rank,
        }
        if self.content:
            data["content"] = self.content
        if self.summary:
            data["summary"] = self.summary
        if self.abstract:
            data["abstract"] = self.abstract
        if self.origin:
            data["origin"] = self.origin
        return data


@dataclass
class TypeQuotaResult:
    """Selection result: chosen entries plus per-type statistics."""

    entries: list[TypeQuotaEntry] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary.

        Returns:
            Dictionary with selected entries and stats.
        """
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "stats": self.stats,
        }


def normalize_quotas(quotas: Mapping[str, Any] | None) -> dict[str, int]:
    """Merge user quotas over the defaults, clamping negatives to zero.

    Plural alias keys (``events``/``entities``/``preferences``) are accepted
    and mapped to the plugin's singular memory types.

    Args:
        quotas: Optional mapping of memory type to integer quota.

    Returns:
        Normalized quota per memory type in :data:`TYPE_ORDER`.
    """
    alias = {"events": "event", "entities": "entity", "preferences": "preference"}
    merged = {**DEFAULT_QUOTAS}
    for key, value in (quotas or {}).items():
        canonical = alias.get(key, key)
        if canonical not in DEFAULT_QUOTAS:
            continue
        try:
            merged[canonical] = max(0, int(value))
        except (TypeError, ValueError):
            merged[canonical] = 0
    return merged


def _clamp_penalty(value: Any, fallback: float) -> float:
    """Clamp a penalty value into the zero-to-one range.

    Args:
        value: Raw penalty value.
        fallback: Value used when parsing fails.

    Returns:
        Clamped penalty between zero and one.
    """
    try:
        penalty = float(value)
    except (TypeError, ValueError):
        penalty = fallback
    return min(1.0, max(0.0, penalty))


def normalize_penalties(value: Any = None) -> dict[str, float]:
    """Normalize other-peer recall penalties by memory type.

    Args:
        value: Optional scalar or mapping of penalties.

    Returns:
        Penalty per memory type in :data:`TYPE_ORDER`.
    """
    if value is None:
        return dict(DEFAULT_OTHER_PEER_PENALTIES)
    if isinstance(value, Mapping):
        merged = dict(DEFAULT_OTHER_PEER_PENALTIES)
        for key, penalty in value.items():
            if key not in DEFAULT_OTHER_PEER_PENALTIES:
                continue
            merged[key] = _clamp_penalty(penalty, merged[key])
        return merged
    penalty = _clamp_penalty(value, 0.0)
    return dict.fromkeys(TYPE_ORDER, penalty)


def _origin_for_scope(scope_type: str) -> str:
    """Map a plugin scope type to an OpenViking origin.

    Args:
        scope_type: Plugin scope type (global/private_user/group/group_member).

    Returns:
        One of ``actor_peer``, ``self``, or ``other_peer``.
    """
    return _SCOPE_ORIGIN.get(scope_type, "other_peer")


def type_char_budgets(max_chars: int) -> dict[str, int]:
    """Compute per-type character budgets for full-content fragments.

    Args:
        max_chars: Total rendering budget.

    Returns:
        Budget per memory type; events are capped to 75% of the total.
    """
    max_chars = max(1, int(max_chars))
    return {
        "event": int(max_chars * EVENTS_BUDGET_RATIO),
        "entity": max_chars,
        "preference": max_chars,
    }


def _full_fragment(index: int, uri: str, score: float, content: str) -> str:
    """Render a full-content memory fragment.

    Args:
        index: One-based entry index.
        uri: Memory URI.
        score: Relevance score.
        content: Full memory content.

    Returns:
        XML fragment string.
    """
    return (
        f'<memory index="{index}" type="full">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{uri.rstrip('/').rsplit('/', 1)[-1]}</filename>\n"
        f"  <score>{score}</score>\n"
        f"  <content>{content}</content>\n"
        f"</memory>"
    )


def _summary_fragment(index: int, uri: str, score: float, summary: str) -> str:
    """Render a summary-only memory fragment.

    Args:
        index: One-based entry index.
        uri: Memory URI.
        score: Relevance score.
        summary: Short memory summary.

    Returns:
        XML fragment string.
    """
    return (
        f'<memory index="{index}" type="summary">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{uri.rstrip('/').rsplit('/', 1)[-1]}</filename>\n"
        f"  <score>{score}</score>\n"
        f"  <summary>{summary}</summary>\n"
        f"</memory>"
    )


def _uri_fragment(index: int, uri: str, score: float) -> str:
    """Render a URI-only memory fragment.

    Args:
        index: One-based entry index.
        uri: Memory URI.
        score: Relevance score.

    Returns:
        XML fragment string.
    """
    return (
        f'<memory index="{index}" type="uri">\n'
        f"  <uri>{uri}</uri>\n"
        f"  <filename>{uri.rstrip('/').rsplit('/', 1)[-1]}</filename>\n"
        f"  <score>{score}</score>\n"
        f"</memory>"
    )


def _extract_event_summary(content: str, fallback: str = "") -> str:
    """Extract the ``Summary:`` line from an OpenViking event memory.

    Args:
        content: Raw memory content.
        fallback: Abstract used when no summary is found.

    Returns:
        Extracted summary or the fallback.
    """
    if content:
        match = re.search(
            r"(?is)^\s*Summary:\s*(.*?)(?:\n\s*\d{4}-\d{2}-\d{2}"
            r"(?:\s*\([^)]+\))?\s*ChatLog:|\n\s*ChatLog:|\n\s*<!--\s*MEMORY_FIELDS|$)",
            content,
        )
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return fallback.strip()


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read an attribute from a mapping or object.

    Args:
        obj: Mapping or object.
        name: Attribute name.
        default: Fallback value.

    Returns:
        Attribute value or default.
    """
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _score(item: Any) -> float:
    """Read a finite relevance score from a candidate.

    Args:
        item: Candidate mapping.

    Returns:
        Score clamped to a finite float.
    """
    try:
        return float(_get_attr(item, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_type_quota(
    candidates: list[dict[str, Any]],
    *,
    quotas: Mapping[str, Any] | None = None,
    penalties: Any = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    min_score: float = DEFAULT_MIN_SCORE,
) -> TypeQuotaResult:
    """Select and render candidates by memory type with quotas.

    This is the plugin-side equivalent of OpenViking's
    ``search_type_quota_recall``. Candidates are already scoped and scored by
    the caller; the selection groups them by memory type, applies per-type
    quotas with other-peer penalties, and renders bounded XML that degrades
    from full content to summary to URI-only entries.

    Args:
        candidates: Scored memory rows. Each row has ``uri``, ``score``,
            ``memory_type``, ``content``, ``abstract``, and a ``scope_type``
            used for origin mapping.
        quotas: Optional per-type quotas.
        penalties: Optional other-peer penalties.
        max_chars: Maximum rendered XML characters.
        min_score: Minimum relevance score.

    Returns:
        Selected entries plus stats.
    """
    normalized_quotas = normalize_quotas(quotas)
    normalized_penalties = normalize_penalties(penalties)

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in TYPE_ORDER}
    for row in candidates:
        memory_type = str(_get_attr(row, "memory_type", "") or "")
        if memory_type not in grouped:
            continue
        score = _score(row)
        if not (score >= min_score) or score < min_score:
            continue
        grouped[memory_type].append(row)

    active_types = [
        (memory_type, normalized_quotas[memory_type])
        for memory_type in TYPE_ORDER
        if normalized_quotas[memory_type] > 0
    ]

    selected: list[tuple[str, dict[str, Any], int, str]] = []
    raw_by_type: dict[str, int] = {}
    for memory_type, quota in active_types:
        found = grouped.get(memory_type, [])
        raw_by_type[memory_type] = len(found)

        # Sort by score desc; apply other-peer penalty as a score offset.
        def sort_key(item: dict[str, Any]) -> float:
            origin = _origin_for_scope(str(_get_attr(item, "scope_type", "") or ""))
            penalty = (
                normalized_penalties.get(memory_type, 0.0)
                if origin == "other_peer"
                else 0.0
            )
            return _score(item) - penalty

        ranked = sorted(found, key=sort_key, reverse=True)[: max(0, quota)]
        selected.extend(
            (
                memory_type,
                item,
                rank,
                _origin_for_scope(str(_get_attr(item, "scope_type", "") or "")),
            )
            for rank, item in enumerate(ranked, start=1)
        )

    entries: list[TypeQuotaEntry] = []
    fragments_by_type: dict[str, list[str]] = {key: [] for key in TYPE_ORDER}
    fragments_by_origin_type: dict[tuple[str, str], list[str]] = {}
    budgets = type_char_budgets(max_chars)
    used_by_type = dict.fromkeys(TYPE_ORDER, 0)
    total_chars = 0
    preference_full_count = 0
    dropped = 0
    seen_content: set[int] = set()

    for index, (memory_type, item, rank, origin) in enumerate(selected, start=1):
        uri = str(_get_attr(item, "uri", "") or "")
        if not uri:
            continue
        score = _score(item)
        abstract = str(_get_attr(item, "abstract", "") or "")
        content = str(_get_attr(item, "content", "") or "").strip()

        content_key = content or abstract or uri
        if content_key:
            content_hash = hash(content_key)
            if content_hash in seen_content:
                continue
            seen_content.add(content_hash)

        mode = "uri"
        summary = ""
        entry_content = ""
        fragment = _uri_fragment(index, uri, score)

        if content:
            full = _full_fragment(index, uri, score, content)
            full_chars = len(full) + (1 if total_chars else 0)
            can_try_full = memory_type in budgets
            if memory_type == "preference":
                can_try_full = preference_full_count < PREFERENCE_FULL_LIMIT
                preference_full_count += 1
            if (
                can_try_full
                and used_by_type.get(memory_type, 0) + full_chars
                <= budgets.get(memory_type, max_chars)
                and total_chars + full_chars <= max_chars
            ):
                mode = "full"
                entry_content = content
                fragment = full
                used_by_type[memory_type] = (
                    used_by_type.get(memory_type, 0) + full_chars
                )
                total_chars += full_chars
            elif memory_type == "event":
                summary = _extract_event_summary(content, fallback=abstract)
                if summary:
                    mode = "summary"
                    fragment = _summary_fragment(index, uri, score, summary)
            elif abstract:
                summary = abstract

        if mode != "full":
            fragment_chars = len(fragment) + (1 if total_chars else 0)
            if total_chars + fragment_chars > max_chars and mode == "summary":
                mode = "uri"
                fragment = _uri_fragment(index, uri, score)
                fragment_chars = len(fragment) + (1 if total_chars else 0)
            if total_chars + fragment_chars > max_chars:
                dropped += 1
                continue
            total_chars += fragment_chars

        entries.append(
            TypeQuotaEntry(
                uri=uri,
                score=score,
                memory_type=memory_type,
                mode=mode,
                origin=origin,
                content=entry_content,
                summary=summary,
                rank=rank,
                abstract=abstract,
            )
        )
        fragments_by_type.setdefault(memory_type, []).append(fragment)
        fragments_by_origin_type.setdefault((origin, memory_type), []).append(fragment)

    origins = dict.fromkeys(ORIGIN_ORDER, 0)
    for entry in entries:
        origins[entry.origin] = origins.get(entry.origin, 0) + 1

    return TypeQuotaResult(
        entries=entries,
        stats={
            "quotas": normalized_quotas,
            "searched": raw_by_type,
            "returned": len(entries),
            "dropped": dropped,
            "max_chars": max_chars,
            "min_score": min_score,
            "other_peer_penalties": normalized_penalties,
            "origins": origins,
        },
    )
