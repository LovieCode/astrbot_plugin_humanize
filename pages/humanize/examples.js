(function initializeHumanizeExamples(global, document) {
  "use strict";

  const api = global.HumanizeApi;
  const STATUS_LABELS = {
    approved: ["已审核", "ready"],
    active: ["已启用", "ready"],
    candidate: ["待审核", "warning"],
    draft: ["草稿", "warning"],
    rejected: ["已拒绝", "empty"],
    disabled: ["已停用", "empty"],
    tombstoned: ["已删除", "error"],
  };
  const state = {
    root: null,
    workspace: null,
    notify: function noop() {},
    tab: "library",
    items: [],
    total: 0,
    page: 1,
    pageSize: 20,
    stats: {},
    scopeOptions: [],
    personaOptions: [{ id: "default", label: "默认人格", configured: true, debuggable: true }],
    personaDefaultId: "default",
    filters: { search: "", status: "", enabled: "", scope: "", agent: "", topic: "", intent: "" },
    detail: null,
    pending: false,
    listRequestId: 0,
    detailRequestId: 0,
    viewEpoch: 0,
    searchTimer: null,
    lastFocus: null,
    drawerKeyHandler: null,
  };
  const refs = {};

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const node = document.createElement("i");
    node.dataset.lucide = name;
    return node;
  }

  function refreshIcons() {
    if (global.lucide && typeof global.lucide.createIcons === "function") global.lucide.createIcons();
  }

  function numberValue(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function booleanValue(value, fallback) {
    if (typeof value === "boolean") return value;
    if (value === 1 || value === "1" || value === "true") return true;
    if (value === 0 || value === "0" || value === "false") return false;
    return fallback;
  }

  function formatTime(value) {
    const text = String(value || "").trim();
    if (!text) return "--";
    const parsed = new Date(text);
    return Number.isNaN(parsed.getTime()) ? text : parsed.toLocaleString("zh-CN", { hour12: false });
  }

  function option(value, label) {
    const node = element("option", "", label);
    node.value = value;
    return node;
  }

  function fillPersonaSelect(select, selected) {
    if (!select) return;
    select.replaceChildren();
    state.personaOptions.filter((item) => item && item.debuggable !== false && item.id !== "*").forEach((item) => {
      const personaId = String(item.id || "").trim();
      if (!personaId || [...select.options].some((candidate) => candidate.value === personaId)) return;
      const label = String(item.label || personaId);
      const text = personaId === "default"
        ? "默认人格（default）"
        : label !== personaId
          ? `${label}（${personaId}）`
          : `${personaId}${item.configured ? "（AstrBot 人格）" : "（历史人格）"}`;
      select.append(option(personaId, text));
    });
    const preferred = String(selected || state.personaDefaultId || "default");
    if (preferred !== "*" && ![...select.options].some((item) => item.value === preferred)) {
      select.append(option(preferred, `${preferred}（历史人格）`));
    }
    select.value = preferred;
    if (!select.value && select.options.length) select.value = select.options[0].value;
  }

  async function loadPersonaOptions(epoch) {
    try {
      const payload = await api.getMemoryAgentOptions();
      if (epoch !== state.viewEpoch || !document.body.contains(state.workspace)) return;
      const source = payload && typeof payload === "object" ? payload : {};
      const items = Array.isArray(source.items) ? source.items : [];
      state.personaOptions = items.length ? items : [{ id: "default", label: "默认人格", configured: true, debuggable: true }];
      state.personaDefaultId = String(source.default_id || "default");
      if (refs.exampleRecallPersona && document.body.contains(refs.exampleRecallPersona)) {
        const selected = refs.exampleRecallPersona.dataset.touched === "true" ? refs.exampleRecallPersona.value : "";
        fillPersonaSelect(refs.exampleRecallPersona, selected);
      }
    } catch {
      if (epoch !== state.viewEpoch) return;
      state.personaOptions = [{ id: "default", label: "默认人格", configured: true, debuggable: true }];
      state.personaDefaultId = "default";
      if (refs.exampleRecallPersona && document.body.contains(refs.exampleRecallPersona)) {
        fillPersonaSelect(refs.exampleRecallPersona, refs.exampleRecallPersona.value);
      }
    }
  }

  function collection(payload, names) {
    if (Array.isArray(payload)) return { items: payload, total: payload.length, source: {} };
    const source = payload && typeof payload === "object" ? payload : {};
    let items = [];
    for (const key of ["items", ...(names || [])]) {
      if (Array.isArray(source[key])) {
        items = source[key];
        break;
      }
    }
    return { items, total: numberValue(source.total ?? source.count, items.length), source };
  }

  function arrayValue(value) {
    if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
    return String(value || "").split(/[\n,，]+/).map((item) => item.trim()).filter(Boolean);
  }

  function normalizeTurns(value) {
    const turns = Array.isArray(value) ? value : [];
    const normalized = turns.slice(0, 3).map((turn) => ({
      role: String(turn && turn.role || "user").toLowerCase() === "assistant" ? "assistant" : "user",
      content: String(turn && (turn.content || turn.text || turn.message) || ""),
    }));
    return normalized.length ? normalized : [{ role: "user", content: "" }];
  }

  function scopeInfo(source) {
    const scope = source && source.scope && typeof source.scope === "object" ? source.scope : {};
    return {
      type: String(scope.type || source.scope_type || "global"),
      token: String(scope.token || source.scope_token || ""),
      label: String(scope.label || source.scope_label || (source.scope_type === "global" ? "全局" : "受限作用域")),
    };
  }

  function normalizeExample(value) {
    const envelope = value && typeof value === "object" ? value : {};
    const source = envelope.detail && typeof envelope.detail === "object"
      ? envelope.detail
      : envelope.item && typeof envelope.item === "object"
        ? envelope.item
        : envelope.example && typeof envelope.example === "object"
          ? envelope.example
          : envelope;
    return {
      id: source.id ?? source.example_id ?? null,
      title: String(source.title || source.name || ""),
      status: String(source.status || source.review_status || "draft").toLowerCase(),
      enabled: booleanValue(source.enabled, ["approved", "active"].includes(String(source.status || "").toLowerCase())),
      scope: scopeInfo(source),
      agentId: String(source.agent_id || source.agent || "default"),
      topic: String(source.topic || ""),
      intent: String(source.intent || ""),
      styleTags: arrayValue(source.style_tags || source.styles),
      keywords: arrayValue(source.keywords),
      turns: normalizeTurns(source.turns || source.dialogue || source.messages),
      idealReply: String(source.ideal_reply || source.reply || source.response || ""),
      conditions: String(source.conditions || source.applicable_when || ""),
      exclusions: String(source.exclusions || source.disabled_when || ""),
      notes: String(source.notes || source.remark || ""),
      qualityScore: numberValue(source.quality_score ?? source.quality, 0.8),
      version: numberValue(source.version ?? source.revision, 0),
      sourceContextRunId: String(source.source_context_run_id || source.context_run_id || ""),
      sourceType: String(source.source_type || source.creation_method || "manual"),
      usageCount: numberValue(source.usage_count ?? source.injection_count, 0),
      createdAt: String(source.created_at || ""),
      updatedAt: String(source.updated_at || ""),
      reviewedAt: String(source.reviewed_at || ""),
      reviewReason: String(source.review_reason || source.reason || ""),
      usage: Array.isArray(envelope.usage) ? envelope.usage : Array.isArray(source.usage) ? source.usage : [],
      revisions: Array.isArray(envelope.revisions) ? envelope.revisions : Array.isArray(source.revisions) ? source.revisions : [],
      audit: Array.isArray(envelope.audit) ? envelope.audit : Array.isArray(source.audit) ? source.audit : [],
    };
  }

  function statusBadge(status, enabled) {
    const normalized = enabled === false && ["approved", "active"].includes(String(status || "").toLowerCase()) ? "disabled" : String(status || "draft").toLowerCase();
    const meta = STATUS_LABELS[normalized] || [normalized || "未知", "empty"];
    return element("span", `runtime-state-badge ${meta[1]}`, meta[0]);
  }

  function header() {
    const node = element("header", "dynamic-header knowledge-page-header");
    const mark = element("span", "dynamic-header-icon");
    mark.append(icon("messages-square"));
    const copy = element("div");
    copy.append(
      element("h2", "", "回复样例"),
      element("p", "", "保存少量典型短对话，让 Agent 参考表达方式和处理结构，不复制旧回复。"),
    );
    const create = element("button", "memory-action primary");
    create.type = "button";
    create.append(icon("plus"), element("span", "", "新增样例"));
    create.addEventListener("click", () => openExampleDrawer(null));
    node.append(mark, copy, create);
    return node;
  }

  function metric(label, value, hint) {
    const card = element("article", "dynamic-metric");
    card.append(element("span", "", label), element("strong", "", value), element("small", "", hint));
    return card;
  }

  function renderMetrics() {
    const stats = state.stats && typeof state.stats === "object" ? state.stats : {};
    const counts = stats.by_status && typeof stats.by_status === "object" ? stats.by_status : stats.counts && typeof stats.counts === "object" ? stats.counts : stats;
    const approved = numberValue(counts.approved ?? counts.active, state.items.filter((item) => ["approved", "active"].includes(normalizeExample(item).status)).length);
    const candidates = numberValue(counts.candidate ?? counts.draft ?? counts.pending, state.items.filter((item) => ["candidate", "draft"].includes(normalizeExample(item).status)).length);
    const disabled = Object.keys(counts).length
      ? numberValue(counts.disabled, 0) + numberValue(counts.rejected, 0) + numberValue(counts.tombstoned, 0)
      : state.items.filter((item) => !normalizeExample(item).enabled).length;
    const used = numberValue(counts.usage ?? counts.injections ?? stats.usage_count, state.items.reduce((sum, item) => sum + normalizeExample(item).usageCount, 0));
    refs.metrics.replaceChildren(
      metric("已审核", approved, "允许参与召回"),
      metric("待审核", candidates, "不会注入回复"),
      metric("已停用", disabled, "保留历史但不使用"),
      metric("使用次数", used, "作为 few-shot 参考"),
    );
  }

  function tabButton(key, label, iconName) {
    const button = element("button", `knowledge-tab${state.tab === key ? " active" : ""}`);
    button.type = "button";
    button.append(icon(iconName), element("span", "", label));
    button.addEventListener("click", () => switchTab(key));
    return button;
  }

  function renderTabs() {
    refs.tabs.replaceChildren(
      tabButton("library", "样例库", "library"),
      tabButton("review", "候选审核", "badge-check"),
      tabButton("recall", "召回测试", "search-check"),
    );
    refreshIcons();
  }

  function buildShell() {
    state.root.replaceChildren(header());
    const workspace = element("section", "examples-workspace knowledge-workspace");
    workspace.dataset.humanizeExamples = "true";
    state.workspace = workspace;
    refs.metrics = element("section", "dynamic-metrics-grid knowledge-metrics");
    refs.metrics.append(metric("已审核", "--", "正在读取"), metric("待审核", "--", "正在读取"), metric("已停用", "--", "正在读取"), metric("使用次数", "--", "正在读取"));
    refs.tabs = element("nav", "knowledge-tabs");
    refs.content = element("section", "dynamic-panel knowledge-main-panel");
    workspace.append(refs.metrics, refs.tabs, refs.content);
    state.root.append(workspace);
    renderTabs();
    refreshIcons();
  }

  function scopeOptionValue(scope) {
    return `${encodeURIComponent(scope.type)}:${encodeURIComponent(scope.token)}`;
  }

  function parseScope(value) {
    const text = String(value || "");
    const separator = text.indexOf(":");
    if (separator < 0) return { type: "", token: "" };
    try {
      return { type: decodeURIComponent(text.slice(0, separator)), token: decodeURIComponent(text.slice(separator + 1)) };
    } catch {
      return { type: "", token: "" };
    }
  }

  function fillScopeSelect(select, selected, allLabel) {
    select.replaceChildren(option("", allLabel || "全部作用域"));
    state.scopeOptions.forEach((scope) => {
      const value = scopeOptionValue(scope);
      if ([...select.options].some((item) => item.value === value)) return;
      select.append(option(value, scope.label));
    });
    if (selected && ![...select.options].some((item) => item.value === selected)) select.append(option(selected, "当前受限作用域"));
    select.value = selected || "";
  }

  function refreshScopeControls() {
    if (refs.exampleScopeFilter && document.body.contains(refs.exampleScopeFilter)) {
      fillScopeSelect(refs.exampleScopeFilter, state.filters.scope, "全部作用域");
    }
    if (refs.exampleRecallScope && document.body.contains(refs.exampleRecallScope)) {
      const current = refs.exampleRecallScope.value;
      const initialScope = state.scopeOptions.find((item) => item.type !== "global") || state.scopeOptions.find((item) => item.type === "global");
      fillScopeSelect(refs.exampleRecallScope, current || (initialScope ? scopeOptionValue(initialScope) : ""), "选择作用域");
    }
  }

  function field(label, control, hint) {
    const wrap = element("label", "knowledge-field");
    wrap.append(element("span", "knowledge-field-label", label), control);
    if (hint) wrap.append(element("small", "", hint));
    return wrap;
  }

  function input(type, maxLength) {
    const control = element("input", "knowledge-input");
    control.type = type;
    if (maxLength) control.maxLength = maxLength;
    return control;
  }

  function textArea(rows, maxLength) {
    const control = element("textarea", "knowledge-textarea");
    control.rows = rows;
    control.maxLength = maxLength;
    return control;
  }

  function listToolbar() {
    const toolbar = element("div", "knowledge-toolbar example-list-toolbar");
    const search = input("search", 300);
    search.className = "knowledge-search";
    search.placeholder = "搜索标题、对话、理想回复或标签";
    search.value = state.filters.search;
    search.addEventListener("input", () => {
      state.filters.search = search.value.trim();
      global.clearTimeout(state.searchTimer);
      state.searchTimer = global.setTimeout(() => { state.page = 1; loadExamples(); }, 260);
    });
    const status = element("select", "knowledge-select");
    status.append(option("", "全部状态"), option("approved", "已审核"), option("draft", "草稿"), option("rejected", "已拒绝"), option("tombstoned", "已删除"));
    if (state.tab === "review" && !state.filters.status) state.filters.status = "draft";
    status.value = state.filters.status;
    status.addEventListener("change", () => { state.filters.status = status.value; state.page = 1; loadExamples(); });
    const enabled = element("select", "knowledge-select");
    enabled.append(option("", "全部启用状态"), option("true", "允许召回"), option("false", "禁止召回"));
    enabled.value = state.filters.enabled;
    enabled.addEventListener("change", () => { state.filters.enabled = enabled.value; state.page = 1; loadExamples(); });
    const scope = element("select", "knowledge-select");
    refs.exampleScopeFilter = scope;
    fillScopeSelect(scope, state.filters.scope, "全部作用域");
    scope.addEventListener("change", () => { state.filters.scope = scope.value; state.page = 1; loadExamples(); });
    const agent = input("search", 200);
    agent.className = "knowledge-search compact";
    agent.placeholder = "Agent";
    agent.value = state.filters.agent;
    agent.addEventListener("change", () => { state.filters.agent = agent.value.trim(); state.page = 1; loadExamples(); });
    const topic = input("search", 200);
    topic.className = "knowledge-search compact";
    topic.placeholder = "主题";
    topic.value = state.filters.topic;
    topic.addEventListener("change", () => { state.filters.topic = topic.value.trim(); state.page = 1; loadExamples(); });
    const intent = input("search", 200);
    intent.className = "knowledge-search compact";
    intent.placeholder = "意图";
    intent.value = state.filters.intent;
    intent.addEventListener("change", () => { state.filters.intent = intent.value.trim(); state.page = 1; loadExamples(); });
    toolbar.append(search, status, enabled, scope, agent, topic, intent);
    return toolbar;
  }

  function renderList() {
    refs.content.replaceChildren();
    const head = element("header", "memory-panel-head");
    const copy = element("div");
    copy.append(
      element("h3", "", state.tab === "review" ? "候选审核" : "典型短对话"),
      element("p", "", state.tab === "review" ? "未审核样例不会注入模型。请先检查隐私、事实和回复质量。" : "样例只提供表达参考，当前消息、规则和事实始终优先。"),
    );
    head.append(copy, element("span", "runtime-panel-badge", `共 ${state.total} 条`));
    const list = element("div", "knowledge-list example-list");
    if (!state.items.length) list.append(element("div", "dynamic-empty", state.tab === "review" ? "暂无待审核样例" : "暂无符合条件的回复样例"));
    else state.items.forEach((raw) => {
      const item = normalizeExample(raw);
      const card = element("button", "knowledge-list-card example-list-card");
      card.type = "button";
      const cardHead = element("span", "knowledge-list-card-head");
      cardHead.append(element("span", "knowledge-list-title", item.title || item.topic || `回复样例 #${item.id}`), statusBadge(item.status, item.enabled));
      const dialogue = element("span", "example-dialogue-preview");
      item.turns.forEach((turn) => {
        const line = element("span", "");
        line.append(element("b", "", turn.role === "assistant" ? "Agent" : "用户"), document.createTextNode(`：${turn.content || "（空）"}`));
        dialogue.append(line);
      });
      const reply = element("span", "example-ideal-preview");
      reply.append(element("b", "", "理想回复"), document.createTextNode(`：${item.idealReply || "（空）"}`));
      const meta = element("span", "knowledge-list-meta");
      [item.scope.label, item.agentId || "default", item.topic || "未标主题", item.intent || "未标意图", `质量 ${Math.round(item.qualityScore * 100)}%`, `使用 ${item.usageCount} 次`].forEach((text) => meta.append(element("span", "", text)));
      [...item.styleTags, ...item.keywords].slice(0, 8).forEach((tag) => meta.append(element("span", "knowledge-tag", tag)));
      card.append(cardHead, dialogue, reply, meta);
      card.addEventListener("click", () => loadExampleDetail(item.id, card));
      list.append(card);
    });
    const pagination = element("footer", "knowledge-pagination");
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    const controls = element("div");
    const previous = element("button", "memory-action", "上一页");
    previous.type = "button";
    previous.disabled = state.page <= 1;
    previous.addEventListener("click", () => { state.page -= 1; loadExamples(); });
    const next = element("button", "memory-action", "下一页");
    next.type = "button";
    next.disabled = state.page >= totalPages;
    next.addEventListener("click", () => { state.page += 1; loadExamples(); });
    const pageSize = element("select", "knowledge-select page-size");
    [10, 20, 50].forEach((size) => pageSize.append(option(String(size), `${size} 条/页`)));
    pageSize.value = String(state.pageSize);
    pageSize.addEventListener("change", () => { state.pageSize = numberValue(pageSize.value, 20); state.page = 1; loadExamples(); });
    controls.append(previous, next, pageSize);
    pagination.append(element("span", "", `第 ${state.page} / ${totalPages} 页`), controls);
    refs.content.append(head, listToolbar(), list, pagination);
    refreshIcons();
  }

  async function loadExamples() {
    const requestId = ++state.listRequestId;
    refs.content.replaceChildren(element("div", "dynamic-loading", "正在读取回复样例…"));
    const scope = parseScope(state.filters.scope);
    try {
      const payload = await api.getReplyExamples({
        search: state.filters.search,
        status: state.filters.status,
        enabled: state.filters.enabled,
        scope_type: scope.type,
        scope_token: scope.token,
        agent_id: state.filters.agent,
        topic: state.filters.topic,
        intent: state.filters.intent,
        review: state.tab === "review" ? "true" : "",
        page: state.page,
        page_size: state.pageSize,
      });
      if (requestId !== state.listRequestId || !document.body.contains(state.workspace)) return;
      const normalized = collection(payload, ["examples"]);
      state.items = normalized.items;
      state.total = normalized.total;
      state.stats = normalized.source.stats || normalized.source.counts || state.stats;
      const rawScopes = Array.isArray(normalized.source.scope_options) ? normalized.source.scope_options : Array.isArray(normalized.source.scopes) ? normalized.source.scopes : [];
      if (rawScopes.length) {
        state.scopeOptions = rawScopes.map((item) => ({
          type: String(item && (item.type || item.scope_type) || ""),
          token: String(item && (item.token || item.scope_token) || ""),
          label: String(item && (item.label || item.scope_label) || "受限作用域"),
        })).filter((item) => item.token || item.type === "global");
      }
      renderMetrics();
      const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
      if (state.page > totalPages) {
        state.page = totalPages;
        return loadExamples();
      }
      renderList();
    } catch (error) {
      if (requestId !== state.listRequestId) return;
      refs.content.replaceChildren(element("div", "dynamic-loading error", error.message || "回复样例加载失败"));
    }
  }

  async function loadOverviewStats(epoch) {
    try {
      const payload = await api.getMemoryOverview();
      if (epoch !== state.viewEpoch || !document.body.contains(state.workspace)) return;
      if (payload && payload.reply_examples && typeof payload.reply_examples === "object") state.stats = payload.reply_examples;
      const rawScopes = payload && Array.isArray(payload.scope_options) ? payload.scope_options : [];
      if (rawScopes.length) {
        state.scopeOptions = rawScopes.map((item) => ({
          type: String(item && (item.type || item.scope_type) || ""),
          token: String(item && (item.token || item.scope_token) || ""),
          label: String(item && (item.label || item.scope_label) || "受限作用域"),
        })).filter((item) => item.token);
      }
      refreshScopeControls();
      renderMetrics();
    } catch {
      // The list remains fully usable when overview statistics are unavailable.
    }
  }

  function closeDrawer() {
    const backdrop = document.querySelector(".knowledge-drawer-backdrop[data-owner='examples']");
    if (backdrop) backdrop.remove();
    if (state.drawerKeyHandler) document.removeEventListener("keydown", state.drawerKeyHandler);
    state.drawerKeyHandler = null;
    document.body.classList.remove("knowledge-drawer-open");
    if (state.lastFocus && document.body.contains(state.lastFocus)) state.lastFocus.focus();
    state.lastFocus = null;
    state.detail = null;
  }

  function drawer(title) {
    closeDrawer();
    state.lastFocus = document.activeElement;
    const backdrop = element("div", "knowledge-drawer-backdrop");
    backdrop.dataset.owner = "examples";
    const panel = element("aside", "knowledge-drawer example-drawer");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", title);
    panel.tabIndex = -1;
    const head = element("header", "knowledge-drawer-head");
    const copy = element("div");
    copy.append(element("span", "knowledge-drawer-eyebrow", "典型短对话"), element("h3", "", title));
    const close = element("button", "icon-button");
    close.type = "button";
    close.setAttribute("aria-label", "关闭回复样例抽屉");
    close.append(icon("x"));
    close.addEventListener("click", closeDrawer);
    head.append(copy, close);
    const body = element("div", "knowledge-drawer-body");
    panel.append(head, body);
    backdrop.append(panel);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) closeDrawer(); });
    state.drawerKeyHandler = (event) => {
      if (event.key === "Escape" && document.body.contains(backdrop)) {
        closeDrawer();
        return;
      }
      if (event.key === "Tab" && document.body.contains(backdrop)) {
        const focusable = [...panel.querySelectorAll("button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex='-1'])")];
        if (!focusable.length) {
          event.preventDefault();
          panel.focus();
          return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", state.drawerKeyHandler);
    document.body.append(backdrop);
    document.body.classList.add("knowledge-drawer-open");
    close.focus();
    refreshIcons();
    return body;
  }

  async function loadExampleDetail(id, trigger) {
    if (!id) return;
    state.lastFocus = trigger || document.activeElement;
    const body = drawer(`回复样例 #${id}`);
    body.append(element("div", "dynamic-loading", "正在读取完整样例…"));
    const requestId = ++state.detailRequestId;
    try {
      const payload = await api.getReplyExampleDetail(id);
      if (requestId !== state.detailRequestId || !document.body.contains(body)) return;
      state.detail = normalizeExample(payload);
      renderEditor(body, state.detail);
    } catch (error) {
      if (requestId !== state.detailRequestId) return;
      body.replaceChildren(element("div", "dynamic-loading error", error.message || "回复样例详情加载失败"));
    }
  }

  function renderTurns(container, initialTurns) {
    const turns = normalizeTurns(initialTurns);
    const list = element("div", "example-turn-list");
    const add = element("button", "text-button");
    add.type = "button";
    add.append(icon("plus"), element("span", "", "增加一轮"));

    const update = () => {
      add.disabled = list.children.length >= 3;
      [...list.children].forEach((row) => {
        const remove = row.querySelector("[data-remove-turn]");
        if (remove) remove.disabled = list.children.length <= 1;
      });
    };
    const appendTurn = (turn) => {
      if (list.children.length >= 3) return;
      const row = element("article", "example-turn-row");
      const rowHead = element("header");
      const role = element("select", "knowledge-select");
      role.append(option("user", "用户"), option("assistant", "Agent"));
      role.value = turn && turn.role === "assistant" ? "assistant" : "user";
      const remove = element("button", "text-button");
      remove.type = "button";
      remove.dataset.removeTurn = "true";
      remove.append(icon("trash-2"), element("span", "", "移除"));
      remove.addEventListener("click", () => { row.remove(); update(); });
      rowHead.append(role, remove);
      const content = textArea(4, 3000);
      content.placeholder = "输入这一轮的短消息";
      content.required = true;
      content.value = turn && turn.content || "";
      row.append(rowHead, content);
      list.append(row);
      update();
      refreshIcons();
    };
    turns.forEach(appendTurn);
    add.addEventListener("click", () => appendTurn({ role: list.children.length % 2 ? "assistant" : "user", content: "" }));
    container.append(list, add);
    update();
    return {
      list,
      value() {
        return [...list.children].map((row) => ({
          role: row.querySelector("select").value,
          content: row.querySelector("textarea").value.trim(),
        }));
      },
    };
  }

  function recordSection(container, title, records, emptyText) {
    const section = element("section", "knowledge-detail-section");
    section.append(element("h4", "", title));
    const list = element("div", "knowledge-record-list");
    if (!records.length) list.append(element("div", "dynamic-empty", emptyText));
    else records.forEach((record) => {
      const source = record && typeof record === "object" ? record : { content: record };
      const card = element("article", "knowledge-record");
      const head = element("header");
      head.append(element("strong", "", source.action || source.status || source.request_id || "记录"), element("time", "", formatTime(source.created_at || source.updated_at)));
      const content = source.content ?? source.reason ?? source.detail ?? source.query ?? source;
      card.append(head, element("pre", "knowledge-record-content", typeof content === "string" ? content : JSON.stringify(content, null, 2)));
      list.append(card);
    });
    section.append(list);
    container.append(section);
  }

  function renderEditor(body, item) {
    body.replaceChildren();
    const summary = element("dl", "memory-definition-list knowledge-editor-summary");
    [
      ["状态", (STATUS_LABELS[item.enabled ? item.status : "disabled"] || [item.status])[0]],
      ["Agent", item.agentId || "default"],
      ["版本", item.version],
      ["使用次数", item.usageCount],
      ["来源", item.sourceType],
      ["Context Run", item.sourceContextRunId || "--"],
      ["更新时间", formatTime(item.updatedAt)],
    ].forEach(([label, value]) => { const row = element("div"); row.append(element("dt", "", label), element("dd", "", value)); summary.append(row); });

    const form = element("form", "knowledge-form example-editor-form");
    const title = input("text", 200);
    title.value = item.title;
    title.required = true;
    const scope = element("select", "knowledge-select wide");
    const selectedScope = item.scope.token ? scopeOptionValue(item.scope) : "";
    fillScopeSelect(scope, selectedScope, "选择作用域");
    scope.required = true;
    const agent = input("text", 200);
    agent.value = item.agentId || "default";
    agent.required = true;
    const topic = input("text", 200);
    topic.value = item.topic;
    const intent = input("text", 200);
    intent.value = item.intent;
    const styles = textArea(3, 1000);
    styles.value = item.styleTags.join("\n");
    const keywords = textArea(3, 1000);
    keywords.value = item.keywords.join("\n");
    const turnField = element("section", "knowledge-field example-turn-field");
    turnField.append(element("span", "knowledge-field-label", "参考对话（1-3 轮）"), element("small", "", "每轮明确 user/assistant；理想回复在下方单独填写。"));
    const turnEditor = renderTurns(turnField, item.turns);
    const idealReply = textArea(7, 6000);
    idealReply.value = item.idealReply;
    idealReply.required = true;
    const conditions = textArea(3, 1500);
    conditions.value = item.conditions;
    const exclusions = textArea(3, 1500);
    exclusions.value = item.exclusions;
    const notes = textArea(3, 1500);
    notes.value = item.notes;
    const quality = input("number");
    quality.min = "0";
    quality.max = "1";
    quality.step = "0.01";
    quality.value = String(Math.max(0, Math.min(1, item.qualityScore)));
    const reason = textArea(3, 1000);
    reason.value = item.reviewReason;
    form.append(
      field("标题", title),
      field("作用域", scope, "私聊或群专用样例不能跨域使用。"),
      field("适用 Agent", agent, "填写具体 Agent ID；只有显式填写 * 才会跨 Agent 共享。"),
      field("主题", topic),
      field("意图", intent),
      field("风格标签（每行一个）", styles),
      field("关键词（每行一个）", keywords),
      turnField,
      field("理想 Agent 回复", idealReply, "只参考表达和处理方式，不照抄事实、名字和时间。"),
      field("适用条件", conditions, "可选；每行或逗号分隔一个关键词，当前消息命中任意一项才会使用。"),
      field("禁用条件", exclusions, "可选；每行或逗号分隔一个关键词，当前消息命中任意一项就会排除。"),
      field("备注", notes),
      field("质量评分", quality),
      field("审核说明", reason),
    );

    const controls = { title, scope, agent, topic, intent, styles, keywords, turnEditor, idealReply, conditions, exclusions, notes, quality, reason };
    const actions = element("footer", "knowledge-drawer-actions");
    const save = element("button", "secondary-button");
    save.type = "submit";
    save.append(icon("save"), element("span", "", item.id ? "保存修改" : "创建草稿"));
    actions.append(save);
    if (item.id && ["draft", "candidate"].includes(item.status)) {
      const approve = element("button", "primary-button");
      approve.type = "button";
      approve.append(icon("badge-check"), element("span", "", "审核通过"));
      approve.addEventListener("click", () => submitAction("approve", item, form, controls));
      const reject = element("button", "text-button");
      reject.type = "button";
      reject.append(icon("ban"), element("span", "", "拒绝"));
      reject.addEventListener("click", () => submitAction("reject", item, form, controls));
      actions.append(approve, reject);
    }
    if (item.id) {
      const restoreRejected = ["rejected", "tombstoned"].includes(item.status);
      if (restoreRejected) {
        const restore = element("button", "text-button");
        restore.type = "button";
        restore.append(icon("rotate-ccw"), element("span", "", "恢复为草稿"));
        restore.addEventListener("click", () => submitAction("restore", item, form, controls));
        actions.append(restore);
      } else if (["approved", "active"].includes(item.status)) {
        const toggle = element("button", "text-button");
        toggle.type = "button";
        toggle.append(icon(item.enabled ? "pause" : "play"), element("span", "", item.enabled ? "停用" : "重新启用"));
        toggle.addEventListener("click", () => submitAction(item.enabled ? "disable" : "enable", item, form, controls));
        actions.append(toggle);
      }
      const remove = element("button", "danger-button");
      remove.type = "button";
      remove.append(icon("trash-2"), element("span", "", "删除"));
      remove.addEventListener("click", () => {
        if (global.confirm("确定删除这个回复样例吗？")) submitAction("delete", item, form, controls);
      });
      actions.append(remove);
    }
    form.append(actions);
    form.addEventListener("submit", (event) => { event.preventDefault(); submitAction(item.id ? "update" : "create", item, form, controls); });
    body.append(summary, form);
    recordSection(body, "使用记录", item.usage, "暂无使用记录");
    recordSection(body, "Revision 历史", item.revisions, "暂无历史版本");
    recordSection(body, "审核与修改记录", item.audit, "暂无审计记录");
    refreshIcons();
  }

  function openExampleDrawer(example) {
    if (!example && !state.scopeOptions.length) {
      state.notify("作用域尚未加载，请稍后再试", "error");
      return;
    }
    const item = example ? normalizeExample(example) : normalizeExample({
      status: "draft",
      enabled: false,
      agent_id: "default",
      turns: [{ role: "user", content: "" }],
      quality_score: 0.8,
    });
    if (!example) {
      const restrictedScope = state.scopeOptions.find((scope) => scope.type !== "global");
      item.scope = restrictedScope
        ? { ...restrictedScope }
        : { type: "", token: "", label: "" };
    }
    state.detail = item;
    const body = drawer(item.id ? `回复样例 #${item.id}` : "新增回复样例");
    renderEditor(body, item);
  }

  function actionPayload(action, item, controls) {
    const payload = {
      action,
      id: item.id,
      version: item.version,
      reason: controls.reason.value.trim(),
    };
    if (["create", "update", "approve"].includes(action)) {
      const scope = parseScope(controls.scope.value);
      Object.assign(payload, {
        title: controls.title.value.trim(),
        status: item.status,
        enabled: item.enabled,
        scope_type: scope.type,
        scope_token: scope.token,
        agent_id: controls.agent.value.trim(),
        topic: controls.topic.value.trim(),
        intent: controls.intent.value.trim(),
        style_tags: arrayValue(controls.styles.value),
        keywords: arrayValue(controls.keywords.value),
        turns: controls.turnEditor.value(),
        ideal_reply: controls.idealReply.value.trim(),
        conditions: controls.conditions.value.trim(),
        exclusions: controls.exclusions.value.trim(),
        notes: controls.notes.value.trim(),
        quality_score: numberValue(controls.quality.value, item.qualityScore),
      });
    }
    return payload;
  }

  async function submitAction(action, item, form, controls) {
    if (state.pending) return;
    const writesEditor = ["create", "update", "approve"].includes(action);
    if (writesEditor && !form.reportValidity()) return;
    const payload = actionPayload(action, item, controls);
    if (writesEditor && (payload.turns.length < 1 || payload.turns.length > 3 || payload.turns.some((turn) => !turn.content))) {
      state.notify("回复样例必须包含 1-3 轮非空对话", "error");
      return;
    }
    const introducesGlobalScope = payload.scope_type === "global" && (action === "create" || item.scope.type !== "global");
    if (introducesGlobalScope && !global.confirm("全局样例会对这个 Agent 的所有聊天生效，确定继续吗？")) return;
    state.pending = true;
    form.querySelectorAll("button, input, textarea, select").forEach((control) => { control.disabled = true; });
    try {
      const response = await api.replyExampleAction(payload);
      state.notify(action === "delete" ? "回复样例已删除" : action === "approve" ? "回复样例已审核通过" : action === "reject" ? "回复样例已拒绝" : "回复样例已保存", "success");
      if (action === "delete") closeDrawer();
      else {
        const responseHasDetail = response && (response.detail || response.item || response.example);
        const detailPayload = responseHasDetail ? response : await api.getReplyExampleDetail(response && response.id ? response.id : item.id);
        const normalized = normalizeExample(detailPayload);
        if (normalized.id) {
          state.detail = normalized;
          renderEditor(form.parentElement, normalized);
        } else closeDrawer();
      }
      await loadExamples();
    } catch (error) {
      state.notify(error.message || "回复样例操作失败", "error");
      form.querySelectorAll("button, input, textarea, select").forEach((control) => { control.disabled = false; });
    } finally {
      state.pending = false;
    }
  }

  function recallResult(raw) {
    const item = normalizeExample(raw.item || raw.example || raw);
    const card = element("article", "knowledge-recall-result example-recall-result");
    const head = element("header");
    head.append(element("strong", "", item.title || item.topic || `回复样例 #${item.id}`), statusBadge(item.status, item.enabled));
    card.append(head);
    item.turns.forEach((turn) => {
      const line = element("p", "example-recall-turn");
      line.append(element("b", "", turn.role === "assistant" ? "Agent" : "用户"), document.createTextNode(`：${turn.content}`));
      card.append(line);
    });
    const ideal = element("p", "example-recall-ideal");
    ideal.append(element("b", "", "理想回复"), document.createTextNode(`：${item.idealReply}`));
    card.append(ideal);
    const meta = element("div", "knowledge-list-meta");
    meta.append(element("span", "", `总分 ${numberValue(raw.score ?? raw.final_score, 0).toFixed(3)}`), element("span", "", `质量 ${Math.round(item.qualityScore * 100)}%`));
    const scores = raw.scores && typeof raw.scores === "object" ? raw.scores : raw.score_components && typeof raw.score_components === "object" ? raw.score_components : {};
    Object.entries(scores).forEach(([key, value]) => meta.append(element("span", "", `${key} ${numberValue(value, 0).toFixed(3)}`)));
    if (raw.filter_reason || raw.reason) meta.append(element("span", "", `原因：${raw.filter_reason || raw.reason}`));
    card.append(meta);
    return card;
  }

  function renderRecall() {
    refs.content.replaceChildren();
    const head = element("header", "memory-panel-head");
    const copy = element("div");
    copy.append(element("h3", "", "回复样例召回测试"), element("p", "", "只查找可用样例并展示排序，不调用 Chat Provider，也不会直接发送旧回复。"));
    head.append(copy, element("span", "runtime-state-badge ready", "只读测试"));
    const form = element("form", "knowledge-form recall-debug-form");
    const query = textArea(4, 4000);
    query.placeholder = "输入一条模拟用户消息";
    query.required = true;
    const scope = element("select", "knowledge-select wide");
    refs.exampleRecallScope = scope;
    const initialScope = state.scopeOptions.find((item) => item.type !== "global") || state.scopeOptions.find((item) => item.type === "global");
    fillScopeSelect(scope, initialScope ? scopeOptionValue(initialScope) : "", "选择作用域");
    scope.required = true;
    const agent = element("select", "knowledge-select wide");
    refs.exampleRecallPersona = agent;
    fillPersonaSelect(agent, "");
    agent.addEventListener("change", () => { agent.dataset.touched = "true"; });
    agent.required = true;
    const limit = input("number");
    limit.min = "1";
    limit.max = "10";
    limit.value = "3";
    const submit = element("button", "primary-button");
    submit.type = "submit";
    submit.append(icon("search-check"), element("span", "", "测试召回"));
    form.append(field("模拟消息", query), field("作用域", scope), field("人格上下文", agent, "选择 AstrBot 当前会话最终生效的人格；选项来自人格配置和插件历史数据，不是平台名。"), field("最多结果", limit), submit);
    const results = element("div", "knowledge-recall-results");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.pending || !form.reportValidity()) return;
      if (agent.value.trim() === "*") {
        state.notify("召回测试必须填写具体 Agent，不能使用 *", "error");
        agent.focus();
        return;
      }
      const selectedScope = parseScope(scope.value);
      state.pending = true;
      submit.disabled = true;
      results.replaceChildren(element("div", "dynamic-loading", "正在查找典型短对话…"));
      try {
        const payload = await api.debugReplyExamples({ query: query.value.trim(), scope_type: selectedScope.type, scope_token: selectedScope.token, agent_id: agent.value.trim(), limit: numberValue(limit.value, 3) });
        const normalized = collection(payload, ["candidates", "results", "examples"]);
        results.replaceChildren(element("p", "knowledge-debug-summary", `返回 ${normalized.items.length} 条；这些内容只会进入 ReplyExamples 参考块。`));
        if (!normalized.items.length) results.append(element("div", "dynamic-empty", "没有符合当前作用域、人格上下文和审核状态的样例"));
        else normalized.items.forEach((item) => results.append(recallResult(item)));
        const filtered = payload && Array.isArray(payload.filtered) ? payload.filtered : [];
        if (filtered.length) {
          results.append(element("h4", "", "已过滤候选"));
          filtered.forEach((item) => results.append(recallResult(item)));
        }
      } catch (error) {
        results.replaceChildren(element("div", "runtime-status-notice error", error.message || "回复样例召回测试失败"));
      } finally {
        state.pending = false;
        submit.disabled = false;
        refreshIcons();
      }
    });
    refs.content.append(head, form, results);
    refreshIcons();
  }

  function switchTab(tab) {
    state.tab = tab;
    state.page = 1;
    if (tab === "review") state.filters.status = "draft";
    else if (tab === "library" && state.filters.status === "draft") state.filters.status = "";
    renderTabs();
    if (tab === "recall") renderRecall();
    else loadExamples();
  }

  function open() {
    if (!state.root) return false;
    state.viewEpoch += 1;
    const epoch = state.viewEpoch;
    closeDrawer();
    buildShell();
    if (state.tab === "recall") renderRecall();
    else loadExamples();
    loadOverviewStats(epoch);
    loadPersonaOptions(epoch);
    return true;
  }

  function close() {
    state.viewEpoch += 1;
    state.listRequestId += 1;
    state.detailRequestId += 1;
    global.clearTimeout(state.searchTimer);
    closeDrawer();
  }

  global.HumanizeExamples = Object.freeze({
    mount(target, options) {
      state.root = target;
      if (options && typeof options.notify === "function") state.notify = options.notify;
    },
    open,
    close,
  });
})(window, document);
