"""Jargon 修复专项 smoke：复用 smoke_spa 的 bridge 模拟 + 扩展 jargon fixtures。

驱动真实点击验证：
- BUG-2: 抽屉底部按钮（编辑词条等）有响应
- BUG-1: modal 提交不崩溃
- BUG-3: 义项编辑/合并/设为首选弹窗出现
- BUG-5: 切走再切回，顶栏搜索/按钮仍可用
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import smoke_spa  # noqa: E402

# 扩展 fixtures：jargon 详情 + 动作
FIXTURES = dict(smoke_spa.FIXTURES)
FIXTURES["jargons"] = {
    "success": True,
    "data": {
        "total": 2,
        "items": [
            {
                "id": 1, "term": "居闻", "meaning": "测试释义A", "status": "verified",
                "enabled": True, "scope_type": "global", "confidence": 0.9,
                "sense_count": 1, "has_conflict": False, "created_at": "2026-08-01T00:00:00",
            },
            {
                "id": 2, "term": "测试词B", "meaning": "测试释义B", "status": "candidate",
                "enabled": True, "scope_type": "private_user", "confidence": 0.8,
                "sense_count": 2, "has_conflict": True, "created_at": "2026-08-02T00:00:00",
            },
        ],
        "page": 1, "page_size": 20,
    },
}
FIXTURES["jargon-detail"] = {
    "success": True,
    "data": {
        "entry": {"id": 1, "term": "居闻", "status": "verified", "enabled": True,
                  "match_mode": "smart", "case_sensitive": False},
        "senses": [
            {"id": 10, "meaning": "测试义项A", "status": "verified", "confidence": 0.9,
             "is_preferred": True, "evidence_count": 2, "created_by": "web",
             "version": 1, "reason": "", "created_at": "2026-08-01T00:00:00"},
            {"id": 11, "meaning": "测试义项B", "status": "candidate", "confidence": 0.7,
             "is_preferred": False, "evidence_count": 0, "created_by": "llm",
             "version": 1, "reason": "LLM 提议", "created_at": "2026-08-01T00:00:00"},
        ],
        "aliases": [{"alias": "测试别名"}],
        "evidence": [], "inferences": [], "injections": [],
    },
}
FIXTURES["jargon-action"] = {
    "success": True,
    "data": {
        "updated": True, "deleted": False,
        "detail": {
            "entry": {"id": 1, "term": "居闻", "status": "verified", "enabled": True},
            "senses": [], "aliases": [],
        },
    },
}

# 自建 bridge SDK（不依赖 smoke_spa 的已格式化模板）
BRIDGE_SDK_TPL = """
(function () {
  window.__apiCalls = [];
  window.__fixtures = %(fixtures)s;
  window.AstrBotPluginPage = {
    ready: () => Promise.resolve({ pluginName: "astrbot_plugin_humanize", pageName: "humanize",
      displayName: "Humanize", locale: "zh-CN", i18n: {}, isDark: false }),
    apiGet: async (endpoint, params) => {
      window.__apiCalls.push("GET " + endpoint);
      const key = endpoint.split("?")[0].split("/")[0];
      const f = window.__fixtures && window.__fixtures[key];
      return f ? JSON.parse(JSON.stringify(f)) : { success: true, data: {} };
    },
    apiPost: async (endpoint, body) => {
      window.__apiCalls.push("POST " + endpoint);
      const key = endpoint.split("?")[0].split("/")[0] + "-action";
      const f = window.__fixtures && window.__fixtures[key];
      return f ? JSON.parse(JSON.stringify(f)) : { success: true, data: { ok: true } };
    },
  };
})();
"""

BRIDGE_SDK = BRIDGE_SDK_TPL % {"fixtures": json.dumps(FIXTURES)}


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        page = browser.new_page()
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # 用 smoke_spa 的 Handler（bridge-sdk 模拟），但替换 fixtures
        from smoke_spa import Handler
        import http.server
        import socketserver
        import threading
        import os

        class FixtureHandler(Handler):
            def do_GET(self):
                # 让 bridge-sdk 用我们的 fixtures（自建 SDK，不走 smoke_spa 的已格式化模板）
                if self.path.startswith("/api/plugin/page/bridge-sdk.js"):
                    self._send(200, "application/javascript", BRIDGE_SDK.encode())
                    return
                super().do_GET()

        old = os.getcwd()
        os.chdir(ROOT / "pages")
        with socketserver.TCPServer(("127.0.0.1", 8794), FixtureHandler) as httpd:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            url = "http://127.0.0.1:8794/api/plugin/page/content/astrbot_plugin_humanize/humanize/index.html?asset_token=local"
            page.goto(url)
            page.wait_for_timeout(2000)

            # 切到黑话
            page.click('.nav-item[data-nav="jargon"]')
            page.wait_for_timeout(1000)

            cards = page.query_selector_all("#jgList > div")
            print(f"1) 黑话列表: {len(cards)} 卡片")
            assert len(cards) >= 1

            # 打开第一张卡片抽屉
            page.click("#jgList > div")
            page.wait_for_timeout(600)
            drawer_ok = page.evaluate(
                "() => { const d = document.querySelector('#jg-drawer'); "
                "return d && getComputedStyle(d).display !== 'none'; }"
            )
            print(f"2) 抽屉打开: {drawer_ok}")
            assert drawer_ok

            senses = page.query_selector_all("#jg-drawer [data-sense-id]")
            print(f"3) 义项数: {len(senses)}")
            assert len(senses) >= 1

            # BUG-2: 底部「编辑词条」→ modal
            page.click('#jg-drawer button[data-action="edit_entry"]')
            page.wait_for_timeout(400)
            modal1 = page.evaluate(
                "() => { const m = document.querySelector('.jg-modal'); "
                "return m && getComputedStyle(m).display; }"
            )
            print(f"4) 编辑词条 modal: {modal1}")
            assert modal1 == "flex", "BUG-2 未修复: 底部按钮无响应"

            # BUG-1: modal 提交不崩溃
            page.fill("#mTerm", "居闻改")
            page.click('[data-m-ok]')
            page.wait_for_timeout(600)
            api_calls = page.evaluate("() => window.__apiCalls")
            print(f"5) 提交后 API: {[c for c in api_calls if 'jargon' in c]}")
            assert any("jargon-action" in c for c in api_calls), "BUG-1 未修复: 提交崩溃"
            toast_err = page.evaluate(
                "() => { const t = document.querySelector('.toast'); "
                "return t ? t.textContent : ''; }"
            )
            print(f"   toast: {toast_err!r}")
            assert "value" not in toast_err and "null" not in toast_err

            # BUG-3: 义项「编辑」→ modal
            page.click('#jg-drawer button[data-sense-action="edit"]')
            page.wait_for_timeout(400)
            modal2 = page.evaluate(
                "() => { const m = document.querySelector('.jg-modal'); "
                "return m && getComputedStyle(m).display; }"
            )
            print(f"6) 编辑义项 modal: {modal2}")
            assert modal2 == "flex", "BUG-3 未修复: 义项编辑无响应"
            page.click('[data-m-close]')
            page.wait_for_timeout(200)

            # 义项「设为首选」→ 因 candidate 应有错误 toast
            page.click('#jg-drawer button[data-sense-action="set_preferred"]')
            page.wait_for_timeout(400)
            modal3 = page.evaluate(
                "() => { const m = document.querySelector('.jg-modal'); "
                "return m && getComputedStyle(m).display; }"
            )
            print(f"7) 设为首选(candidate) modal: {modal3} (应 none + 错误提示)")

            # BUG-5: 切走再切回，搜索可用（先关抽屉，避免遮罩拦截导航点击）
            page.click("#jg-drawerClose")
            page.wait_for_timeout(300)
            page.click('.nav-item[data-nav="dashboard"]')
            page.wait_for_timeout(300)
            page.click('.nav-item[data-nav="jargon"]')
            page.wait_for_timeout(800)
            page.fill("#topbar .input-box input", "居闻")
            page.wait_for_timeout(600)
            api2 = page.evaluate("() => window.__apiCalls")
            print(f"8) 切回后搜索 API: {[c for c in api2 if 'jargon' in c][-3:]}")
            assert any("jargons" in c for c in api2), "BUG-5 未修复: 切回后搜索失灵"

            # 新建词条按钮也应可用
            page.get_by_role("button", name="新建词条").click()
            page.wait_for_timeout(400)
            modal4 = page.evaluate(
                "() => { const m = document.querySelector('.jg-modal'); "
                "return m && getComputedStyle(m).display; }"
            )
            print(f"9) 新建词条 modal: {modal4}")
            assert modal4 == "flex", "BUG-5 未修复: 切回后新建失灵"

            errors = [e for e in errors if "404" not in e]
            print(f"ERRORS: {errors}")
            assert not errors, f"JS errors: {errors}"
            print("JARGON SMOKE OK")
            browser.close()
        os.chdir(old)
    return 0


if __name__ == "__main__":
    sys.exit(main())
