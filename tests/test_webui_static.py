from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "humanize"
WEBUI_ROOT = PLUGIN_ROOT / "webui"

# Formal WebUI views. Sources live under webui/<view>/; the deployed SPA is
# built from them into pages/humanize/index.html by scripts/build_spa.py.
VIEWS = ("dashboard", "memory", "jargon", "examples", "context", "prompts", "settings")

# Shared scripts loaded by every view page (source), in this exact order.
SCRIPT_ORDER = (
    "shared/icons.js",
    "shared/ui.js",
    "shared/api.js",
    "shared/views/{view}.js",
)

# Backend endpoints the views actually call (see shared/api.js + shared/views/*.js).
API_ENDPOINTS = {
    "overview",
    "settings",
    "jargons",
    "jargon-detail",
    "jargon-export",
    "jargon-action",
    "memory-status",
    "memory-overview",
    "memory-agent-options",
    "memories",
    "memory-detail",
    "memory-jobs",
    "memory-action",
    "memory-recall-debug",
    "reply-examples",
    "reply-example-detail",
    "reply-example-action",
    "reply-example-recall-debug",
    "context-runs",
    "context-run",
    "context-stats",
    "prompt-templates",
    "prompt-template-audit",
    "chat-providers",
    "memory-providers",
}


class _AssetParser(HTMLParser):
    """Collect locally referenced stylesheets and scripts from one page."""

    def __init__(self) -> None:
        """Initialize empty asset collections."""
        super().__init__()
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record script src and stylesheet href attributes.

        Args:
            tag: Parsed HTML element name.
            attrs: Element attributes reported by ``HTMLParser``.
        """
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.scripts.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.stylesheets.append(str(values["href"]))


def _read_view(view: str, relative: str) -> str:
    """Read one UTF-8 asset from a view's source directory."""
    return (WEBUI_ROOT / view / relative).read_text(encoding="utf-8")


def _read_built(relative: str) -> str:
    """Read one UTF-8 asset from the built SPA page."""
    return (PAGE_ROOT / relative).read_text(encoding="utf-8")


def _view_files(pattern: str) -> list[Path]:
    """List view asset files under the built page's shared directory."""
    return sorted((PAGE_ROOT / "shared").glob(pattern))


def _read_shared_css() -> str:
    """Merge all built shared and per-view stylesheets."""
    chunks = [_read_built(f"shared/{p.name}") for p in _view_files("*.css")]
    chunks += [
        _read_built(f"shared/views/{p.name}") for p in _view_files("views/*.css")
    ]
    return "\n".join(chunks)


def _read_shared_js() -> str:
    """Merge all built per-view scripts."""
    chunks = [_read_built(f"shared/views/{p.name}") for p in _view_files("views/*.js")]
    return "\n".join(chunks)


def test_webui_sources_are_complete_and_built_page_serves_every_view() -> None:
    """Every view source exists; the built SPA contains all views and assets.

    Requires the complete webui/ sources locally; skipped when the sources
    are not deployed (e.g. the remote plugin copy only ships pages/humanize/).
    """
    import pytest

    if not all((WEBUI_ROOT / v / "index.html").is_file() for v in VIEWS):
        pytest.skip("webui sources not complete (remote deployment)")

    # Source views with their per-view stylesheet/script and shared layer.
    for view in VIEWS:
        html = _read_view(view, "index.html")
        parser = _AssetParser()
        parser.feed(html)
        for asset in parser.stylesheets + parser.scripts:
            if re.match(r"^https?://", asset):
                continue
            path = (WEBUI_ROOT / view / asset.removeprefix("./")).resolve()
            assert path.is_file(), f"{view} references missing asset {asset}"

        expected_scripts = [t.format(view=view) for t in SCRIPT_ORDER]
        assert parser.scripts == expected_scripts, (
            f"{view} script order mismatch: {parser.scripts}"
        )
        assert (WEBUI_ROOT / view / "shared/views" / f"{view}.css").is_file()
        assert (WEBUI_ROOT / view / "shared/views" / f"{view}.js").is_file()

    # Built SPA must contain every view section plus the shared layer.
    built = _read_built("index.html")
    for view in VIEWS:
        assert f'id="view-{view}"' in built, f"built SPA missing view {view}"
    for asset in (
        "app.js",
        "shared/icons.js",
        "shared/ui.js",
        "shared/api.js",
        "shared/tokens.css",
        "shared/base.css",
        "shared/components.css",
        "shared/layout.css",
        "shared/motion.css",
    ):
        assert (PAGE_ROOT / asset).is_file(), f"built SPA missing {asset}"
    for view in VIEWS:
        assert (PAGE_ROOT / "shared/views" / f"{view}.css").is_file()
        assert (PAGE_ROOT / "shared/views" / f"{view}.js").is_file()

    # Built SPA must not contain cross-view duplicate element ids.
    ids = re.findall(r'id="([^"]+)"', built)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"built SPA has duplicate ids: {dupes}"


def test_webui_uses_flex_layout_and_palette() -> None:
    """Design system: flexbox layout, pink tokens, responsive media queries."""
    css = _read_shared_css()

    assert "display: grid" not in css
    assert "display:grid" not in css

    for token in ("--pink:", "--pink-soft:", "--pink-line:"):
        assert token in css

    assert "@media" in css

    icons_js = _read_built("shared/icons.js")
    assert "HZ.icon" in icons_js
    assert "<svg" in icons_js
    assert "HZ.icon(" in _read_shared_js()


def test_webui_views_reference_real_api_endpoints() -> None:
    """All dashboard sections reference retained backend API endpoints."""
    api_js = _read_built("shared/api.js")
    assert '"/api/v1/plugins/extensions/astrbot_plugin_humanize/"' in api_js

    source = api_js + "\n" + _read_shared_js()
    for endpoint in API_ENDPOINTS:
        assert f'"{endpoint}"' in source, f"missing endpoint reference: {endpoint}"

    assert '"provider-cache-capabilities"' not in source


def test_webui_renders_persisted_content_as_text() -> None:
    """Untrusted API data is rendered through DOM text APIs, never innerHTML."""
    for view in sorted(p.name for p in _view_files("views/*.js")):
        source = _read_built(f"shared/views/{view}")

        assert ".textContent" in source, f"{view} never uses textContent"

        assert "document.write" not in source, f"{view} uses document.write"
        assert "DOMParser" not in source, f"{view} uses DOMParser"
        assert "createContextualFragment" not in source, (
            f"{view} uses createContextualFragment"
        )


def test_webui_build_is_reproducible() -> None:
    """The built SPA matches a fresh build from sources (scripts/build_spa.py).

    Requires the complete webui/ sources locally; skipped when the sources
    are not deployed (e.g. the remote plugin copy only ships pages/humanize/).
    """
    import subprocess
    import sys

    if not all((WEBUI_ROOT / v / "index.html").is_file() for v in VIEWS):
        import pytest

        pytest.skip("webui sources not complete (remote deployment)")

    subprocess.run(
        [sys.executable, str(PLUGIN_ROOT / "scripts/build_spa.py"), "--check"],
        cwd=PLUGIN_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
