(function registerContextView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** 运行状态 → 徽章映射。 */
  const RUN_STATUS = {
    success: { label: "成功", variant: "success" },
    ok: { label: "成功", variant: "success" },
    failed: { label: "失败", variant: "danger" },
    error: { label: "错误", variant: "danger" },
    pending: { label: "进行中", variant: "warning" },
    running: { label: "进行中", variant: "warning" },
  };

  /**
   * Build a status badge for a context run status.
   * @param {string} status
   * @returns {HTMLElement}
   */
  function statusBadge(status) {
    const info = RUN_STATUS[status] || { label: status || "--", variant: "" };
    return Ui.createBadge(info.label, info.variant);
  }

  /** View state (reset on each mount). */
  let state = null;
  let ctx = null;
  let listGuard = null;
  let detailGuard = null;
  let listRoot = null;
  let detailRoot = null;
  let paginationRoot = null;

  /** Create a fresh state object. */
  function freshState() {
    return {
      page: 1, pageSize: 20,
      scopeType: "", scopeId: "", sectionKey: "",
      selectedId: null, total: 0,
    };
  }

  /**
   * Fetch and render the context runs list.
   */
  async function loadList() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    const reqId = listGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载运行列表…"));
    try {
      const data = await Api.getContextRuns({
        page: state.page, page_size: state.pageSize,
        scope_type: state.scopeType, scope_id: state.scopeId,
        section_key: state.sectionKey,
      });
      if (listGuard.isStale(reqId) || ctx.isStale()) return;
      renderList(data);
    } catch (err) {
      if (listGuard.isStale(reqId) || ctx.isStale()) return;
      listRoot.replaceChildren();
      listRoot.append(Ui.createAlert({
        variant: "danger", title: "加载失败",
        message: (err && err.message) || String(err),
      }));
      paginationRoot.replaceChildren();
    }
  }

  /**
   * Render the runs table from API response.
   * @param {*} data
   */
  function renderList(data) {
    const norm = Core.normalizeCollection(data);
    state.total = norm.total;
    listRoot.replaceChildren();

    if (!norm.items.length) {
      listRoot.append(Ui.createEmptyState({
        title: "暂无运行记录",
        message: "尚未记录任何上下文构建运行。",
      }));
      renderPagination();
      return;
    }

    const columns = [
      { key: "request_id", label: "请求 ID", width: "120px", mono: true },
      { key: "scope", label: "作用域", width: "120px" },
      { key: "status", label: "状态", width: "80px" },
      { key: "model", label: "模型", width: "120px" },
      { key: "tokens", label: "Token", width: "80px", num: true },
      { key: "latency_ms", label: "耗时", width: "80px", num: true },
      { key: "created_at", label: "时间", width: "130px", mono: true },
    ];

    const rows = norm.items.map((item) => ({
      key: item.request_id,
      cells: {
        request_id: Core.truncateId(item.request_id, 10, 4),
        scope: { text: Core.formatScopeLabel(item.scope_type, item.scope_id) },
        status: { text: item.status, variant: (RUN_STATUS[item.status] || {}).variant },
        model: item.model || "--",
        tokens: String(item.total_tokens ?? item.token_count ?? 0),
        latency_ms: item.latency_ms ? `${item.latency_ms}ms` : "--",
        created_at: Core.formatTime(item.created_at),
      },
    }));

    listRoot.append(Ui.createTable({
      columns, rows,
      selectedKey: state.selectedId,
      onRowClick: (row) => selectRun(row.key),
    }));
    renderPagination();
  }

  /** Render pagination controls. */
  function renderPagination() {
    if (!paginationRoot) return;
    paginationRoot.replaceChildren();
    paginationRoot.append(Ui.createPagination({
      page: state.page, pageSize: state.pageSize, total: state.total,
      onChange: (page) => { state.page = page; loadList(); },
    }));
  }

  /**
   * Select a run and load its detail.
   * @param {string} requestId
   */
  async function selectRun(requestId) {
    state.selectedId = requestId;
    await loadDetail(requestId);
  }

  /**
   * Fetch and render a single context run detail.
   * @param {string} requestId
   */
  async function loadDetail(requestId) {
    if (!detailRoot || !ctx || ctx.isStale()) return;
    const reqId = detailGuard.bump();
    detailRoot.replaceChildren();
    detailRoot.append(Ui.createLoading("加载运行详情…"));
    try {
      const data = await Api.getContextRun(requestId);
      if (detailGuard.isStale(reqId) || ctx.isStale()) return;
      if (!data) {
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "运行不存在", message: "该运行记录可能已被清理。",
        }));
        return;
      }
      renderDetail(data);
    } catch (err) {
      if (detailGuard.isStale(reqId) || ctx.isStale()) return;
      detailRoot.replaceChildren();
      detailRoot.append(Ui.createAlert({
        variant: "danger", title: "加载详情失败",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Render the full run detail: summary + model request + inserted sections + response.
   * @param {object} data
   */
  function renderDetail(data) {
    detailRoot.replaceChildren();
    detailRoot.style.gap = "var(--sp-4)";
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";

    if (data.error) {
      detailRoot.append(Ui.createAlert({
        variant: "danger", title: "运行错误",
        message: String(data.error),
      }));
    }

    detailRoot.append(renderSummaryPanel(data));

    if (data.model_request || data.request) {
      detailRoot.append(renderTracePanel(
        "模型请求快照", "scan-search", data.model_request || data.request,
      ));
    }

    const sections = data.inserted_sections || data.sections || [];
    if (Array.isArray(sections) && sections.length) {
      sections.forEach((section, idx) => {
        const label = `插入段 ${idx + 1}${section.section_key ? ` · ${section.section_key}` : ""}`;
        detailRoot.append(renderTracePanel(label, "file-text", section.content || section, {
          format: section.format,
        }));
      });
    }

    if (data.response || data.model_response) {
      detailRoot.append(renderTracePanel(
        "响应快照", "messages-square", data.response || data.model_response,
      ));
    }

    Core.refreshIcons();
  }

  /**
   * Render the summary panel with key metrics.
   * @param {object} data
   * @returns {HTMLElement}
   */
  function renderSummaryPanel(data) {
    const summary = data.summary || data;
    return Ui.createPanel({
      title: "运行摘要",
      subtitle: `请求 ID ${Core.truncateId(data.request_id || "", 12, 4)}`,
      icon: "list-todo",
      body: () => {
        const items = [
          { dt: "状态", node: statusBadge(summary.status) },
          { dt: "作用域", dd: Core.formatScopeLabel(data.scope_type, data.scope_id) },
          { dt: "模型", dd: summary.model || "--" },
          { dt: "Prompt Tokens", dd: String(summary.prompt_tokens ?? 0) },
          { dt: "Completion Tokens", dd: String(summary.completion_tokens ?? 0) },
          { dt: "总 Tokens", dd: String(summary.total_tokens ?? 0) },
          { dt: "耗时", dd: summary.latency_ms ? `${summary.latency_ms}ms` : "--" },
          { dt: "创建时间", dd: Core.formatTime(data.created_at || summary.created_at) },
        ];
        if (summary.section_key) {
          items.splice(2, 0, { dt: "Section Key", dd: summary.section_key, mono: true });
        }
        return Ui.createDefinitionList(items, { vertical: true });
      },
    });
  }

  /**
   * Render a trace viewer wrapped in a panel.
   * @param {string} title
   * @param {string} icon
   * @param {*} content
   * @param {object} [opts] { format }
   * @returns {HTMLElement}
   */
  function renderTracePanel(title, icon, content, opts) {
    const o = opts || {};
    return Ui.createPanel({
      title, icon,
      body: () => Ui.createTraceViewer({ content, format: o.format }),
    });
  }

  /**
   * Render the toolbar with scope filters.
   * @returns {HTMLElement}
   */
  function renderToolbar() {
    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";

    toolbar.append(Ui.createSelect({
      value: state.scopeType,
      options: [
        { value: "", label: "全部作用域" },
        { value: "global", label: "全局" },
        { value: "group", label: "群组" },
        { value: "private", label: "私聊" },
        { value: "channel", label: "频道" },
        { value: "chat", label: "会话" },
      ],
      sm: true,
      onChange: (val) => { state.scopeType = val; state.page = 1; loadList(); },
    }));

    toolbar.append(Ui.createInput({
      placeholder: "Section Key 过滤…",
      value: state.sectionKey,
      sm: true,
      onChange: (val) => {
        state.sectionKey = val.trim();
        state.page = 1;
        loadList();
      },
    }));

    const spacer = document.createElement("div");
    spacer.className = "toolbar-spacer";
    toolbar.append(spacer);

    toolbar.append(Ui.createButton({
      label: "刷新", variant: "ghost", size: "sm", icon: "refresh-cw",
      onClick: () => loadList(),
    }));

    return toolbar;
  }

  global.HumanizeViews.context = {
    async mount(root, context) {
      ctx = context;
      state = freshState();
      listGuard = Core.requestIdGuard();
      detailGuard = Core.requestIdGuard();

      root.replaceChildren();
      root.style.gap = "var(--sp-4)";
      root.style.display = "flex";
      root.style.flexDirection = "column";

      const header = document.createElement("div");
      header.className = "page-header";
      const headerText = document.createElement("div");
      headerText.className = "page-header-text";
      const title = document.createElement("h1");
      title.className = "page-title";
      title.textContent = "上下文追踪";
      const sub = document.createElement("p");
      sub.className = "page-subtitle";
      sub.textContent = "请求级上下文构建与插入段追踪";
      headerText.append(title, sub);
      header.append(headerText);
      root.append(header);

      root.append(renderToolbar());

      const split = document.createElement("div");
      split.className = "split-view";
      const left = document.createElement("div");
      left.className = "split-view-left";
      left.style.display = "flex";
      left.style.flexDirection = "column";
      left.style.gap = "var(--sp-2)";
      listRoot = document.createElement("div");
      paginationRoot = document.createElement("div");
      left.append(listRoot, paginationRoot);

      const right = document.createElement("div");
      right.className = "split-view-right";
      detailRoot = document.createElement("div");
      detailRoot.style.display = "flex";
      detailRoot.style.flexDirection = "column";
      detailRoot.style.gap = "var(--sp-4)";
      detailRoot.append(Ui.createEmptyState({
        title: "未选中运行",
        message: "从左侧列表选择一条运行记录查看详情。",
      }));
      right.append(detailRoot);

      split.append(left, right);
      root.append(split);

      await loadList();
    },
    unmount() {
      ctx = null; state = null;
      listRoot = null; detailRoot = null; paginationRoot = null;
      listGuard = null; detailGuard = null;
    },
  };
})(window);