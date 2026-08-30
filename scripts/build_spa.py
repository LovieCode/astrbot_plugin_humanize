#!/usr/bin/env python3
"""Build the single-page (SPA) entry for the Humanize WebUI.

The seven standalone views live under webui/<view>/ (source of truth, each
independently previewable). This script merges them into one plugin page:

    pages/humanize/index.html          (AstrBot entry; only page discovered)
    pages/humanize/shared/             (tokens/base/components/layout/motion.css,
                                        icons/ui/api.js)
    pages/humanize/shared/views/<v>.{css,js}
    pages/humanize/assets/

Cross-page duplicate element ids are automatically renamed with a per-view
prefix (e.g. mem-drawer) across HTML, JS and CSS so the merged document has
no id collisions. The source files are never modified.

Usage:
    python scripts/build_spa.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webui"
OUT = ROOT / "pages" / "humanize"
VIEWS = [
    "dashboard",
    "memory",
    "jargon",
    "examples",
    "context",
    "prompts",
    "policy",
    "settings",
]

# Per-view id prefix. Pages keep their own names; only shared ids get prefixed.
PREFIX = {
    "dashboard": "db",
    "memory": "mem",
    "jargon": "jg",
    "examples": "ex",
    "context": "cx",
    "prompts": "pt",
    "policy": "pl",
    "settings": "st",
}

SHARED_CSS = ["tokens.css", "base.css", "components.css", "layout.css", "motion.css"]
SHARED_JS = ["icons.js", "ui.js", "api.js"]

MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.S)
ID_ATTR_RE = re.compile(r'id="([^"]+)"')
JS_ID_RE = re.compile(
    r'(["\'])id["\']?\s*[\)\],]\s*|getElementById\(\s*["\']([^"\']+)["\']|querySelector(?:All)?\(\s*["\']#([A-Za-z0-9_-]+)'
)
CSS_ID_RE = re.compile(r"(?<![\w-])#([A-Za-z_][A-Za-z0-9_-]*)")


def collect_ids() -> dict[str, list[str]]:
    """Map id -> views where it appears."""
    seen: dict[str, list[str]] = {}
    for v in VIEWS:
        html = (SRC / v / "index.html").read_text(encoding="utf-8")
        for m in ID_ATTR_RE.finditer(html):
            seen.setdefault(m.group(1), []).append(v)
    return seen


def rename_map() -> dict[str, dict[str, str]]:
    """view -> {old_id: prefixed_id} for ids shared by more than one view."""
    ids = collect_ids()
    shared = {i: vs for i, vs in ids.items() if len(set(vs)) > 1}
    # sidebar/topbar are deliberately single in the merged page (not per view).
    shared = {i: vs for i, vs in shared.items() if i not in {"sidebar", "topbar"}}
    out: dict[str, dict[str, str]] = {v: {} for v in VIEWS}
    for old, views in shared.items():
        for v in set(views):
            out[v][old] = f"{PREFIX[v]}-{old}"
    return out


def rename_html(html: str, mapping: dict[str, dict[str, str]], view: str) -> str:
    for old, new in mapping[view].items():
        html = html.replace(f'id="{old}"', f'id="{new}"')
        html = html.replace(f'for="{old}"', f'for="{new}"')
        html = html.replace(f'aria-controls="{old}"', f'aria-controls="{new}"')
        html = html.replace(f'data-target="{old}"', f'data-target="{new}"')
    return html


def rename_js(js: str, mapping: dict[str, dict[str, str]], view: str) -> str:
    for old, new in mapping[view].items():
        js = js.replace(f'getElementById("{old}")', f'getElementById("{new}")')
        js = js.replace(f'$("{old}")', f'$("{new}")')
        js = js.replace(f'$("#{old}")', f'$("#{new}")')
        js = js.replace(f'querySelector("#{old}")', f'querySelector("#{new}")')
        js = js.replace(f'querySelectorAll("#{old}")', f'querySelectorAll("#{new}")')
    return js


def wrap_view_js(js: str, view: str) -> str:
    """Wrap the outermost IIFE into an HZ.views registration.

    The view scripts are written as standalone IIFEs that run immediately.
    In the SPA all views are loaded together, so instead of executing they
    must register themselves and run only when the view is activated. The
    first '(function () {' and the last '})();' delimit the outermost IIFE;
    inner IIFEs (e.g. dashboard.js) are left untouched.
    """
    start = js.find("(function () {")
    if start < 0:
        raise SystemExit(f"FATAL: no IIFE in {view}.js")
    end = js.rfind("})();")
    if end < 0 or end < start:
        raise SystemExit(f"FATAL: no closing IIFE in {view}.js")
    head = js[:start] + f'HZ.views["{view}"] = {{ init: function () {{\n'
    body = js[start + len("(function () {") : end]
    # The sidebar is rendered once by app.js; drop per-view renders (they
    # would re-render the shared sidebar on every init and lose delegates).
    body = re.sub(r"HZ\.renderSidebar\([^)]*\);\s*", "", body, count=1)
    tail = "\n} };\n" + js[end + len("})();") :]
    return head + body + tail


def build_views_js() -> dict[str, str]:
    """Return view name -> merged JS text (id renames + IIFE registration)."""
    mapping = rename_map()
    out: dict[str, str] = {}
    for v in VIEWS:
        js = (SRC / v / "shared" / "views" / f"{v}.js").read_text(encoding="utf-8")
        js = rename_js(js, mapping, v)
        js = wrap_view_js(js, v)
        out[v] = js
    return out


def rename_css(css: str, mapping: dict[str, dict[str, str]], view: str) -> str:
    for old, new in mapping[view].items():
        css = re.sub(rf"#({old})\b", f"#{new}", css)
    return css


def view_main(view: str, mapping: dict[str, dict[str, str]]) -> str:
    html = (SRC / view / "index.html").read_text(encoding="utf-8")
    body = re.search(r"<body>(.*)</body>", html, re.S)
    if not body:
        raise SystemExit(f"FATAL: no <body> in webui/{view}/index.html")
    body_html = body.group(1)
    m = MAIN_RE.search(body_html)
    if not m:
        raise SystemExit(f"FATAL: no <main> in webui/{view}/index.html")
    content = m.group(1)

    # Body-level overlays outside <main> (drawers/modals) must be kept too.
    rest = body_html.replace(m.group(0), "")
    rest = re.sub(r"<script.*?</script>", "", rest, flags=re.S)

    # Keep only standalone overlay elements (drawer-mask / aside.drawer /
    # modal-mask blocks); discard the bg-decor and app wrappers entirely.
    def extract_overlay(html: str, start: int) -> str:
        """Extract a balanced overlay element starting at ``start``.

        Counts nested div/aside open/close tags so overlays that contain
        inner elements (e.g. modal-mask wrapping an aside.modal) are not
        truncated at the first closing tag.
        """
        depth = 0
        for m in re.finditer(r"<(/?)(div|aside)(\s[^>]*)?>", html[start:], re.S):
            if not m.group(1):
                depth += 1
            else:
                depth -= 1
            if depth == 0:
                return html[start : start + m.end()]
        return html[start:]

    overlay_parts = []
    covered_until = -1
    for m in re.finditer(
        r'<div class="(?:drawer-mask|modal-mask)"|<aside class="drawer"|<aside class="[^"]*modal[^"]*"',
        rest,
        re.S,
    ):
        if m.start() < covered_until:
            # 已被外层 overlay（如 modal-mask 包 modal）覆盖
            continue
        extracted = extract_overlay(rest, m.start())
        overlay_parts.append(extracted)
        covered_until = m.start() + len(extracted)
    overlays = "\n\n    ".join(p.strip() for p in overlay_parts)
    if overlays:
        content += "\n\n    " + overlays

    content = rename_html(content, mapping, view)
    # Remove the per-view topbar (the SPA has a single shared topbar).
    content = re.sub(r'<div class="topbar" id="topbar"></div>', "", content, count=1)
    # 行尾空白只会在 git diff --check 里反复炸雷（topbar 移除后的残留缩进等），
    # 产物按生成物标准统一去掉。
    content = "\n".join(line.rstrip() for line in content.split("\n"))
    # section-link navigation: relative page links become plugin-page routes.
    for target in VIEWS:
        content = content.replace(
            f'href="{target}.html"',
            f'href="/plugin-page/astrbot_plugin_humanize/{target}"',
        )
    return content


def build() -> str:
    mapping = rename_map()
    sections = "\n".join(
        f'    <section class="view" id="view-{v}">\n{view_main(v, mapping)}\n    </section>'
        for v in VIEWS
    )
    shared_css = "\n".join(
        f'<link rel="stylesheet" href="shared/{f}" />' for f in SHARED_CSS
    )
    font_css = '<link rel="stylesheet" href="fonts/fonts.css" />'
    view_css = "\n".join(
        f'<link rel="stylesheet" href="shared/views/{v}.css" />' for v in VIEWS
    )
    shared_js = '<script src="/api/plugin/page/bridge-sdk.js"></script>\n' + "\n".join(
        f'<script src="shared/{f}"></script>' for f in SHARED_JS
    )
    view_js = "\n".join(f'<script src="shared/views/{v}.js"></script>' for v in VIEWS)
    views_init = (
        "<script>window.HZ = window.HZ || {}; HZ.views = HZ.views || {};</script>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>洛薇 Humanize</title>
{font_css}
{shared_css}
{view_css}
<style>
/* 单页应用：一次只显示一个视图 */
.view {{ display: none; }}
.view.active {{ display: flex; flex-direction: column; flex: 1 1 auto; min-width: 0; max-width: 100%; width: 100%; }}
</style>
</head>
<body>

<div class="bg-decor">
  <div class="blob blob-1"></div>
  <div class="blob blob-2"></div>
</div>

<div class="app">

  <aside class="sidebar" id="sidebar"></aside>

  <main class="main">

    <div class="topbar" id="topbar"></div>

{sections}

  </main>
</div>

{shared_js}
<script>
/* 将 data-icon 占位替换为共享图标 */
document.querySelectorAll("[data-icon]").forEach((el) => {{
  const svg = HZ.icon(el.dataset.icon);
  if (el.classList.contains("cx-sec-dot") || el.classList.contains("icon-btn")) {{
    el.innerHTML = svg;
  }} else {{
    el.insertAdjacentHTML("afterbegin", svg);
  }}
}});
</script>
{views_init}
{view_js}
<script src="app.js"></script>
</body>
</html>
"""


