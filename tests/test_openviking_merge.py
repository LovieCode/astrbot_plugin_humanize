from __future__ import annotations

import pytest
from astrbot_plugin_humanize.humanize.vendor.openviking_core.session.memory import (
    MemoryField,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.session.memory.merge_op import (
    FieldType,
    ImmutableOp,
    MergeOp,
    MergeOpFactory,
    PatchOp,
    PatchParseError,
    ReplaceOp,
    SearchReplaceBlock,
    StrPatch,
    SumOp,
    apply_str_patch,
    merge_links,
)


def test_merge_factory_preserves_openviking_field_semantics() -> None:
    patch = MergeOpFactory.create(MergeOp.PATCH, FieldType.STRING)
    replace = MergeOpFactory.create(MergeOp.REPLACE, FieldType.STRING)
    summed = MergeOpFactory.from_field(
        MemoryField(
            name="score",
            field_type=FieldType.INT64,
            merge_op=MergeOp.SUM,
        )
    )

    assert isinstance(patch, PatchOp)
    assert patch.get_output_schema_type(FieldType.STRING) is StrPatch
    assert isinstance(replace, ReplaceOp)
    assert isinstance(summed, SumOp)


def test_scalar_merge_operations_preserve_empty_and_existing_values() -> None:
    assert ReplaceOp().apply("existing", "") == "existing"
    assert ReplaceOp().apply("existing", "replacement") == "replacement"
    assert SumOp().apply(10, 5) == 15
    assert SumOp().apply("invalid", 5) == "invalid"
    assert ImmutableOp().apply(None, "first") == "first"
    assert ImmutableOp().apply("first", "second") == "first"


def test_string_patch_supports_exact_and_numbered_replacements() -> None:
    exact = StrPatch(
        blocks=[SearchReplaceBlock(search="likes tea", replace="likes coffee")]
    )
    numbered = StrPatch(
        blocks=[
            SearchReplaceBlock(
                search="3\tkeep\n4\tsame",
                replace="3\tKEEP\n4\tSAME",
            )
        ]
    )

    assert apply_str_patch("user likes tea", exact) == "user likes coffee"
    assert apply_str_patch("keep\nsame\nkeep\nsame", numbered) == (
        "keep\nsame\nKEEP\nSAME"
    )


def test_string_patch_rejects_unmatched_content() -> None:
    patch = StrPatch(
        blocks=[SearchReplaceBlock(search="missing fact", replace="new fact")]
    )

    with pytest.raises(PatchParseError, match="search content not found"):
        apply_str_patch("current memory", patch)


def test_link_merge_deduplicates_and_keeps_strongest_weight() -> None:
    existing = [
        {
            "from_uri": "viking://user/a",
            "to_uri": "viking://user/b",
            "match_text": "tea",
            "weight": 0.4,
            "description": "old",
        }
    ]
    incoming = [
        {
            "from_uri": "viking://user/a",
            "to_uri": "viking://user/b",
            "match_text": "tea",
            "weight": 0.8,
            "description": "new",
        }
    ]

    merged = merge_links(existing, incoming)

    assert len(merged) == 1
    assert merged[0]["weight"] == 0.8
    assert merged[0]["description"] == "new"
