(function registerProtocolView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** View state (reset on each mount). */
  let state = null;
  let ctx = null;
  let statsGuard = null;
  let listGuard = null;
  let detailGuard = null;
  let statsRoot = null;
  let listRoot = null;
  let detailRoot = null;
  let paginationRoot = null;

  /** Create a fresh state object. */
  function freshState() {
    return {
      page: 1, pageSize: 20,
      total: 0,
      stats: null,
      selectedId: null,
    };
  }

  /**
   * Fetch overview statistics (jargon + protocol + statistics).
   * Rendered as a metric grid above the log table.
   */
  async function loadStats() {
    if (!statsRoot || !ctx || ctx.isStale()) return;
    const reqId = statsGuard.bump();
    try {
      const data = await Api.getOverview();
      if (statsGuard.isStale(reqId) || ctx.isStale()) return;
      state.stats = data || null;
      renderStats(state.stats);
    } catch (err) {
      if (statsGuard.isStale(reqId) || ctx.isStale()) return;
      statsRoot.replaceChildren();
      statsRoot.append(Ui.createAlert({
        variant: "warning",
        title: "统计加载失败",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Render the statistics metric grid.
   * @param {object} stats Overview payload.
   */
  function renderStats(stats) {
    statsRoot.replaceChildren();
    statsRoot.style.display = "flex";
    statsRoot.style.flexDirection = "column";
    statsRoot.style.gap = "var(--sp-3)";

    const protocol = (stats && stats.protocol) || {};
    const statistics = (stats && stats.statistics) || {};
    const range = statistics.start_date && statistics.end_date
      ? `${statistics.start_date} ~ ${statistics.end_date}`
      : "最近 7 天";

    const grid = document.createElement("div");
    grid.className = "metric-grid";
    grid.style.display = "grid";
    grid.style.gap = "var(--sp-3)";
    grid.style.gridTemplateColumns = "repeat(auto-fit, minmax(140px, 1fr))";

    grid.append(Ui.createMetric({
      label: "协议请求", value: Core.numberValue(protocol.total),
      hint: range, icon: "activity",
    }));
    grid.append(Ui.createMetric({
      label: "通过", value: Core.numberValue(protocol.success),
      hint: "最终放行", icon: "check",
    }));
    grid.append(Ui.createMetric({
      label: "拦截", value: Core.numberValue(protocol.blocked),
      hint: "被协议阻止", icon: "ban",
    }));
    grid.append(Ui.createMetric({
      label: "成功率", value: Core.formatRate(statistics.success_rate),
      hint: "7 天合计", icon: "chart-no-axes-combined",
    }));

    statsRoot.append(grid);
    Core.refreshIcons();
  }

  /**
   * Fetch and render the protocol logs list for the current page.
   */
  async function loadList() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    const reqId = listGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载协议日志…"));
    try {
      const data = await Api.getProtocolLogs({
        page: state.page, page_size: state.pageSize,
      });
      if (listGuard.isStale(reqId) || ctx.isStale()) return;
      renderList(data);
    } catch (err) {
      if (listGuard.isStale(reqId) || ctx.isStale()) return;
      listRoot.replaceChildren();
      listRoot.append(Ui.createAlert({
        variant: "danger",
        title: "加载失败",
        message: (err && err.message) || String(err),
      }));
      paginationRoot.replaceChildren();
    }
  }

  /**
   * Render the log table from API response.
   * @param {object} data {items, total}
   */
  function renderList(data) {
    const items = (data && data.items) || [];
    state.total = Core.numberValue(data && data.total);
    listRoot.replaceChildren();

    if (!items.length) {
      listRoot.append(Ui.createEmptyState({
        title: "暂无协议日志",
        message: "近期没有回复协议事件。",
      }));
      renderPagination();
      return;
    }

    const columns = [
      { key: "created_at", label: "时间", width: "140px", mono: true },
      { key: "action", label: "动作", width: "90px" },
      { key: "stage", label: "阶段", width: "80px" },
      { key: "success", label: "结果", width: "70px" },
      { key: "model", label: "模型", width: "120px" },
      { key: "duration_ms", label: "耗时", width: "70px", num: true },
      { key: "scope_id", label: "作用域", width: "100px", mono: true },
      { key: "failure_code", label: "失败码", width: "100px" },
    ];

    const rows = items.map((item) => ({
      key: item.id,
      cells: {
        created_at: Core.formatTime(item.created_at),
        action: item.action || "—",
        stage: { text: item.stage || "—", variant: item.stage === "final" ? "info" : "" },
        success: { text: item.success ? "通过" : "拦截", variant: item.success ? "success" : "danger" },
        model: item.model || "—",
        duration_ms: item.duration_ms != null ? `${item.duration_ms} ms` : "—",
        scope_id: item.scope_id || "—",
        failure_code: item.failure_code || "—",
      },
    }));

    listRoot.append(Ui.createTable({
      columns, rows,
      selectedKey: state.selectedId,
      onRowClick: (row) => selectLog(row.key, items),
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
   * Select a log row and render its detail.
   * @param {number} id Log id.
   * @param {Array} items Current page items (for instant render).
   */
  function selectLog(id, items) {
    state.selectedId = id;
    const item = items.find((it) => it.id === id);
    if (item) {
      renderDetail(item);
    } else {
      detailRoot.replaceChildren();
      detailRoot.append(Ui.createEmptyState({
        title: "选择一条日志",
        message: "点击左侧日志查看详情。",
      }));
    }
  }

  /**
   * Render the protocol log detail panel.
   * @param {object} item Log item.
   */
  function renderDetail(item) {
    detailRoot.replaceChildren();
    detailRoot.style.gap = "var(--sp-4)";
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";

    const actions = [];
    if (item.request_id) {
      actions.push(() => Ui.createButton({
        label: "复制请求 ID", variant: "outline", size: "sm", icon: "copy",
        onClick: () => Core.copyText(item.request_id).then(() => ctx.toastSuccess("已复制请求 ID")),
      }));
    }

    const headerPanel = Ui.createPanel({
      title: `日志 #${item.id}`,
      subtitle: Core.formatTime(item.created_at),
      icon: "scroll-text",
      actions,
      body: () => Ui.createDefinitionList([
        { dt: "结果", node: Ui.createBadge(item.success ? "通过" : "拦截", item.success ? "success" : "danger") },
        { dt: "动作", dd: item.action || "—" },
        { dt: "阶段", dd: item.stage || "—" },
        { dt: "最终记录", dd: item.is_final ? "是" : "否" },
        { dt: "模型", dd: item.model || "—" },
        { dt: "耗时", dd: item.duration_ms != null ? `${item.duration_ms} ms` : "—" },
        { dt: "作用域", dd: item.scope_id || "—" },
        { dt: "消息 ID", dd: item.message_id || "—" },
        { dt: "请求 ID", dd: item.request_id || "—" },
        { dt: "失败码", dd: item.failure_code || "—" },
      ], { vertical: true }),
    });
    detailRoot.append(headerPanel);

    if (item.failure_detail) {
      const detailPanel = Ui.createPanel({
        title: "失败详情", icon: "circle-alert",
        body: () => {
          const pre = document.createElement("pre");
          pre.className = "trace-content";
          pre.style.whiteSpace = "pre-wrap";
          pre.style.wordBreak = "break-word";
          pre.style.margin = "0";
          pre.textContent = String(item.failure_detail);
          return pre;
        },
      });
      detailRoot.append(detailPanel);
    }

    Core.refreshIcons();
  }

  /**
   * Mount the protocol view: install topbar actions and load stats + list.
   * @param {HTMLElement} root
   * @param {object} viewCtx
   */
  async function mount(root, viewCtx) {
    ctx = viewCtx;
    state = freshState();
    statsGuard = Core.requestIdGuard();
    listGuard = Core.requestIdGuard();
    detailGuard = Core.requestIdGuard();
    root.replaceChildren();

    const shell = document.createElement("div");
    shell.className = "protocol-view";
    shell.style.display = "flex";
    shell.style.flexDirection = "column";
    shell.style.gap = "var(--sp-4)";

    statsRoot = document.createElement("div");
    statsRoot.className = "protocol-stats";
    shell.append(statsRoot);

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
      title: "选择一条日志",
      message: "点击左侧日志查看详情。",
    }));
    right.append(detailRoot);

    split.append(left, right);
    shell.append(split);

    root.append(shell);

    ctx.setTopbarActions([
      Ui.createButton({
        label: "刷新", variant: "outline", size: "sm", icon: "refresh-cw",
        onClick: () => { loadStats(); loadList(); },
      }),
    ]);

    await Promise.all([loadStats(), loadList()]);
  }

  /** Reset view-scoped state on unmount. */
  function unmount() {
    state = null;
    ctx = null;
    statsGuard = null;
    listGuard = null;
    detailGuard = null;
    statsRoot = null;
    listRoot = null;
    detailRoot = null;
    paginationRoot = null;
  }

  global.HumanizeViews.protocol = { mount, unmount };
})(window);
