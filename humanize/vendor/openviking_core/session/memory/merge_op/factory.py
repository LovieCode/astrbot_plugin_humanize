# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Factory for creating MergeOp instances.
"""

from typing import TYPE_CHECKING

from .base import FieldType, MergeOp, MergeOpBase
from .immutable import ImmutableOp
from .patch import PatchOp
from .replace import ReplaceOp
from .sum import SumOp

if TYPE_CHECKING:
    from ..dataclass import MemoryField


class MergeOpFactory:
    """Factory for creating MergeOp instances."""

    @staticmethod
    def create(merge_op: MergeOp, field_type: FieldType) -> MergeOpBase:
        """Create a MergeOp instance from a MergeOp enum.

        Args:
            merge_op: The merge operation type
            field_type: The underlying field type

        Returns:
            MergeOpBase implementation
        """
        if merge_op == MergeOp.PATCH:
            return PatchOp(field_type)
        elif merge_op == MergeOp.REPLACE:
            return ReplaceOp()
        elif merge_op == MergeOp.SUM:
            return SumOp()
        elif merge_op == MergeOp.IMMUTABLE:
            return ImmutableOp()
        else:
            # Default to PatchOp
            return PatchOp(field_type)

    @staticmethod
    def from_field(field: "MemoryField") -> MergeOpBase:
        """Create a MergeOp instance from a MemoryField.

        Args:
            field: The memory field definition

        Returns:
            MergeOpBase implementation
        """
        return MergeOpFactory.create(field.merge_op, field.field_type)
