"""OpenViking memory utilities retained by the embedded core."""

from .line_numbers import (
    add_line_numbers,
    every_line_has_line_numbers,
    extract_start_line_number,
    strip_line_numbers,
)
from .link_renderer import LinkRenderer

__all__ = [
    "LinkRenderer",
    "add_line_numbers",
    "every_line_has_line_numbers",
    "extract_start_line_number",
    "strip_line_numbers",
]
