from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from astrbot_plugin_humanize.humanize.vendor.openviking_core import (
    UPSTREAM_COMMIT,
    UPSTREAM_TAG,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.core.namespace import (
    uri_parts,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.core.peer_id import (
    safe_peer_id,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.message import (
    ImagePart,
    TextPart,
    part_from_dict,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.retrieve import (
    hotness_score,
)
from astrbot_plugin_humanize.humanize.vendor.openviking_core.session.memory import (
    MemoryData,
    MemoryFile,
    WikiLink,
)


def test_vendor_source_is_pinned_and_licensed() -> None:
    vendor_root = (
        Path(__file__).resolve().parents[1] / "humanize" / "vendor" / "openviking_core"
    )

    assert UPSTREAM_TAG == "v0.4.9"
    assert UPSTREAM_COMMIT == "4f0bd86f32c5a98ed78e7ba04adb5708c0bdb89a"
    assert (vendor_root / "LICENSES" / "AGPL-3.0.txt").is_file()
    assert (vendor_root / "LICENSES" / "SOURCE.md").is_file()


def test_vendor_does_not_import_removed_openviking_packages() -> None:
    vendor_root = (
        Path(__file__).resolve().parents[1] / "humanize" / "vendor" / "openviking_core"
    )
    invalid_imports: list[str] = []

    for source_path in vendor_root.rglob("*.py"):
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"), filename=str(source_path)
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            invalid_imports.extend(
                name
                for name in names
                if name == "openviking"
                or name.startswith("openviking.")
                or name == "openviking_cli"
                or name.startswith("openviking_cli.")
            )

    assert invalid_imports == []


def test_vendor_domain_kernel_behaves_without_platform_services() -> None:
    text = part_from_dict({"type": "text", "text": "hello"})
    image = part_from_dict(
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}
    )
    link = WikiLink(
        f=1,
        t=2,
        link_type="not a valid link type!",
        weight=2,
        match_text=None,
    )
    memory = MemoryData(memory_type="preference", content="likes tea")
    memory_file = MemoryFile(content="Read [tea](preferences/tea.md)")

    assert isinstance(text, TextPart)
    assert isinstance(image, ImagePart)
    assert link.link_type == "related_to"
    assert link.weight == 1.0
    assert memory.content == "likes tea"
    assert memory_file.plain_content() == "Read tea"
    assert safe_peer_id("../unsafe") is None
    assert uri_parts("user/demo/memories/tea?view=l0") == [
        "user",
        "demo",
        "memories",
        "tea",
    ]
    score = hotness_score(
        3,
        datetime(2026, 7, 16, tzinfo=timezone.utc),
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    assert 0.0 < score < 1.0
