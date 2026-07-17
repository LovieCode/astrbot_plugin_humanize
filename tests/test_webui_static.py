from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = PLUGIN_ROOT / "pages" / "humanize"
VIEW_NAMES = (
    "overview",
    "jargons",
    "memory",
    "examples",
    "context",
    "protocol",
    "prompts",
    "settings",
)


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


def _read(name: str) -> str:
    """Read one UTF-8 WebUI asset.

    Args:
        name: File name relative to the WebUI page directory.

    Returns:
        The decoded file contents.
    """
    return (PAGE_ROOT / name).read_text(encoding="utf-8")


def _runtime_js() -> str:
    """Return all application JavaScript except the static icon bundle."""
    names = ["api.js", "core.js", "ui.js", "app.js"]
    names.extend(f"views/{name}.js" for name in VIEW_NAMES)
    return "\n".join(_read(name) for name in names)


def test_webui_static_assets_and_dom_references_are_consistent() -> None:
    """Every local asset and DOM ID referenced at boot exists exactly once."""
    html = _read("index.html")
    app_js = _read("app.js")
    parser = _PageContractParser()
    parser.feed(html)

    assert parser.scripts == [
        "./lucide.js",
        "./api.js",
        "./core.js",
        "./ui.js",
        *[f"./views/{name}.js" for name in VIEW_NAMES],
        "./app.js",
    ]
    assert parser.stylesheets[-1:] == ["./style.css"]
    for asset in [*parser.scripts, *parser.stylesheets]:
        if asset.startswith("http"):
            continue
        assert (PAGE_ROOT / asset.removeprefix("./")).is_file(), asset

    assert len(parser.ids) == len(set(parser.ids))
    assert {
        "app",
        "sidebar-nav",
        "sidebar-status-dot",
        "sidebar-status-text",
        "sidebar-version",
        "topbar-title",
        "topbar-subtitle",
        "topbar-actions",
        "view-root",
        "toast-region",
    } <= set(parser.ids)
    for name in VIEW_NAMES:
        assert f"global.HumanizeViews.{name}" in _read(f"views/{name}.js")
        assert f'{{ key: "{name}"' in app_js


def test_webui_uses_real_api_without_demo_or_fake_data() -> None:
    """All visible operational views are backed by plugin API calls."""
    api_js = _read("api.js")
    ui_js = _runtime_js()

    forbidden = [
        "DEMO_MODE",
        "demoJargons",
        "demoOverview",
        "demoGet",
        "demoPost",
        "?demo=1",
    ]
    for marker in forbidden:
        assert marker not in api_js
        assert marker not in ui_js

    methods = {
        "getOverview",
        "getSettings",
        "getJargons",
        "getJargonDetail",
        "getProtocolLogs",
        "getContextRuns",
        "getContextRun",
        "getContextStats",
        "getProviderCacheCapabilities",
        "getMemoryOverview",
        "getMemories",
        "getMemoryDetail",
        "getMemoryJobs",
        "memoryAction",
        "debugMemoryRecall",
        "getReplyExamples",
        "getReplyExampleDetail",
        "replyExampleAction",
        "debugReplyExamples",
        "getChatProviders",
        "getPromptTemplates",
        "exportJargons",
        "jargonAction",
        "savePromptTemplate",
    }
    for method in methods:
        assert re.search(rf"\b{method}\b", api_js)
        assert re.search(rf"\bApi\.{method}\b", ui_js)

    for endpoint in [
        "overview",
        "settings",
        "jargons",
        "jargon-detail",
        "protocol-logs",
        "context-runs",
        "context-run",
        "context-stats",
        "provider-cache-capabilities",
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
        "chat-providers",
        "prompt-templates",
        "jargon-export",
        "jargon-action",
    ]:
        assert f'"{endpoint}"' in api_js


def test_webui_multi_sense_actions_and_legacy_fallback_are_present() -> None:
    """The jargon detail UI manages entry settings and individual senses."""
    jargon_js = _read("views/jargons.js")

    for marker in [
        "matchModeSelect",
        "enabledCheckbox",
        "caseSensitiveCheckbox",
        "aliasesTextarea",
        "newSenseTextarea",
        "sourceSelect",
        "targetSelect",
    ]:
        assert marker in jargon_js

    for action in [
        "update_entry",
        "replace_aliases",
        "create_sense",
        "update_sense",
        "confirm_sense",
        "reject_sense",
        "set_preferred_sense",
        "merge_sense",
        "delete_sense",
    ]:
        assert f'"{action}"' in jargon_js

    assert "sense_id" in jargon_js
    assert "source_sense_id" in jargon_js and "target_sense_id" in jargon_js


