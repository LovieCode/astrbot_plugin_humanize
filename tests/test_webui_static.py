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


def _read(name: str) -> str:
    """Read one UTF-8 WebUI asset.

    Args:
        name: File name relative to the WebUI page directory.

    Returns:
        The decoded file contents.
    """
    return (PAGE_ROOT / name).read_text(encoding="utf-8")


def test_webui_static_assets_and_dom_references_are_consistent() -> None:
    """Every local asset and DOM ID referenced at boot exists exactly once."""
    html = _read("index.html")
    app_js = _read("app.js")
    parser = _PageContractParser()
    parser.feed(html)

    assert parser.scripts == [
        "lucide.js",
        "api.js",
        "memory.js",
        "examples.js",
        "app.js",
    ]
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
    assert {
        "legacyWorkspace",
        "dynamicWorkspace",
        "toastRegion",
        "senseList",
        "evidenceSenseFilter",
    } <= referenced_ids


def test_webui_uses_real_api_without_demo_or_fake_data() -> None:
    """All visible operational views are backed by plugin API calls."""
    api_js = _read("api.js")
    app_js = _read("app.js")
    memory_js = _read("memory.js")
    examples_js = _read("examples.js")
    ui_js = "\n".join((app_js, memory_js, examples_js))

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
        assert marker not in app_js

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
        assert re.search(rf"\bapi\.{method}\b", ui_js)

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
    html = _read("index.html")
    app_js = _read("app.js")

    required_ids = {
        "matchModeSelect",
        "caseSensitiveToggle",
        "entryEnabledToggle",
        "aliasesInput",
        "senseList",
        "newSenseInput",
        "mergeSourceSelect",
        "mergeTargetSelect",
        "evidenceSenseFilter",
    }
    parser = _PageContractParser()
    parser.feed(html)
    assert required_ids <= set(parser.ids)

    for action in [
        "update_entry",
        "replace_aliases",
        "create_sense",
        "update_sense",
        "confirm_sense",
        "reject_sense",
        "set_preferred",
        "merge_sense",
    ]:
        assert f'"{action}"' in app_js

    assert 'update_sense: "update_meaning"' in app_js
    assert 'confirm_sense: "confirm"' in app_js
    assert 'reject_sense: "reject"' in app_js
    assert "sense_id" in app_js
    assert "source_sense_id" in app_js and "target_sense_id" in app_js


def test_removed_modules_are_absent_from_primary_navigation() -> None:
    """Removed runtime modules do not retain frozen WebUI placeholders."""
    html = _read("index.html")
    api_js = _read("api.js")
    app_js = _read("app.js")
    style_css = _read("style.css")
    parser = _PageContractParser()
    parser.feed(html)

    assert not {
        "persona",
        "state",
        "behavior",
        "expression",
        "control",
    } & parser.nav_targets
    assert {
        "overview",
        "jargons",
        "context",
        "memory",
        "examples",
        "protocol",
        "settings",
    } <= parser.nav_targets
    assert "relationships" not in parser.nav_targets
    assert "关系记忆" not in html
    assert "长期记忆" in html
    assert "回复样例" in html
    assert "冻结" not in html
    assert "features.js" not in html
    assert not (PAGE_ROOT / "features.js").exists()

    sources = "\n".join((html, api_js, app_js, style_css))
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
    for name in ["api.js", "app.js", "memory.js", "examples.js"]:
        source = _read(name)
        assert ".innerHTML" not in source
        assert "insertAdjacentHTML" not in source
        assert "document.write" not in source

    assert ".textContent" in _read("app.js")
    assert "replaceChildren" in _read("memory.js")


def test_settings_view_edits_all_prompt_templates_in_one_column() -> None:
    """Prompt templates are loaded, edited, saved, and reset without rich HTML."""
    app_js = _read("app.js")
    style_css = _read("style.css")

    for marker in [
        "normalizePromptTemplates",
        "appendPromptTemplateCards",
        "api.getPromptTemplates()",
        "api.savePromptTemplate",
        'action: "reset"',
        "prompt_templates",
        "variables",
        "editable",
    ]:
        assert marker in app_js

    for label in [
        "提示词模板",
        "可用变量",
        "撤销修改",
        "恢复默认",
        "保存模板",
        "正在保存",
    ]:
        assert label in app_js

    for selector in [
        ".settings-workspace",
        ".prompt-template-list",
        ".prompt-template-card",
        ".prompt-template-editor",
        ".prompt-template-actions",
    ]:
        assert selector in style_css

    settings_layout = re.search(
        r"\.settings-workspace[^\{]*\{(?P<body>[^}]*)\}", style_css, re.DOTALL
    )
    assert settings_layout is not None
    assert "grid-template-columns: minmax(0, 1fr)" in settings_layout.group("body")
    assert 'element("textarea", "prompt-template-editor")' in app_js


