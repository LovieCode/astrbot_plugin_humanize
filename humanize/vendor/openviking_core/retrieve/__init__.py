"""OpenViking retrieval primitives retained by the embedded core."""

from .memory_lifecycle import DEFAULT_HALF_LIFE_DAYS, hotness_score

__all__ = ["DEFAULT_HALF_LIFE_DAYS", "hotness_score"]