def test_removed_modules_are_absent_from_primary_navigation() -> None:
    """Removed runtime modules do not retain frozen WebUI placeholders."""
    html = _read("index.html")
    api_js = _read("api.js")
    app_js = _read("app.js")
    style_css = _read("style.css")
    nav_source = re.search(r"const NAV_ITEMS = \[(.*?)\];", app_js, re.DOTALL)
    assert nav_source is not None
    nav_keys = set(re.findall(r'key: "([a-z]+)"', nav_source.group(1)))
    assert nav_keys == set(VIEW_NAMES)
    assert not {"persona", "state", "behavior", "expression", "control"} & nav_keys
    assert "relationships" not in nav_keys
    assert "关系记忆" not in app_js
    assert "长期记忆" in app_js
    assert "回复样例" in app_js
    assert "冻结" not in app_js
    assert "features.js" not in html
    assert not (PAGE_ROOT / "features.js").exists()

    sources = "\n".join((html, api_js, _runtime_js(), style_css))
    for marker in [
        "HumanizeFeatures",
        "FEATURE_VIEWS",
        "featureWorkspace",
        "getFeatures",
        "savePersona",
        "saveState",
        "saveBehavior",
        "saveExpression",
        "resetControl",
        "control-audit",
        "control-reset",
        "control/reset",
        ".feature-workspace",
        ".freeze-badge",
        ".frozen-notice",
    ]:
        assert marker not in sources


def test_persisted_content_uses_dom_text_apis() -> None:
    """Dynamic API content is not interpolated into HTML strings."""
    names = ["api.js", "core.js", "ui.js", "app.js"]
    names.extend(f"views/{name}.js" for name in VIEW_NAMES)
    for name in names:
        source = _read(name)
        assert ".innerHTML" not in source
        assert "insertAdjacentHTML" not in source
        assert "document.write" not in source

    assert ".textContent" in _runtime_js()
    assert "replaceChildren" in _read("views/memory.js")


def test_settings_view_edits_all_prompt_templates_in_one_column() -> None:
    """Prompt templates are loaded, edited, saved, and reset without rich HTML."""
    prompts_js = _read("views/prompts.js")
    style_css = _read("style.css")

    for marker in [
        "loadTemplates",
        "renderList",
        "Api.getPromptTemplates()",
        "Api.savePromptTemplate",
        'action: "reset"',
        "variables",
        "required_variables",
    ]:
        assert marker in prompts_js

    for label in [
        "模板内容",
        "模板变量",
        "撤销修改",
        "恢复默认",
        "保存",
        "正在保存",
    ]:
        assert label in prompts_js

    for selector in [
        ".prompt-nav",
        ".prompt-nav-item",
        ".split-view",
        ".textarea",
    ]:
        assert selector in style_css
    assert "Ui.createTextarea" in prompts_js


def test_context_trace_renders_full_snapshot_and_response_as_safe_text() -> None:
    """Context detail exposes complete trace fields without executing rich text."""
    context_js = _read("views/context.js")
    core_js = _read("core.js")
    ui_js = _read("ui.js")
    style_css = _read("style.css")

    for field in [
        "model_request",
        "request",
        "inserted_sections",
        "sections",
        "content",
        "response",
        "model_response",
        "error",
    ]:
        assert field in context_js

    for label in [
        "模型请求快照",
        "插入段",
        "响应快照",
        "展开",
        "收起",
        "复制",
    ]:
        assert label in "\n".join((context_js, ui_js))

    assert "Ui.createTraceViewer" in context_js
    assert "function createTraceViewer" in ui_js
    assert "function detectTraceFormat" in core_js
    assert "function copyText" in core_js
    assert "navigator.clipboard.writeText" in core_js
    assert ".textContent" in ui_js
    for source in (context_js, core_js, ui_js):
        assert "DOMParser" not in source
        assert "marked(" not in source
        assert "markdown-it" not in source

    for selector in [
        ".trace-viewer",
        ".trace-viewer-header",
        ".trace-viewer-action",
        ".trace-viewer-body",
        '.trace-viewer-body[data-collapsed="true"]',
        ".trace-viewer-content",
        ".split-view",
    ]:
        assert selector in style_css
    assert "@media (max-width: 1100px)" in style_css


