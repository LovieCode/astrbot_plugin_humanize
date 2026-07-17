"""OpenViking merge-operation domain models retained by the embedded core."""

from .base import (
    FieldType,
    MergeOp,
    MergeOpBase,
    SearchReplaceBlock,
    StrPatch,
    get_python_type_for_field,
)
from .factory import MergeOpFactory
from .immutable import ImmutableOp
from .link_merge import merge_links
from .patch import PatchOp
from .patch_handler import PatchParseError, apply_str_patch
from .replace import ReplaceOp
from .sum import SumOp

__all__ = [
    "FieldType",
    "MergeOp",
    "MergeOpBase",
    "MergeOpFactory",
    "ImmutableOp",
    "PatchOp",
    "PatchParseError",
    "ReplaceOp",
    "SearchReplaceBlock",
    "StrPatch",
    "SumOp",
    "apply_str_patch",
    "get_python_type_for_field",
    "merge_links",
]