def copy_assets() -> None:
    (OUT / "shared" / "views").mkdir(parents=True, exist_ok=True)
    # local fonts (self-hosted, no Google Fonts CDN dependency)
    src_fonts = SRC / "_fonts"
    if src_fonts.is_dir():
        (OUT / "fonts").mkdir(exist_ok=True)
        for f in src_fonts.iterdir():
            if f.is_file():
                (OUT / "fonts" / f.name).write_bytes(f.read_bytes())
    # shared css
    for f in SHARED_CSS:
        (OUT / "shared" / f).write_bytes(
            (SRC / "dashboard" / "shared" / f).read_bytes()
        )
    # shared js (view-agnostic, take dashboard copy); SPA nav is handled by
    # app.js, so hrefs are neutralized to avoid navigating away.
    for f in SHARED_JS:
        text = (SRC / "dashboard" / "shared" / f).read_text(encoding="utf-8")
        if f == "ui.js":
            text = re.sub(r'href: "[^"]+"', 'href: "#"', text)
        (OUT / "shared" / f).write_text(text, encoding="utf-8")
    # view css/js with id renames applied
    mapping = rename_map()
    views_js = build_views_js()
    for v in VIEWS:
        css = (SRC / v / "shared" / "views" / f"{v}.css").read_text(encoding="utf-8")
        (OUT / "shared" / "views" / f"{v}.css").write_text(
            rename_css(css, mapping, v), encoding="utf-8"
        )
        (OUT / "shared" / "views" / f"{v}.js").write_text(views_js[v], encoding="utf-8")
    # assets (from dashboard, the only view that uses them)
    src_assets = SRC / "dashboard" / "assets"
    if src_assets.is_dir():
        (OUT / "assets").mkdir(exist_ok=True)
        for f in src_assets.iterdir():
            if f.is_file():
                (OUT / "assets" / f.name).write_bytes(f.read_bytes())
    # app.js (SPA controller)
    (OUT / "app.js").write_bytes((SRC / "app.js").read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate without writing")
    args = parser.parse_args()

    if args.check:
        expected = build()
        out = OUT / "index.html"
        if not out.is_file() or out.read_text(encoding="utf-8") != expected:
            print("FAIL: pages/humanize/index.html is out of date", file=sys.stderr)
            return 1
        print("OK")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(build(), encoding="utf-8")
    copy_assets()
    print(f"Built {OUT} ({len(list(OUT.rglob('*')))} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
