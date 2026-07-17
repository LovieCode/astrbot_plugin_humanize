(function initializeHumanizeMemory(global, document) {
  "use strict";

  const api = global.HumanizeApi;
  const TYPE_LABELS = {
    profile: "用户资料",
    preferences: "用户偏好",
    preference: "用户偏好",
    entities: "人物与项目",
    entity: "人物与项目",
    events: "重要事件",
    event: "重要事件",
  };
  const STATUS_LABELS = {
    active: ["已启用", "ready"],
    confirmed: ["已确认", "ready"],
    candidate: ["待审核", "warning"],
    pending: ["待审核", "warning"],
    conflict: ["有冲突", "error"],
    rejected: ["已拒绝", "empty"],
    disabled: ["已停用", "empty"],
    superseded: ["已替代", "empty"],
    tombstone: ["已删除", "error"],
    tombstoned: ["已删除", "error"],
    dead: ["已终止", "error"],
    running: ["运行中", "ready"],
    retry: ["待重试", "warning"],
    completed: ["已完成", "ready"],
  };

  const state = {
    root: null,
    workspace: null,
    notify: function noop() {},
    overview: {},
    personaOptions: [{ id: "default", label: "默认人格", configured: true, debuggable: true }],
    personaDefaultId: "default",
    items: [],
    total: 0,
    page: 1,
    pageSize: 20,
    tab: "memories",
    filters: { search: "", type: "", status: "", scopeToken: "", agent: "" },
    jobs: [],
    jobsTotal: 0,
    jobsStatus: "",
    jobsType: "",
    jobsAgent: "",
    jobsPage: 1,
    jobsPageSize: 20,
    detail: null,
    pending: false,
    listRequestId: 0,
    detailRequestId: 0,
    jobsRequestId: 0,
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
    if (global.lucide && typeof global.lucide.createIcons === "function") {
      global.lucide.createIcons();
    }
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

  function formatScore(value) {
    const score = numberValue(value, null);
    if (score === null) return "--";
    return `${Math.round(Math.max(0, Math.min(1, score > 1 ? score / 100 : score)) * 100)}%`;
  }

  function collection(payload, names) {
    if (Array.isArray(payload)) return { items: payload, total: payload.length };
    const source = payload && typeof payload === "object" ? payload : {};
    const keys = ["items", ...(names || [])];
    let items = [];
    for (const key of keys) {
      if (Array.isArray(source[key])) {
        items = source[key];
        break;
      }
    }
    return {
      items,
      total: numberValue(source.total ?? source.count, items.length),
    };
  }

  function scopeInfo(source) {
    const scope = source && source.scope && typeof source.scope === "object" ? source.scope : {};
    return {
      type: String(scope.type || source.scope_type || "global"),
      token: String(scope.token || source.scope_token || ""),
      label: String(scope.label || source.scope_label || (source.scope_type === "global" ? "全局" : "受限作用域")),
    };
  }

  function normalizeMemory(value) {
    const envelope = value && typeof value === "object" ? value : {};
    const source = envelope.detail && typeof envelope.detail === "object"
      ? envelope.detail
      : envelope.item && typeof envelope.item === "object"
        ? envelope.item
        : envelope.memory && typeof envelope.memory === "object"
          ? envelope.memory
          : envelope;
    const scope = scopeInfo(source);
    return {
      id: source.id ?? source.memory_id ?? null,
      memoryKey: String(source.memory_key || source.key || ""),
      type: String(source.type || source.memory_type || "profile").toLowerCase(),
      status: String(source.status || source.state || "candidate").toLowerCase(),
      content: String(source.content || source.canonical_text || source.text || source.summary || source.value || ""),
      preview: String(source.content_preview || source.preview || source.content || source.canonical_text || source.text || source.summary || ""),
      structuredValue: source.structured_value ?? source.value_json ?? null,
      scope,
      agentId: String(source.agent_id || "default"),
      subject: String(source.subject_label || source.subject || source.subject_key || ""),
      confidence: numberValue(source.confidence, 0),
      importance: numberValue(source.importance, 0),
      revision: numberValue(source.revision ?? source.version, 0),
      evidenceCount: numberValue(source.evidence_count, Array.isArray(envelope.evidence) ? envelope.evidence.length : 0),
      recallCount: numberValue(source.recall_count, 0),
      createdAt: String(source.created_at || ""),
      updatedAt: String(source.updated_at || source.last_seen_at || ""),
      reason: String(source.reason || source.review_reason || ""),
      evidence: Array.isArray(envelope.evidence) ? envelope.evidence : Array.isArray(source.evidence) ? source.evidence : [],
      revisions: Array.isArray(envelope.revisions) ? envelope.revisions : Array.isArray(source.revisions) ? source.revisions : [],
      conflicts: Array.isArray(envelope.conflicts) ? envelope.conflicts : Array.isArray(source.conflicts) ? source.conflicts : [],
      recallLogs: Array.isArray(envelope.recall_logs) ? envelope.recall_logs : Array.isArray(source.recall_logs) ? source.recall_logs : [],
      audit: Array.isArray(envelope.audit) ? envelope.audit : Array.isArray(source.audit) ? source.audit : [],
    };
  }

  function statusBadge(status) {
    const normalized = String(status || "candidate").toLowerCase();
    const meta = STATUS_LABELS[normalized] || [normalized || "未知", "empty"];
    return element("span", `runtime-state-badge ${meta[1]}`, meta[0]);
  }

  function header() {
    const node = element("header", "dynamic-header knowledge-page-header");
    const mark = element("span", "dynamic-header-icon");
    mark.append(icon("brain"));
    const copy = element("div");
    copy.append(
      element("h2", "", "长期记忆"),
      element("p", "", "只保存聊天需要的事实、偏好、人物和事件；事实源为内置 OpenViking workspace。"),
    );
    const refresh = element("button", "memory-action");
    refresh.type = "button";
    refresh.append(icon("refresh-cw"), element("span", "", "刷新本地状态"));
    refresh.addEventListener("click", () => refreshCurrentView());
    node.append(mark, copy, refresh);
    return node;
  }

  function metric(label, value, hint) {
    const card = element("article", "dynamic-metric");
    card.append(element("span", "", label), element("strong", "", value), element("small", "", hint));
    return card;
  }

  function overviewCounts() {
    const source = state.overview && typeof state.overview === "object" ? state.overview : {};
    const memoryStats = source.memories && typeof source.memories === "object" ? source.memories : source;
    const counts = memoryStats.by_status && typeof memoryStats.by_status === "object" ? memoryStats.by_status : memoryStats.counts && typeof memoryStats.counts === "object" ? memoryStats.counts : memoryStats;
    return {
      active: numberValue(counts.active ?? counts.confirmed, 0),
      candidate: numberValue(counts.candidate ?? counts.pending, 0),
      conflict: numberValue(counts.conflict, 0),
      dead: numberValue(counts.dead, 0) + numberValue(counts.tombstone ?? counts.tombstoned, 0) + numberValue(counts.rejected, 0) + numberValue(counts.superseded, 0),
    };
  }

  function renderOverview() {
    if (!refs.metrics || !refs.localStatus) return;
    const counts = overviewCounts();
    refs.metrics.replaceChildren(
      metric("有效记忆", counts.active, "可参与召回"),
      metric("待审核", counts.candidate, "需要人工确认"),
      metric("冲突", counts.conflict, "新旧信息不一致"),
      metric("终止", counts.dead, "拒绝、替代或删除"),
    );
    const source = state.overview || {};
    const retrieval = source.retrieval && typeof source.retrieval === "object" ? source.retrieval : source.index && typeof source.index === "object" ? source.index : {};
    const jobStats = source.jobs && typeof source.jobs === "object" ? source.jobs : {};
    const jobs = jobStats.by_status && typeof jobStats.by_status === "object" ? jobStats.by_status : jobStats;
    const runtime = source.runtime && typeof source.runtime === "object" ? source.runtime : {};
    const ftsAvailable = booleanValue(retrieval.fts5_available ?? source.fts5_available ?? source.fts_available, null);
    const statusRows = [
      ["事实源", "OpenViking workspace"],
      ["关键词检索", ftsAvailable === null ? "未知" : ftsAvailable ? "FTS5 可用" : "降级为精确匹配"],
      ["索引代次", retrieval.generation || retrieval.active_generation || retrieval.index_generation || "未启用向量索引"],
      ["后台任务", `${numberValue(jobs.pending, 0)} 待处理 · ${numberValue(jobs.running, 0)} 运行中 · ${numberValue(jobs.dead, 0)} 终止`],
      ["最近召回", formatTime(source.last_recall_at || retrieval.last_recall_at || runtime.last_recall_at)],
    ];
    const list = element("dl", "memory-definition-list");
    statusRows.forEach(([label, value]) => {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      list.append(row);
    });
    refs.localStatus.replaceChildren(list, element("p", "memory-embedded-note", "页面刷新只读取本地状态，不会调用 Chat、Embedding 或 Rerank Provider。"));
    renderScopeOptions();
    refreshIcons();
  }

  function scopeOptions() {
    const source = state.overview || {};
    const raw = Array.isArray(source.scope_options) ? source.scope_options : Array.isArray(source.scopes) ? source.scopes : [];
    return raw.map((item) => ({
      token: String(item && (item.token || item.scope_token) || ""),
      type: String(item && (item.type || item.scope_type) || ""),
      label: String(item && (item.label || item.scope_label) || "受限作用域"),
    })).filter((item) => item.token || item.type === "global");
  }

  function fillScopeSelect(select, selected, requireSelection) {
    if (!select) return;
    select.replaceChildren(element("option", "", requireSelection ? "选择作用域" : "全部作用域"));
    select.firstChild.value = "";
    scopeOptions().forEach((scope) => {
      const option = element("option", "", scope.label);
      option.value = `${encodeURIComponent(scope.type)}:${encodeURIComponent(scope.token)}`;
      select.append(option);
    });
    select.value = selected || "";
  }

  function parseScopeSelection(value) {
    const text = String(value || "");
    const separator = text.indexOf(":");
    if (separator < 0) return { type: "", token: "" };
    try {
      return {
        type: decodeURIComponent(text.slice(0, separator)),
        token: decodeURIComponent(text.slice(separator + 1)),
      };
    } catch {
      return { type: "", token: "" };
    }
  }

  function renderScopeOptions() {
    if (refs.scopeFilter) fillScopeSelect(refs.scopeFilter, state.filters.scopeToken, false);
    if (refs.recallScope) {
      fillScopeSelect(refs.recallScope, refs.recallScope.value, true);
      if (!refs.recallScope.value) {
        const initialScope = scopeOptions().find((scope) => scope.type !== "global") || scopeOptions().find((scope) => scope.type === "global");
        if (initialScope) refs.recallScope.value = `${encodeURIComponent(initialScope.type)}:${encodeURIComponent(initialScope.token)}`;
      }
    }
  }

  function tabButton(key, label, iconName) {
    const button = element("button", `knowledge-tab${state.tab === key ? " active" : ""}`);
    button.type = "button";
    button.dataset.memoryTab = key;
    button.append(icon(iconName), element("span", "", label));
    button.addEventListener("click", () => switchTab(key));
    return button;
  }

  function renderTabs() {
    refs.tabs.replaceChildren(
      tabButton("memories", "记忆列表", "list"),
      tabButton("candidates", "候选审核", "badge-check"),
      tabButton("recall", "召回调试", "search-check"),
      tabButton("jobs", "后台任务", "list-todo"),
    );
    refreshIcons();
  }

  function buildShell() {
    state.root.replaceChildren(header());
    const workspace = element("section", "memory-workspace knowledge-workspace");
    workspace.dataset.humanizeMemory = "true";
    state.workspace = workspace;
    const metrics = element("section", "dynamic-metrics-grid knowledge-metrics");
    refs.metrics = metrics;
    const statusPanel = element("section", "dynamic-panel memory-status-panel");
    const statusHead = element("header", "memory-panel-head");
    const statusCopy = element("div");
    statusCopy.append(element("h3", "", "内置记忆状态"), element("p", "", "本地事实源、检索降级和后台任务健康。"));
    statusHead.append(statusCopy, statusBadge("active"));
    refs.localStatus = element("div", "memory-status-summary");
    statusPanel.append(statusHead, refs.localStatus);
    refs.tabs = element("nav", "knowledge-tabs");
    refs.content = element("section", "dynamic-panel knowledge-main-panel");
    workspace.append(metrics, statusPanel, refs.tabs, refs.content);
    state.root.append(workspace);
    renderTabs();
    refs.metrics.append(metric("有效记忆", "--", "正在读取"), metric("待审核", "--", "正在读取"), metric("冲突", "--", "正在读取"), metric("终止", "--", "正在读取"));
    refs.localStatus.append(element("div", "dynamic-loading", "正在读取本地记忆状态…"));
    refreshIcons();
  }

  async function loadOverview(epoch) {
    try {
      const payload = await api.getMemoryOverview();
      if (epoch !== state.viewEpoch || !document.body.contains(state.workspace)) return;
      state.overview = payload && typeof payload === "object" ? payload : {};
      renderOverview();
    } catch (error) {
      if (epoch !== state.viewEpoch || !document.body.contains(state.workspace)) return;
      refs.localStatus.replaceChildren(element("div", "runtime-status-notice error", error.message || "记忆状态读取失败"));
    }
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
      if (refs.memoryRecallPersona && document.body.contains(refs.memoryRecallPersona)) {
        const selected = refs.memoryRecallPersona.dataset.touched === "true" ? refs.memoryRecallPersona.value : "";
        fillPersonaSelect(refs.memoryRecallPersona, selected);
      }
    } catch {
      if (epoch !== state.viewEpoch) return;
      state.personaOptions = [{ id: "default", label: "默认人格", configured: true, debuggable: true }];
      state.personaDefaultId = "default";
      if (refs.memoryRecallPersona && document.body.contains(refs.memoryRecallPersona)) {
        fillPersonaSelect(refs.memoryRecallPersona, refs.memoryRecallPersona.value);
      }
    }
  }

  function field(label, control, hint) {
    const wrap = element("label", "knowledge-field");
    wrap.append(element("span", "knowledge-field-label", label), control);
    if (hint) wrap.append(element("small", "", hint));
    return wrap;
  }

  function listToolbar() {
    const toolbar = element("div", "knowledge-toolbar memory-list-toolbar");
    const search = element("input", "knowledge-search");
    search.type = "search";
    search.placeholder = "搜索记忆键或正文";
    search.value = state.filters.search;
    search.addEventListener("input", () => {
      state.filters.search = search.value.trim();
      global.clearTimeout(state.searchTimer);
      state.searchTimer = global.setTimeout(() => { state.page = 1; loadMemories(); }, 260);
    });
    const type = element("select", "knowledge-select");
    type.append(option("", "全部类型"), option("profile", "用户资料"), option("preference", "用户偏好"), option("entity", "人物与项目"), option("event", "重要事件"));
    type.value = state.filters.type;
    type.addEventListener("change", () => { state.filters.type = type.value; state.page = 1; loadMemories(); });
    const status = element("select", "knowledge-select");
    status.append(option("", "全部状态"), option("active", "已启用"), option("candidate", "待审核"), option("rejected", "已拒绝"), option("superseded", "已替代"), option("tombstoned", "已删除"));
    if (state.tab === "candidates" && !state.filters.status) state.filters.status = "candidate";
    status.value = state.filters.status;
    status.addEventListener("change", () => { state.filters.status = status.value; state.page = 1; loadMemories(); });
    const scope = element("select", "knowledge-select");
    refs.scopeFilter = scope;
    fillScopeSelect(scope, state.filters.scopeToken, false);
    scope.addEventListener("change", () => { state.filters.scopeToken = scope.value; state.page = 1; loadMemories(); });
    const agent = element("input", "knowledge-search compact");
    agent.type = "search";
    agent.placeholder = "Agent";
    agent.value = state.filters.agent;
    agent.addEventListener("change", () => { state.filters.agent = agent.value.trim(); state.page = 1; loadMemories(); });
    const create = element("button", "memory-action primary");
    create.type = "button";
    create.append(icon("plus"), element("span", "", "新增记忆"));
    create.addEventListener("click", () => openMemoryDrawer(null));
    toolbar.append(search, type, status, scope, agent, create);
    return toolbar;
  }

  function renderMemoryList() {
    const content = refs.content;
    content.replaceChildren();
    const head = element("header", "memory-panel-head");
    const copy = element("div");
    copy.append(
      element("h3", "", state.tab === "candidates" ? "候选审核" : "记忆列表"),
      element("p", "", state.tab === "candidates" ? "审核候选、修正冲突，不确定内容不会参与正常召回。" : "按作用域过滤后再检索，点击条目从抽屉查看完整证据。"),
    );
    head.append(copy, element("span", "runtime-panel-badge", `共 ${state.total} 条`));
    const list = element("div", "knowledge-list");
    if (!state.items.length) {
      list.append(element("div", "dynamic-empty", state.tab === "candidates" ? "暂无待审核记忆" : "暂无符合条件的记忆"));
    } else {
      state.items.forEach((raw) => {
        const item = normalizeMemory(raw);
        const card = element("button", "knowledge-list-card");
        card.type = "button";
        const cardHead = element("span", "knowledge-list-card-head");
        const title = element("span", "knowledge-list-title", item.memoryKey || item.content.slice(0, 72) || `记忆 #${item.id}`);
        cardHead.append(title, statusBadge(item.status));
        const preview = element("span", "knowledge-list-preview", item.preview || "（无正文）");
        const tags = element("span", "knowledge-list-meta");
        const metadata = [
          TYPE_LABELS[item.type] || item.type,
          item.scope.label,
          `Agent ${item.agentId || "default"}`,
          `置信度 ${formatScore(item.confidence)}`,
          `${item.evidenceCount} 条证据`,
          formatTime(item.updatedAt),
        ];
        if (item.subject) metadata.splice(2, 0, item.subject);
        metadata.forEach((text) => tags.append(element("span", "", text)));
        card.append(cardHead, preview, tags);
        card.addEventListener("click", () => loadMemoryDetail(item.id, card));
        list.append(card);
      });
    }
    const pagination = element("footer", "knowledge-pagination");
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    const summary = element("span", "", `第 ${state.page} / ${totalPages} 页`);
    const controls = element("div");
    const previous = element("button", "memory-action", "上一页");
    previous.type = "button";
    previous.disabled = state.page <= 1;
    previous.addEventListener("click", () => { state.page -= 1; loadMemories(); });
    const next = element("button", "memory-action", "下一页");
    next.type = "button";
    next.disabled = state.page >= totalPages;
    next.addEventListener("click", () => { state.page += 1; loadMemories(); });
    const pageSize = element("select", "knowledge-select page-size");
    [10, 20, 50].forEach((size) => pageSize.append(option(String(size), `${size} 条/页`)));
    pageSize.value = String(state.pageSize);
    pageSize.addEventListener("change", () => { state.pageSize = numberValue(pageSize.value, 20); state.page = 1; loadMemories(); });
    controls.append(previous, next, pageSize);
    pagination.append(summary, controls);
    content.append(head, listToolbar(), list, pagination);
    refreshIcons();
  }

  async function loadMemories() {
    const requestId = ++state.listRequestId;
    refs.content.replaceChildren(element("div", "dynamic-loading", "正在读取记忆…"));
    const scope = parseScopeSelection(state.filters.scopeToken);
    try {
      const payload = await api.getMemories({
        search: state.filters.search,
        type: state.filters.type,
        status: state.filters.status,
        scope_type: scope.type,
        scope_token: scope.token,
        agent_id: state.filters.agent,
        review: state.tab === "candidates" ? "true" : "",
        page: state.page,
        page_size: state.pageSize,
      });
      if (requestId !== state.listRequestId || !document.body.contains(state.workspace)) return;
      const normalized = collection(payload, ["memories"]);
      state.items = normalized.items;
      state.total = normalized.total;
      const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
      if (state.page > totalPages) {
        state.page = totalPages;
        return loadMemories();
      }
      renderMemoryList();
    } catch (error) {
      if (requestId !== state.listRequestId) return;
      refs.content.replaceChildren(element("div", "dynamic-loading error", error.message || "记忆列表加载失败"));
    }
  }

  function closeDrawer() {
    const backdrop = document.querySelector(".knowledge-drawer-backdrop[data-owner='memory']");
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
    backdrop.dataset.owner = "memory";
    const panel = element("aside", "knowledge-drawer");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", title);
    panel.tabIndex = -1;
    const head = element("header", "knowledge-drawer-head");
    const heading = element("div");
    heading.append(element("span", "knowledge-drawer-eyebrow", "内置记忆"), element("h3", "", title));
    const close = element("button", "icon-button");
    close.type = "button";
    close.setAttribute("aria-label", "关闭记忆抽屉");
    close.append(icon("x"));
    close.addEventListener("click", closeDrawer);
    head.append(heading, close);
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

  async function loadMemoryDetail(id, trigger) {
    if (!id) return;
    state.lastFocus = trigger || document.activeElement;
    const body = drawer(`记忆 #${id}`);
    body.append(element("div", "dynamic-loading", "正在读取完整记忆…"));
    const requestId = ++state.detailRequestId;
    try {
      const payload = await api.getMemoryDetail(id);
      if (requestId !== state.detailRequestId || !document.body.contains(body)) return;
      state.detail = normalizeMemory(payload);
      renderMemoryEditor(body, state.detail);
    } catch (error) {
      if (requestId !== state.detailRequestId) return;
      body.replaceChildren(element("div", "dynamic-loading error", error.message || "记忆详情加载失败"));
    }
  }

  function textArea(rows, maxLength) {
    const control = element("textarea", "knowledge-textarea");
    control.rows = rows;
    control.maxLength = maxLength;
    return control;
  }

  function input(type, maxLength) {
    const control = element("input", "knowledge-input");
    control.type = type;
    if (maxLength) control.maxLength = maxLength;
    return control;
  }

  function selectType(value) {
    const control = element("select", "knowledge-select wide");
    control.append(option("profile", "用户资料"), option("preference", "用户偏好"), option("entity", "人物与项目"), option("event", "重要事件"));
    control.value = value === "preferences" ? "preference" : value === "entities" ? "entity" : value === "events" ? "event" : TYPE_LABELS[value] ? value : "profile";
    return control;
  }

  function formScopeSelect(item) {
    const control = element("select", "knowledge-select wide");
    fillScopeSelect(control, "", true);
    const globalScope = scopeOptions().find((scope) => scope.type === "global");
    const selectedType = item.scope.type || "";
    const selectedToken = item.scope.token || (selectedType === "global" && globalScope ? globalScope.token : "");
    const desired = selectedToken ? `${encodeURIComponent(selectedType)}:${encodeURIComponent(selectedToken)}` : "";
    if (desired && ![...control.options].some((entry) => entry.value === desired)) {
      const custom = option(desired, item.scope.label || (item.scope.type === "global" ? "全局" : "当前受限作用域"));
      control.append(custom);
    }
    control.value = desired;
    control.required = true;
    return control;
  }

  function renderRecords(container, title, records, emptyText) {
    const section = element("section", "knowledge-detail-section");
    section.append(element("h4", "", title));
    const list = element("div", "knowledge-record-list");
    if (!records.length) {
      list.append(element("div", "dynamic-empty", emptyText));
    } else {
      records.forEach((record) => {
        const card = element("article", "knowledge-record");
        const source = record && typeof record === "object" ? record : { content: record };
        const head = element("header");
        head.append(
          element("strong", "", source.title || source.action || source.status || source.source_type || "记录"),
          element("time", "", formatTime(source.created_at || source.updated_at || source.occurred_at)),
        );
        const content = source.content ?? source.excerpt ?? source.text ?? source.reason ?? source.value ?? source.detail ?? source;
        card.append(head, element("pre", "knowledge-record-content", typeof content === "string" ? content : JSON.stringify(content, null, 2)));
        list.append(card);
      });
    }
    section.append(list);
    container.append(section);
  }

  function renderMemoryEditor(body, item) {
    body.replaceChildren();
    const form = element("form", "knowledge-form");
    const keyInput = input("text", 200);
    keyInput.value = item.memoryKey;
    keyInput.required = true;
    const typeSelect = selectType(item.type);
    const scopeSelect = formScopeSelect(item);
    const agentInput = input("text", 160);
    agentInput.value = item.agentId || "default";
    agentInput.required = true;
    const contentInput = textArea(7, 6000);
    contentInput.value = item.content;
    contentInput.required = true;
    const structuredInput = textArea(5, 6000);
    structuredInput.value = item.structuredValue === null || item.structuredValue === undefined
      ? ""
      : typeof item.structuredValue === "string" ? item.structuredValue : JSON.stringify(item.structuredValue, null, 2);
    const confidenceInput = input("number");
    confidenceInput.min = "0";
    confidenceInput.max = "1";
    confidenceInput.step = "0.01";
    confidenceInput.value = String(Math.max(0, Math.min(1, item.confidence)));
    const importanceInput = input("number");
    importanceInput.min = "0";
    importanceInput.max = "1";
    importanceInput.step = "0.01";
    importanceInput.value = String(Math.max(0, Math.min(1, item.importance)));
    const reasonInput = textArea(3, 1000);
    reasonInput.value = item.reason;
    form.append(
      field("记忆键", keyInput, "稳定标识，例如 preference.reply_style。"),
      field("类型", typeSelect),
      field("作用域", scopeSelect, "作用域由后端签发，页面不会展示原始 QQ 或群 ID。"),
      field("适用 Agent", agentInput, "填写具体 Agent ID；只有显式填写 * 才会跨 Agent 共享。"),
      field("记忆正文", contentInput),
      field("结构值（JSON，可选）", structuredInput),
      field("置信度", confidenceInput),
      field("重要度", importanceInput),
      field("审核说明", reasonInput),
    );

    const summary = element("dl", "memory-definition-list knowledge-editor-summary");
    [
      ["状态", (STATUS_LABELS[item.status] || [item.status])[0]],
      ["Agent", item.agentId || "default"],
      ["Revision", item.revision],
      ["证据", item.evidenceCount],
      ["召回次数", item.recallCount],
      ["创建时间", formatTime(item.createdAt)],
      ["更新时间", formatTime(item.updatedAt)],
    ].forEach(([label, value]) => {
      const row = element("div");
      row.append(element("dt", "", label), element("dd", "", value));
      summary.append(row);
    });

    const actions = element("footer", "knowledge-drawer-actions");
    const save = element("button", "secondary-button");
    save.type = "submit";
    save.append(icon("save"), element("span", "", item.id ? "保存修改" : "创建候选"));
    actions.append(save);
    if (item.id && ["candidate", "pending", "conflict"].includes(item.status)) {
      const approve = element("button", "primary-button");
      approve.type = "button";
      approve.append(icon("badge-check"), element("span", "", "批准启用"));
      approve.addEventListener("click", () => submitMemoryAction("approve", item, form, { keyInput, typeSelect, scopeSelect, agentInput, contentInput, structuredInput, confidenceInput, importanceInput, reasonInput }));
      const reject = element("button", "text-button");
      reject.type = "button";
      reject.append(icon("ban"), element("span", "", "拒绝"));
      reject.addEventListener("click", () => submitMemoryAction("reject", item, form, { reasonInput }));
      actions.append(approve, reject);
    }
    if (item.id) {
      if (["disabled", "rejected", "superseded", "tombstone", "tombstoned", "dead"].includes(item.status)) {
        const restore = element("button", "text-button");
        restore.type = "button";
        restore.append(icon("rotate-ccw"), element("span", "", "恢复为候选"));
        restore.addEventListener("click", () => submitMemoryAction("restore", item, form, { reasonInput }));
        actions.append(restore);
      }
      const remove = element("button", "danger-button");
      remove.type = "button";
      remove.append(icon("trash-2"), element("span", "", "删除"));
      remove.addEventListener("click", () => {
        if (global.confirm("确定删除这条记忆吗？删除操作会保留必要审计记录。")) submitMemoryAction("delete", item, form, { reasonInput });
      });
      actions.append(remove);
    }
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitMemoryAction(item.id ? "update" : "create", item, form, { keyInput, typeSelect, scopeSelect, agentInput, contentInput, structuredInput, confidenceInput, importanceInput, reasonInput });
    });
    form.append(actions);
    body.append(summary, form);
    renderRecords(body, "证据链", item.evidence, "暂无证据；没有证据的自动候选不得直接启用。" );
    renderRecords(body, "冲突", item.conflicts, "暂无冲突");
    renderRecords(body, "Revision 历史", item.revisions, "暂无历史版本");
    renderRecords(body, "召回记录", item.recallLogs, "暂无召回记录");
    renderRecords(body, "审计", item.audit, "暂无审计记录");
    refreshIcons();
  }

  function openMemoryDrawer(memory) {
    if (!memory && !scopeOptions().length) {
      state.notify("作用域尚未加载，请稍后再试", "error");
      return;
    }
    const item = memory ? normalizeMemory(memory) : normalizeMemory({
      type: "profile",
      status: "candidate",
      agent_id: "default",
      confidence: 1,
      importance: 0.5,
    });
    if (!memory) {
      const restrictedScope = scopeOptions().find((scope) => scope.type !== "global");
      item.scope = restrictedScope
        ? { ...restrictedScope }
        : { type: "", token: "", label: "" };
    }
    state.detail = item;
    const body = drawer(item.id ? `记忆 #${item.id}` : "新增记忆");
    renderMemoryEditor(body, item);
  }

  async function submitMemoryAction(action, item, form, controls) {
    if (state.pending) return;
    const writesEditor = ["create", "update", "approve"].includes(action);
    if (writesEditor && form && typeof form.reportValidity === "function" && !form.reportValidity()) return;
    let structuredValue = null;
    if (writesEditor && controls.structuredInput && controls.structuredInput.value.trim()) {
      try {
        structuredValue = JSON.parse(controls.structuredInput.value);
      } catch {
        state.notify("结构值必须是合法 JSON", "error");
        controls.structuredInput.focus();
        return;
      }
    }
    const payload = {
      action,
      id: item.id,
      revision: item.revision,
      reason: controls.reasonInput ? controls.reasonInput.value.trim() : item.reason,
    };
    if (writesEditor) {
      const scope = parseScopeSelection(controls.scopeSelect.value);
      const introducesGlobalScope = scope.type === "global" && (action === "create" || item.scope.type !== "global");
      if (introducesGlobalScope && !global.confirm("全局记忆会对这个 Agent 的所有聊天生效，确定继续吗？")) return;
      Object.assign(payload, {
        memory_key: controls.keyInput.value.trim(),
        type: controls.typeSelect.value,
        status: item.status,
        content: controls.contentInput.value.trim(),
        structured_value: structuredValue,
        scope_type: scope.type,
        scope_token: scope.token,
        agent_id: controls.agentInput.value.trim(),
        confidence: numberValue(controls.confidenceInput.value, item.confidence),
        importance: numberValue(controls.importanceInput.value, item.importance),
      });
    }
    state.pending = true;
    form.querySelectorAll("button, input, textarea, select").forEach((control) => { control.disabled = true; });
    try {
      const response = await api.memoryAction(payload);
      state.notify(action === "delete" ? "记忆已删除" : action === "approve" ? "记忆已批准" : action === "reject" ? "记忆已拒绝" : "记忆已保存", "success");
      if (action === "delete") closeDrawer();
      else {
        const detail = response && (response.detail || response.item || response.memory) ? response : await api.getMemoryDetail(response && response.id ? response.id : item.id);
        const normalized = normalizeMemory(detail);
        if (normalized.id) {
          state.detail = normalized;
          renderMemoryEditor(form.parentElement, normalized);
        } else closeDrawer();
      }
      await Promise.all([loadOverview(state.viewEpoch), loadMemories()]);
    } catch (error) {
      state.notify(error.message || "记忆操作失败", "error");
      form.querySelectorAll("button, input, textarea, select").forEach((control) => { control.disabled = false; });
    } finally {
      state.pending = false;
    }
  }

  function recallCandidate(raw) {
    const item = normalizeMemory(raw.item || raw.memory || raw);
    const card = element("article", "knowledge-recall-result");
    const head = element("header");
    head.append(element("strong", "", item.memoryKey || item.content.slice(0, 60) || `记忆 #${item.id}`), statusBadge(item.status));
    card.append(head, element("p", "", item.content || item.preview || "（无正文）"));
    const scores = raw.scores && typeof raw.scores === "object" ? raw.scores : raw.score_components && typeof raw.score_components === "object" ? raw.score_components : {};
    const meta = element("div", "knowledge-list-meta");
    meta.append(element("span", "", `总分 ${numberValue(raw.score ?? raw.final_score, 0).toFixed(3)}`));
    Object.entries(scores).forEach(([key, value]) => meta.append(element("span", "", `${key} ${numberValue(value, 0).toFixed(3)}`)));
    if (raw.filter_reason || raw.reason) meta.append(element("span", "", `原因：${raw.filter_reason || raw.reason}`));
    card.append(meta);
    return card;
  }

  function renderRecallPanel() {
    refs.content.replaceChildren();
    const head = element("header", "memory-panel-head");
    const copy = element("div");
    copy.append(element("h3", "", "召回调试"), element("p", "", "输入一句测试消息，只运行记忆检索并展示实际命中与可用评分，不调用 Chat Provider。"));
    head.append(copy, element("span", "runtime-state-badge ready", "只读测试"));
    const form = element("form", "knowledge-form recall-debug-form");
    const query = textArea(4, 4000);
    query.placeholder = "例如：我之前说过喜欢什么回复风格？";
    query.required = true;
    const scope = element("select", "knowledge-select wide");
    refs.recallScope = scope;
    fillScopeSelect(scope, "", true);
    const initialScope = scopeOptions().find((item) => item.type !== "global") || scopeOptions().find((item) => item.type === "global");
    if (initialScope) scope.value = `${encodeURIComponent(initialScope.type)}:${encodeURIComponent(initialScope.token)}`;
    scope.required = true;
    const agent = element("select", "knowledge-select wide");
    refs.memoryRecallPersona = agent;
    fillPersonaSelect(agent, "");
    agent.addEventListener("change", () => { agent.dataset.touched = "true"; });
    agent.required = true;
    const type = selectType("profile");
    type.prepend(option("", "全部类型"));
    type.value = "";
    const limit = input("number");
    limit.min = "1";
    limit.max = "20";
    limit.value = "5";
    const submit = element("button", "primary-button");
    submit.type = "submit";
    submit.append(icon("search-check"), element("span", "", "测试召回"));
    form.append(field("测试消息", query), field("作用域", scope), field("人格上下文", agent, "选择 AstrBot 当前会话最终生效的人格；选项来自人格配置和插件历史数据，不是平台名。"), field("记忆类型", type), field("最多结果", limit), submit);
    const results = element("div", "knowledge-recall-results");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (state.pending || !form.reportValidity()) return;
      const selectedScope = parseScopeSelection(scope.value);
      state.pending = true;
      submit.disabled = true;
      results.replaceChildren(element("div", "dynamic-loading", "正在执行本地检索…"));
      try {
        const payload = await api.debugMemoryRecall({ query: query.value.trim(), scope_type: selectedScope.type, scope_token: selectedScope.token, agent_id: agent.value.trim(), type: type.value, limit: numberValue(limit.value, 5) });
        const normalized = collection(payload, ["candidates", "results", "memories"]);
        results.replaceChildren();
        const summary = element("p", "knowledge-debug-summary", `返回 ${normalized.items.length} 条实际命中；结果受作用域、类型、阈值和数量上限约束。`);
        results.append(summary);
        if (!normalized.items.length) results.append(element("div", "dynamic-empty", "没有符合当前作用域和阈值的记忆"));
        else normalized.items.forEach((item) => results.append(recallCandidate(item)));
        const filtered = payload && Array.isArray(payload.filtered) ? payload.filtered : [];
        if (filtered.length) {
          const filteredTitle = element("h4", "", "已过滤候选");
          results.append(filteredTitle);
          filtered.forEach((item) => results.append(recallCandidate(item)));
        }
      } catch (error) {
        results.replaceChildren(element("div", "runtime-status-notice error", error.message || "召回测试失败"));
      } finally {
        state.pending = false;
        submit.disabled = false;
        refreshIcons();
      }
    });
    refs.content.append(head, form, results);
    refreshIcons();
  }

  function renderJobsPanel() {
    refs.content.replaceChildren();
    const head = element("header", "memory-panel-head");
    const copy = element("div");
    copy.append(element("h3", "", "后台任务"), element("p", "", "查看提取、索引和清理任务。失败任务不会阻断正常聊天。"));
    head.append(copy, element("span", "runtime-panel-badge", `共 ${state.jobsTotal} 条`));
    const toolbar = element("div", "knowledge-toolbar knowledge-job-toolbar");
    const status = element("select", "knowledge-select");
    status.append(option("", "全部状态"), option("pending", "待处理"), option("running", "运行中"), option("retry", "待重试"), option("completed", "已完成"), option("dead", "已终止"));
    status.value = state.jobsStatus;
    status.addEventListener("change", () => { state.jobsStatus = status.value; state.jobsPage = 1; loadJobs(); });
    const type = element("input", "knowledge-search compact");
    type.type = "search";
    type.placeholder = "任务类型";
    type.value = state.jobsType;
    type.addEventListener("change", () => { state.jobsType = type.value.trim(); state.jobsPage = 1; loadJobs(); });
    const agent = element("input", "knowledge-search compact");
    agent.type = "search";
    agent.placeholder = "Agent";
    agent.value = state.jobsAgent;
    agent.addEventListener("change", () => { state.jobsAgent = agent.value.trim(); state.jobsPage = 1; loadJobs(); });
    const reload = element("button", "memory-action");
    reload.type = "button";
    reload.append(icon("refresh-cw"), element("span", "", "刷新任务"));
    reload.addEventListener("click", loadJobs);
    toolbar.append(status, type, agent, reload);
    const list = element("div", "knowledge-list");
    if (!state.jobs.length) list.append(element("div", "dynamic-empty", "暂无任务记录"));
    else state.jobs.forEach((job) => {
      const card = element("article", "knowledge-job-card");
      const cardHead = element("header");
      const identity = element("div");
      identity.append(element("strong", "", job.type || job.job_type || "memory job"), element("code", "", String(job.id || job.job_id || "--")));
      cardHead.append(identity, statusBadge(job.status));
      const meta = element("dl", "knowledge-job-meta");
      [
        ["Attempts", job.attempts ?? 0],
        ["Next", formatTime(job.next_run_at || job.next_attempt_at)],
        ["Updated", formatTime(job.updated_at)],
        ["Scope", job.scope_label || "受限作用域"],
        ["Agent", job.agent_id || "default"],
      ].forEach(([label, value]) => { const row = element("div"); row.append(element("dt", "", label), element("dd", "", value)); meta.append(row); });
      card.append(cardHead, meta);
      if (job.error || job.last_error) card.append(element("div", "memory-record-error", job.error || job.last_error));
      list.append(card);
    });
    const pagination = element("footer", "knowledge-pagination");
    const totalPages = Math.max(1, Math.ceil(state.jobsTotal / state.jobsPageSize));
    const controls = element("div");
    const previous = element("button", "memory-action", "上一页");
    previous.type = "button";
    previous.disabled = state.jobsPage <= 1;
    previous.addEventListener("click", () => { state.jobsPage -= 1; loadJobs(); });
    const next = element("button", "memory-action", "下一页");
    next.type = "button";
    next.disabled = state.jobsPage >= totalPages;
    next.addEventListener("click", () => { state.jobsPage += 1; loadJobs(); });
    const pageSize = element("select", "knowledge-select page-size");
    [10, 20, 50, 100].forEach((size) => pageSize.append(option(String(size), `${size} 条/页`)));
    pageSize.value = String(state.jobsPageSize);
    pageSize.addEventListener("change", () => { state.jobsPageSize = numberValue(pageSize.value, 20); state.jobsPage = 1; loadJobs(); });
    controls.append(previous, next, pageSize);
    pagination.append(element("span", "", `第 ${state.jobsPage} / ${totalPages} 页`), controls);
    refs.content.append(head, toolbar, list, pagination);
    refreshIcons();
  }

  async function loadJobs() {
    const requestId = ++state.jobsRequestId;
    refs.content.replaceChildren(element("div", "dynamic-loading", "正在读取后台任务…"));
    try {
      const payload = await api.getMemoryJobs({ status: state.jobsStatus, job_type: state.jobsType, agent_id: state.jobsAgent, page: state.jobsPage, page_size: state.jobsPageSize });
      if (requestId !== state.jobsRequestId || !document.body.contains(state.workspace)) return;
      const normalized = collection(payload, ["jobs"]);
      state.jobs = normalized.items;
      state.jobsTotal = normalized.total;
      const totalPages = Math.max(1, Math.ceil(state.jobsTotal / state.jobsPageSize));
      if (state.jobsPage > totalPages) {
        state.jobsPage = totalPages;
        return loadJobs();
      }
      renderJobsPanel();
    } catch (error) {
      if (requestId !== state.jobsRequestId) return;
      refs.content.replaceChildren(element("div", "dynamic-loading error", error.message || "后台任务加载失败"));
    }
  }

  function switchTab(tab) {
    state.tab = tab;
    state.page = 1;
    if (tab === "jobs") state.jobsPage = 1;
    if (tab === "candidates") state.filters.status = "candidate";
    else if (tab === "memories" && state.filters.status === "candidate") state.filters.status = "";
    renderTabs();
    if (tab === "recall") renderRecallPanel();
    else if (tab === "jobs") loadJobs();
    else loadMemories();
  }

  function refreshCurrentView() {
    loadOverview(state.viewEpoch);
    loadPersonaOptions(state.viewEpoch);
    if (state.tab === "recall") renderRecallPanel();
    else if (state.tab === "jobs") loadJobs();
    else loadMemories();
  }

  function open() {
    if (!state.root) return false;
    state.viewEpoch += 1;
    const epoch = state.viewEpoch;
    closeDrawer();
    buildShell();
    loadOverview(epoch);
    loadPersonaOptions(epoch);
    if (state.tab === "recall") renderRecallPanel();
    else if (state.tab === "jobs") loadJobs();
    else loadMemories();
    return true;
  }

  function close() {
    state.viewEpoch += 1;
    state.listRequestId += 1;
    state.detailRequestId += 1;
    state.jobsRequestId += 1;
    global.clearTimeout(state.searchTimer);
    closeDrawer();
  }

  global.HumanizeMemory = Object.freeze({
    mount(target, options) {
      state.root = target;
      if (options && typeof options.notify === "function") state.notify = options.notify;
    },
    open,
    close,
  });
})(window, document);
