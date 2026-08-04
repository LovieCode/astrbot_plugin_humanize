from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "humanize"
SHARED_ROOT = PAGE_ROOT / "shared"

# Formal multi-page WebUI pages. Static preview/design pages (preview.html,
# design.html) are intentionally kept alongside and are not part of the UI.
FORMAL_PAGES = {
    "dashboard.html",
    "memory.html",
    "jargon.html",
    "examples.html",
    "context.html",
    "prompts.html",
    "settings.html",
}

# Shared scripts loaded by every page, in this exact order.
SCRIPT_ORDER = (
    "shared/icons.js",
    "shared/ui.js",
    "shared/api.js",
    "shared/views/{view}.js",
)

# Backend endpoints the views actually call (see shared/api.js + shared/views/*.js).
# Removed from the set on purpose:
# - provider-cache-capabilities: deleted together with the provider cache page.
# - protocol-logs: standalone protocol log page was merged into the context view;
#   the frontend now consumes protocol data through context-runs/context-run.
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

# HTML parsing helpers are intentionally tiny: the pages use plain HTML, so the
# standard library parser is enough; no external dependency is needed.


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


def _read(relative: str) -> str:
    """Read one UTF-8 WebUI asset.

    Args:
        relative: Path relative to the Humanize page directory.

    Returns:
        Decoded asset text.
    """
    return (PAGE_ROOT / relative).read_text(encoding="utf-8")


def _local_asset_path(relative: str) -> Path:
    """Resolve a page-relative asset reference to a file path.

    Args:
        relative: Asset reference from a page (may start with ``./``).

    Returns:
        Path under the Humanize page directory, or an absolute URL unchanged.

    Raises:
        ValueError: If the reference is an external URL.
    """
    if re.match(r"^https?://", relative):
        raise ValueError(f"external asset: {relative}")
    return (PAGE_ROOT / relative.removeprefix("./")).resolve()


def _view_files(pattern: str) -> list[Path]:
    """List view asset files (e.g. shared/views/*.js).

    Args:
        pattern: Glob pattern relative to the shared directory.

    Returns:
        Sorted list of matching files.
    """
    return sorted(SHARED_ROOT.glob(pattern))


def _read_shared_css() -> str:
    """Merge all shared and per-view stylesheets.

    Returns:
        Concatenated CSS text shared by every page.
    """
    chunks = [_read(str(p.relative_to(PAGE_ROOT))) for p in _view_files("*.css")]
    chunks += [_read(str(p.relative_to(PAGE_ROOT))) for p in _view_files("views/*.css")]
    return "\n".join(chunks)


def _read_shared_js() -> str:
    """Merge all view scripts (shared api.js is asserted separately).

    Returns:
        Concatenated JavaScript text of the per-view files.
    """
    chunks = [_read(str(p.relative_to(PAGE_ROOT))) for p in _view_files("views/*.js")]
    return "\n".join(chunks)


def test_webui_pages_are_complete_and_assets_exist() -> None:
    """Every formal page exists and references real, ordered shared assets."""
    for page_name in sorted(FORMAL_PAGES):
        html = _read(page_name)
        parser = _AssetParser()
        parser.feed(html)

        # Every locally referenced stylesheet and script must exist (external
        # URLs such as the Google Fonts stylesheet are intentionally skipped).
        for asset in parser.stylesheets + parser.scripts:
            if re.match(r"^https?://", asset):
                continue
            path = _local_asset_path(asset)
            assert path.is_file(), f"{page_name} references missing asset {asset}"

        # Shared scripts must be loaded in the fixed order icons → ui → api → views.
        view = page_name.removesuffix(".html")
        expected_scripts = [t.format(view=view) for t in SCRIPT_ORDER]
        assert parser.scripts == expected_scripts, (
            f"{page_name} script order mismatch: {parser.scripts}"
        )

        # The page must carry its own per-view stylesheet and script.
        assert (SHARED_ROOT / "views" / f"{view}.css").is_file()
        assert (SHARED_ROOT / "views" / f"{view}.js").is_file()

    # The shared layer must ship all files every page relies on.
    assert (SHARED_ROOT / "icons.js").is_file()
    assert (SHARED_ROOT / "ui.js").is_file()
    assert (SHARED_ROOT / "api.js").is_file()
    assert (SHARED_ROOT / "tokens.css").is_file()
    assert (SHARED_ROOT / "base.css").is_file()
    assert (SHARED_ROOT / "components.css").is_file()
    assert (SHARED_ROOT / "layout.css").is_file()
    assert (SHARED_ROOT / "motion.css").is_file()


def test_webui_uses_flex_layout_and_palette() -> None:
    """Design system: flexbox layout, pink tokens, responsive media queries."""
    css = _read_shared_css()

    # Flexbox only — no CSS grid anywhere.
    assert "display: grid" not in css
    assert "display:grid" not in css

    # Pink accent tokens exist and are soft.
    for token in ("--pink:", "--pink-soft:", "--pink-line:"):
        assert token in css

    # Responsive rules are present across the shared and view stylesheets.
    assert "@media" in css

    # No emoji icons: icons come from the shared SVG library.
    icons_js = _read("shared/icons.js")
    assert "HZ.icon" in icons_js
    assert "<svg" in icons_js
    # Views inject icons through HZ.icon (SVG strings), never through emoji.
    assert "HZ.icon(" in _read_shared_js()


def test_webui_views_reference_real_api_endpoints() -> None:
    """All dashboard sections reference retained backend API endpoints."""
    api_js = _read("shared/api.js")
    assert '"/api/v1/plugins/extensions/astrbot_plugin_humanize/"' in api_js

    source = api_js + "\n" + _read_shared_js()
    for endpoint in API_ENDPOINTS:
        assert f'"{endpoint}"' in source, f"missing endpoint reference: {endpoint}"

    # The removed provider cache capabilities endpoint must not be referenced.
    assert '"provider-cache-capabilities"' not in source


def test_webui_renders_persisted_content_as_text() -> None:
    """Untrusted API data is rendered through DOM text APIs, never innerHTML."""
    for view in sorted(view.name for view in _view_files("views/*.js")):
        source = _read(f"shared/views/{view}")

        # Every view must write dynamic text through textContent at least once.
        assert ".textContent" in source, f"{view} never uses textContent"

        # No document.write or DOMParser anywhere: no HTML-from-string parsing
        # of API data. innerHTML is allowed for static template/icon markup
        # (e.g. pagination buttons, SVG icons), never for user data.
        assert "document.write" not in source, f"{view} uses document.write"
        assert "DOMParser" not in source, f"{view} uses DOMParser"
        assert "createContextualFragment" not in source, (
            f"{view} uses createContextualFragment"
        )