def test_overview_only_observes_provider_prompt_cache() -> None:
    """Overview reports provider evidence without maintaining a local LLM cache."""
    api_js = _read("api.js")
    overview_js = _read("views/overview.js")

    assert (
        'getProviderCacheCapabilities: () => get("provider-cache-capabilities")'
        in api_js
    )
    assert 'getChatProviders: () => get("chat-providers")' in api_js
    assert "Api.getProviderCacheCapabilities()" in overview_js
    assert "Api.getChatProviders()" in overview_js
    assert "function loadProviderCache" in overview_js
    for label in [
        "Provider Prompt Cache",
        "真实命中观测",
        "Prefix 命中",
        "Cached Token",
        "usage 缺失保持 unknown",
        "不在插件内复用模型输出",
    ]:
        assert label in overview_js

    for removed in [
        "getCacheOverview",
        "getCacheEvents",
        "purgeCache",
        "startEmbeddingRebuild",
        "buildCacheStatusPanel",
        "buildCacheEventsPanel",
        "buildEmbeddingStatusPanel",
        "内部 LLM Result Cache",
        "重建 Shadow 索引",
        '"cache-overview"',
        '"cache-events"',
        '"cache-purge"',
        '"embedding-providers"',
        '"embedding-generations"',
        '"embedding-rebuild"',
    ]:
        assert removed not in api_js
        assert removed not in overview_js


def test_internal_memory_and_reply_examples_are_modular_and_single_column() -> None:
    """The knowledge UI manages local memory and reviewed examples only."""
    html = _read("index.html")
    api_js = _read("api.js")
    app_js = _read("app.js")
    memory_js = _read("views/memory.js")
    examples_js = _read("views/examples.js")
    style_css = _read("style.css")
    all_sources = "\n".join((html, api_js, app_js, memory_js, examples_js))

    assert "global.HumanizeViews.memory" in memory_js
    assert "global.HumanizeViews.examples" in examples_js
    assert "global.HumanizeViews?.[key]" in app_js
    assert '{ key: "memory"' in app_js
    assert '{ key: "examples"' in app_js

    for method_contract in [
        'getMemoryOverview: () => get("memory-overview")',
        'getMemoryAgentOptions: () => get("memory-agent-options")',
        'getMemories: (params) => get("memories", params)',
        'getMemoryDetail: (id) => get("memory-detail", { id })',
        'getMemoryJobs: (params) => get("memory-jobs", params)',
        'memoryAction: (body) => post("memory-action", body)',
        'debugMemoryRecall: (body) => post("memory-recall-debug", body)',
        'getReplyExamples: (params) => get("reply-examples", params)',
        'getReplyExampleDetail: (id) => get("reply-example-detail", { id })',
        'replyExampleAction: (body) => post("reply-example-action", body)',
        'debugReplyExamples: (body) => post("reply-example-recall-debug", body)',
    ]:
        assert method_contract in api_js

    for marker in [
        "OpenViking 记忆条目",
        "记忆状态",
        'candidate: { label: "候选"',
        "召回测试",
        "后台任务",
        "新增记忆",
        "Api.getMemoryOverview()",
        "Api.getMemories({",
        "Api.getMemoryDetail(id)",
        "Api.getMemoryJobs",
        "Api.memoryAction(payload)",
        "Api.debugMemoryRecall",
        "Api.getMemoryAgentOptions()",
    ]:
        assert marker in memory_js

    for marker in [
        "1-3 轮对话样例",
        'draft: { label: "草稿"',
        "召回测试",
        "新增样例",
        "Api.getReplyExamples({",
        "Api.getReplyExampleDetail(id)",
        "Api.replyExampleAction(payload)",
        "Api.debugReplyExamples",
        "Api.getMemoryAgentOptions()",
    ]:
        assert marker in examples_js

    for marker in [
        'pending: { label: "待处理"',
        'running: { label: "进行中"',
        'retry: { label: "重试中"',
        'completed: { label: "已完成"',
        'dead: { label: "已死亡"',
        "page: 1, page_size: 50",
    ]:
        assert marker in memory_js
    assert 'failed: {' not in memory_js

    assert "openviking-status" not in all_sources
    for removed in [
        "getArchives",
        "archiveSession",
        "generateArchiveLayer",
        "embedArchive",
        "deleteArchive",
        '"archives"',
        '"archive-detail"',
    ]:
        assert removed not in all_sources

    for selector in [
        ".split-view",
        ".split-view-left",
        ".split-view-right",
        ".drawer",
        ".turn-row",
        ".conversation",
    ]:
        assert selector in style_css
