"""Memory confidence decay helpers.

Memories fade: a fact's effective confidence halves every
``half_life_days`` after its last update, and contradicting evidence can
penalize it further at write time. Decay is computed lazily (on recall
and detail reads) so reads never mutate stored state; only the
contradiction penalty writes back a lower stored confidence.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime


def parse_utc(value: object, *, now: datetime) -> datetime | None:
    """Parse an ISO timestamp as UTC, or return None when unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00").replace("z", "+00:00")
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def decay_factor(
    updated_at: object,
    *,
    now: datetime | None = None,
    half_life_days: float,
) -> float:
    """Return the multiplicative time-decay factor in ``(0, 1]``.

    A memory reaches factor ``0.5`` after one half-life. Missing or
    unparsable timestamps decay nothing (factor 1.0).

    Args:
        updated_at: Last-update ISO timestamp of the memory.
        now: Reference clock; defaults to the current UTC time.
        half_life_days: Positive half-life in days.

    Returns:
        Decay factor between ``0`` (exclusive) and ``1``.
    """
    try:
        half_life = max(0.1, float(half_life_days))
    except (TypeError, ValueError):
        half_life = 120.0
    reference = now if now is not None else datetime.now(UTC)
    parsed = parse_utc(updated_at, now=reference)
    if parsed is None:
        return 1.0
    age_days = max(0.0, (reference - parsed).total_seconds() / 86_400.0)
    return math.pow(0.5, age_days / half_life)


def decayed_confidence(
    confidence: object,
    updated_at: object,
    *,
    now: datetime | None = None,
    half_life_days: float,
) -> float:
    """Combine stored confidence with time decay into an effective score.

    Args:
        confidence: Stored confidence in ``[0, 1]``.
        updated_at: Last-update ISO timestamp.
        now: Reference clock.
        half_life_days: Positive half-life in days.

    Returns:
        Effective confidence clamped to ``[0, 1]``.
    """
    try:
        base = min(1.0, max(0.0, float(confidence)))
    except (TypeError, ValueError):
        base = 0.0
    return base * decay_factor(updated_at, now=now, half_life_days=half_life_days)


def apply_contradiction_penalty(confidence: float, penalty: float) -> float:
    """Shrink a stored confidence once after contradicting evidence arrives.

    Args:
        confidence: Current stored confidence.
        penalty: Multiplicative penalty in ``(0, 1]``.

    Returns:
        Penalized confidence with a hard floor of 0.05.
    """
    try:
        factor = min(1.0, max(0.05, float(penalty)))
    except (TypeError, ValueError):
        factor = 0.5
    base = min(1.0, max(0.0, float(confidence)))
    return max(0.05, base * factor)