def test_context_trace_renders_full_snapshot_and_response_as_safe_text() -> None:
    """Context detail exposes complete trace fields without executing rich text."""
    app_js = _read("app.js")
    style_css = _read("style.css")

    for field in [
        "request_snapshot",
        "response_snapshot",
        "context_injection",
        "content",
        "snapshot",
        "response",
        "raw_output",
        "action",
        "messages",
        "error",
    ]:
        assert f'"{field}"' in app_js

    for label in [
        "模型请求快照",
        "request_snapshot · 完整 ProviderRequest",
        "插件注入明细",
        "模型响应快照",
        "response_snapshot · 完整 LLMResponse",
        "发送正文",
        "模型原始输出",
        "展开",
        "收起",
        "复制",
    ]:
        assert label in app_js

    assert "traceTextViewer" in app_js
    assert "detectTraceFormat" in app_js
    assert "displayedRequestSnapshot" in app_js
    assert "displayedResponseSnapshot" in app_js
    assert "responseMetadata" not in app_js
    assert 'viewer.classList.toggle("is-expanded")' in app_js
    assert "navigator.clipboard.writeText" in app_js
    assert "document.createTextNode" in app_js
    assert "DOMParser" not in app_js
    assert "marked(" not in app_js
    assert "markdown-it" not in app_js

    for selector in [
        ".trace-text-viewer",
        ".trace-content",
        ".trace-copy-button",
        ".trace-expand-button",
        ".trace-text-viewer.is-expanded .trace-content",
        ".context-response-error",
        ".trace-response-empty",
    ]:
        assert selector in style_css
    context_layout = re.search(
        r"\.context-layout\s*\{(?P<body>[^}]*)\}", style_css, re.DOTALL
    )
    assert context_layout is not None
    assert "flex-direction: column" in context_layout.group("body")
    assert "minmax(360px, 0.78fr)" not in style_css
    assert "@media (max-width: 640px)" in style_css


def test_overview_only_observes_provider_prompt_cache() -> None:
    """Overview reports provider evidence without maintaining a local LLM cache."""
    api_js = _read("api.js")
    app_js = _read("app.js")

    assert (
        'getProviderCacheCapabilities: () => get("provider-cache-capabilities")'
        in api_js
    )
    assert 'getChatProviders: () => get("chat-providers")' in api_js
    assert "api.getProviderCacheCapabilities()" in app_js
    assert "api.getChatProviders()" in app_js
    assert "function buildProviderPromptCachePanel" in app_js
    for label in [
        "Provider Prompt Cache",
        "真实命中观测",
        "Prefix 命中",
        "Cached Token",
        "usage 缺失保持 unknown",
        "不在插件内复用模型输出",
    ]:
        assert label in app_js

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
        assert removed not in app_js


def test_internal_memory_and_reply_examples_are_modular_and_single_column() -> None:
    """The knowledge UI manages local memory and reviewed examples only."""
    html = _read("index.html")
    api_js = _read("api.js")
    app_js = _read("app.js")
    memory_js = _read("memory.js")
    examples_js = _read("examples.js")
    style_css = _read("style.css")
    all_sources = "\n".join((html, api_js, app_js, memory_js, examples_js))

    assert 'data-nav="memory"' in html
    assert 'data-nav="examples"' in html
    assert "global.HumanizeMemory = Object.freeze" in memory_js
    assert "global.HumanizeExamples = Object.freeze" in examples_js
    assert "global.HumanizeMemory.mount" in app_js
    assert "global.HumanizeExamples.mount" in app_js
    assert 'view === "memory"' in app_js
    assert 'view === "examples"' in app_js

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
        "内置 OpenViking workspace",
        "内置记忆状态",
        "记忆列表",
        "候选审核",
        "召回调试",
        "后台任务",
        "新增记忆",
        "api.getMemoryOverview()",
        "api.getMemories({",
        "api.getMemoryDetail(id)",
        "api.getMemoryJobs",
        "api.memoryAction(payload)",
        "api.debugMemoryRecall",
        "api.getMemoryAgentOptions()",
        "人格上下文",
        "AstrBot 当前会话最终生效的人格",
    ]:
        assert marker in memory_js

    for marker in [
        "保存少量典型短对话",
        "不复制旧回复",
        "样例库",
        "候选审核",
        "召回测试",
        "新增样例",
        "未审核样例不会注入模型",
        "api.getReplyExamples({",
        "api.getReplyExampleDetail(id)",
        "api.replyExampleAction(payload)",
        "api.debugReplyExamples",
        "api.getMemoryAgentOptions()",
        "人格上下文",
        "AstrBot 当前会话最终生效的人格",
    ]:
        assert marker in examples_js

    assert 'agent.placeholder = "填写具体 Agent ID"' not in examples_js

    assert 'item.agentId || "default"' in examples_js
    assert 'item.agentId || "全部 Agent"' not in examples_js
    assert 'Agent ${item.agentId || "default"}' in memory_js
    assert 'action === "create" || item.scope.type !== "global"' in memory_js
    assert 'action === "create" || item.scope.type !== "global"' in examples_js
    for marker in [
        "jobsPage: 1",
        "jobsPageSize: 20",
        "page: state.jobsPage",
        "page_size: state.jobsPageSize",
        'option("pending", "待处理")',
        'option("running", "运行中")',
        'option("retry", "待重试")',
        'option("completed", "已完成")',
        'option("dead", "已终止")',
    ]:
        assert marker in memory_js
    assert 'option("failed"' not in memory_js

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
        ".memory-workspace",
        ".examples-workspace",
        ".knowledge-workspace",
        ".knowledge-list",
        ".knowledge-recall-results",
        ".knowledge-drawer",
        ".example-turn-list",
    ]:
        assert selector in style_css
    knowledge_layout = re.search(
        r"\.knowledge-workspace\s*,\s*\.examples-workspace\s*,"
        r"\s*\.knowledge-list\s*,\s*\.knowledge-record-list\s*,"
        r"\s*\.knowledge-recall-results\s*\{(?P<body>[^}]*)\}",
        style_css,
        re.DOTALL,
    )
    assert knowledge_layout is not None
    assert "grid-template-columns: minmax(0, 1fr)" in knowledge_layout.group("body")
