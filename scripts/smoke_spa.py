#!/usr/bin/env python3
"""Local E2E smoke test for the built SPA (pages/humanize).

Serves the built page through a mock AstrBot page mechanism (rewrites
relative asset URLs to /api/plugin/page/content/... and injects a mock
window.AstrBotPluginPage bridge backed by canned fixtures), then drives a
headless browser with Playwright to verify:

  1. index.html loads and the dashboard view is active by default
  2. sidebar navigation switches views and init() runs once per view
  3. bridge apiGet is used for data (HZ.api prefers the bridge)
  4. no duplicate element ids in the merged DOM

Usage:
    python scripts/smoke_spa.py
"""

from __future__ import annotations

import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE_DIR = ROOT / "pages" / "humanize"
PLUGIN = "astrbot_plugin_humanize"

# Canned responses keyed by endpoint (returned as {success,data}).
FIXTURES = {
    "overview": {
        "success": True,
        "data": {
            "stats": {"messages_total": 42, "protocol_rate": 0.952, "memory_hits": 248, "jargon_injected": 12},
            "scopes": [{"scope_type": "group", "scope_hash": "abc", "label": "测试群"}],
            "pending_items": [],
        },
    },
    "memory-status": {"success": True, "data": {"state": "ready", "memories": 1500}},
    "memory-overview": {"success": True, "data": {"total": 1500, "layers": {"L0": 10, "L1": 200, "L2": 1290}}},
    "jargons": {"success": True, "data": {"total": 0, "items": [], "page": 1, "page_size": 20}},
    "examples": {"success": True, "data": {"total": 0, "items": [], "page": 1, "page_size": 20}},
    "context-runs": {"success": True, "data": {"total": 0, "items": [], "page": 1, "page_size": 20}},
    "prompt-templates": {"success": True, "data": {"templates": [], "active": ""}},
    "settings": {"success": True, "data": {"enabled": True, "memory_enabled": True}},
}

ASSET_ATTR_RE = re.compile(r'(?P<attr>(?:src|href))="(?P<url>[^"]+)"')


def rewrite_html(html: str, path: str) -> str:
    base = path.rsplit("/", 1)[0]

    def repl(m: re.Match) -> str:
        attr, url = m.group("attr"), m.group("url")
        if url.startswith(("http://", "https://", "/", "#", "data:")):
            return m.group(0)
        resolved = base + "/" + url
        return f'{attr}="{resolved}?asset_token=local"'

    html = ASSET_ATTR_RE.sub(repl, html)
    if "/api/plugin/page/bridge-sdk.js" not in html:
        bridge = '<script src="/api/plugin/page/bridge-sdk.js"></script>'
        html = html.replace("</body>", f"{bridge}</body>", 1)
    return html


BRIDGE_SDK = """
(function () {
  window.__apiCalls = [];
  window.__fixtures = window.__fixtures || %(fixtures)s;
  window.AstrBotPluginPage = {
    ready: () => Promise.resolve({ pluginName: "%(plugin)s", pageName: "humanize",
      displayName: "Humanize", locale: "zh-CN", i18n: {}, isDark: false }),
    apiGet: async (endpoint, params) => {
      window.__apiCalls.push("GET " + endpoint);
      const key = endpoint.split("?")[0].split("/")[0];
      const f = window.__fixtures && window.__fixtures[key];
      return f ? JSON.parse(JSON.stringify(f)) : { success: true, data: {} };
    },
    apiPost: async (endpoint, body) => {
      window.__apiCalls.push("POST " + endpoint);
      return { success: true, data: { ok: true } };
    },
  };
})();
""" % {"plugin": PLUGIN, "fixtures": json.dumps(FIXTURES)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = re.sub(r"\?.*$", "", self.path)
        if path == "/api/plugin/page/bridge-sdk.js":
            self._send(200, "application/javascript", BRIDGE_SDK.encode())
            return
        prefix = f"/api/plugin/page/content/{PLUGIN}/humanize/"
        if path.startswith(prefix):
            rel = path[len(prefix):]
        else:
            self._send(404, "text/plain", b"not found")
            return
        target = (PAGE_DIR / rel).resolve()
        try:
            target.relative_to(PAGE_DIR.resolve())
        except ValueError:
            self._send(403, "text/plain", b"forbidden")
            return
        if not target.is_file():
            self._send(404, "text/plain", b"missing")
            return
        data = target.read_bytes()
        ctype = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".jpg": "image/jpeg",
            ".png": "image/png",
        }.get(target.suffix, "application/octet-stream")
        if target.suffix == ".html":
            data = rewrite_html(data.decode("utf-8"), path).encode()
        self._send(200, ctype, data)

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 18777), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    from playwright.sync_api import sync_playwright

    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel='msedge')
        page = browser.new_page()
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"http://127.0.0.1:18777/api/plugin/page/content/{PLUGIN}/humanize/index.html")
        page.wait_for_timeout(1500)

        # 1. dashboard active by default
        active = page.evaluate(
            "document.querySelector('.view.active')?.id || ''")
        print("active view:", active)
        if active != "view-dashboard":
            failures.append(f"expected view-dashboard, got {active}")

        # 2. duplicate ids
        dupes = page.evaluate(
            "() => { const c = {}; document.querySelectorAll('[id]').forEach(e => c[e.id]=(c[e.id]||0)+1); return Object.entries(c).filter(([,n])=>n>1).map(([i])=>i); }")
        print("dup ids:", dupes or "none")
        if dupes:
            failures.append(f"duplicate ids: {dupes}")

        # 3. sidebar switch -> memory view + init
        page.click('.nav-item[data-nav="memory"]')
        page.wait_for_timeout(800)
        active = page.evaluate("document.querySelector('.view.active')?.id || ''")
        print("after switch:", active)
        if active != "view-memory":
            failures.append(f"switch failed: {active}")

        # 4. bridge used for API
        calls = page.evaluate("window.__apiCalls || []")
        print("bridge calls:", calls[:6], "..." if len(calls) > 6 else "")
        if not any("memory-status" in c for c in calls):
            failures.append("bridge apiGet not used for memory view")

        # 5. switch back and forth: init runs once
        page.click('.nav-item[data-nav="dashboard"]')
        page.wait_for_timeout(300)
        inited = page.evaluate(
            "Object.fromEntries(['dashboard','memory'].map(n => [n, document.getElementById('view-'+n)?.dataset.inited || '0']))")
        print("inited flags:", inited)
        if inited.get("memory") != "1":
            failures.append("memory view not marked inited")

        browser.close()

    print("ERRORS:", errors[:5] or "none")
    if failures:
        print("FAILURES:", failures)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
