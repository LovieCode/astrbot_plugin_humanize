"""OpenViking merge-operation domain models retained by the embedded core."""

from .base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    SearchReplaceBlock,
    StrPatch,
    get_python_type_for_field,
)

__all__ = [
    "FieldType",
    "MergeOp",
    "MergeOpBase",
    "SearchReplaceBlock",
    "StrPatch",
    "get_python_type_for_field",
]
