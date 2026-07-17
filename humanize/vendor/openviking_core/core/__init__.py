"""Identifier and namespace primitives retained from OpenViking."""

from .identifiers import normalize_identifier_part, validate_identifier_part
from .peer_id import normalize_peer_id, safe_peer_id

__all__ = [
    "normalize_identifier_part",
    "normalize_peer_id",
    "safe_peer_id",
    "validate_identifier_part",
]
