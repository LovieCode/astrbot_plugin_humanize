from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "humanize"


class _PageContractParser(HTMLParser):
    """Collect IDs, navigation targets, and local assets from the WebUI HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.nav_targets: set[str] = set()
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record contract-bearing attributes from one start tag.

        Args:
            tag: HTML tag name.
            attrs: Parsed attributes on the tag.
        """
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(str(values["id"]))
        if values.get("data-nav"):
            self.nav_targets.add(str(values["data-nav"]))
        if tag == "script" and values.get("src"):
            self.scripts.append(str(values["src"]))
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.stylesheets.append(str(values["href"]))


def test_webui_static_assets_and_dom_references_are_consistent() -> None:
    """Every local asset and DOM ID referenced at boot exists exactly once."""
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    app_js = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")
    parser = _PageContractParser()
    parser.feed(html)

    assert parser.scripts == ["lucide.js", "api.js", "features.js", "app.js"]
    assert parser.stylesheets == ["style.css"]
    for asset in [*parser.scripts, *parser.stylesheets]:
        assert (PAGE_ROOT / asset).is_file(), asset

    assert len(parser.ids) == len(set(parser.ids))
    collect_refs = re.search(
        r"function collectRefs\(\) \{\s*\[(.*?)\]\.forEach",
        app_js,
        flags=re.DOTALL,
    )
    assert collect_refs is not None
    referenced_ids = set(re.findall(r'"([A-Za-z][A-Za-z0-9]*)"', collect_refs.group(1)))
    assert referenced_ids
    assert referenced_ids <= set(parser.ids)
    assert {"legacyWorkspace", "featureWorkspace", "toastRegion"} <= referenced_ids


def test_webui_feature_navigation_matches_frontend_api_contract() -> None:
    """Non-relationship feature views use matching API methods and endpoints."""
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    api_js = (PAGE_ROOT / "api.js").read_text(encoding="utf-8")
    features_js = (PAGE_ROOT / "features.js").read_text(encoding="utf-8")
    parser = _PageContractParser()
    parser.feed(html)

    feature_views = {"persona", "state", "behavior", "expression", "control"}
    assert feature_views <= parser.nav_targets
    assert "relationships" in parser.nav_targets
    assert "关系记忆" in html and "规划中" in html

    methods = {
        "getFeatures",
        "savePersona",
        "saveState",
        "saveBehavior",
        "saveExpression",
        "resetControl",
    }
    for method in methods:
        assert re.search(rf"\b{method}\b", api_js)
        assert re.search(rf"\bapi\.{method}\b", features_js)

    for endpoint in [
        "features",
        "persona",
        "state",
        "behavior",
        "expression",
        "control-audit",
        "control-reset",
        "control/reset",
    ]:
        assert f'"{endpoint}"' in api_js

    assert "global.HumanizeFeatures = Object.freeze" in features_js
    assert re.search(r"\bmount\(target, options\)", features_js)
    assert re.search(r"\bopen,\s*\n", features_js)
    assert "data-feature-form" in features_js
    assert "data-feature-save" in features_js
    assert "data-reset-form" in features_js

    save_function = features_js.index("async function save()")
    payload_collection = features_js.index(
        "const payload = collectPayload();", save_function
    )
    controls_disabled = features_js.index("state.pending = true;", save_function)
    assert payload_collection < controls_disabled
