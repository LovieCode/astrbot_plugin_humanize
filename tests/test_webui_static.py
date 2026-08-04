from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "humanize"

# Static preview/design pages are intentionally kept alongside the SPA.
EXTRA_PAGES = {"preview.html", "design.html", "assets"}


class _AssetParser(HTMLParser):
    """Collect locally referenced stylesheets and scripts."""

    def __init__(self) -> None:
        """Initialize empty asset collections."""
        super().__init__()
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []
        self.nav_views: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record dashboard assets and navigation targets.

        Args:
            tag: Parsed HTML element name.
            attrs: Element attributes reported by ``HTMLParser``.
        """
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.scripts.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.stylesheets.append(str(values["href"]))
        if values.get("data-view"):
            self.nav_views.append(str(values["data-view"]))


def _read(name: str) -> str:
    """Read one UTF-8 WebUI asset.

    Args:
        name: Path relative to the Humanize page directory.

    Returns:
        Decoded asset text.
    """
    return (PAGE_ROOT / name).read_text(encoding="utf-8")


def test_webui_is_a_small_independent_single_page_application() -> None:
    """The SPA ships its own HTML, stylesheet, and application script."""
    html = _read("index.html")
    parser = _AssetParser()
    parser.feed(html)

    assert parser.scripts == ["./app.js"]
    assert parser.stylesheets[-1:] == ["./style.css"]
    assert (PAGE_ROOT / "app.js").is_file()
    assert (PAGE_ROOT / "style.css").is_file()
    assert not (PAGE_ROOT / "views").exists()
    # Only the three SPA files are required; extra preview/design pages may exist.
    assert {"index.html", "style.css", "app.js"} <= {p.name for p in PAGE_ROOT.iterdir()}
    assert parser.nav_views == [
        "overview",
        "jargons",
        "memory",
        "examples",
        "context",
        "protocol",
        "prompts",
        "settings",
    ]
    app_js = _read("app.js")
    for marker in ("VIEWS_MAP", "OverviewView", "SettingsView", "iconNode"):
        assert marker in app_js


def test_webui_uses_flex_layout_and_the_requested_palette() -> None:
    """The design system: flexbox layout, flat white + soft pink, no emoji icons."""
    html = _read("index.html")
    css = _read("style.css")
    app_js = _read("app.js")

    # Flexbox only — no CSS grid anywhere.
    assert "display: grid" not in css
    assert "display:grid" not in css
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 640px)" in css

    # Pink accent tokens exist and are soft.
    for token in ("--pink:", "--pink-soft:", "--pink-line:"):
        assert token in css

    # No emoji icons: SVGs are the only icon source (hidden sprite).
    assert 'class="sprite"' in html
    assert 'id="i-heart"' in html
    assert "createElementNS" in app_js


def test_webui_keeps_operational_views_on_real_plugin_api() -> None:
    """All dashboard sections reference retained backend API endpoints."""
    source = _read("app.js")
    endpoints = {
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
        "protocol-logs",
        "provider-cache-capabilities",
        "chat-providers",
        "prompt-templates",
    }

    assert (
        'const API_BASE = "/api/v1/plugins/extensions/astrbot_plugin_humanize/"'
        in source
    )
    for endpoint in endpoints:
        assert f'"{endpoint}"' in source
    # The static preview fallback is allowed, but must be clearly gated on file://.
    assert 'window.location.protocol === "file:"' in source
    assert "IS_PREVIEW" in source


def test_webui_renders_persisted_content_as_text() -> None:
    """Untrusted API data is rendered through DOM text APIs rather than HTML parsing."""
    source = _read("app.js")

    for forbidden in (
        ".innerHTML",
        "insertAdjacentHTML",
        "document.write",
        "DOMParser",
    ):
        assert forbidden not in source
    assert ".textContent" in source
    assert ".replaceChildren" in source
    assert 'credentials: "same-origin"' in source
