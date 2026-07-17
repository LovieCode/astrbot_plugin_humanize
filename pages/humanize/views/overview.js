(function registerOverviewView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** View state (reset on each mount). */
  let ctx = null;
  let overviewGuard = null;
  let memoryGuard = null;
  let logsGuard = null;
  let providerGuard = null;
  let contextGuard = null;
  let metricsRoot = null;
  let panelsRoot = null;
  let providerRoot = null;
  let contextRoot = null;
  let logsRoot = null;

  /** Memory service state → display label mapping. */
  const MEMORY_STATE_LABELS = {
    not_initialized: { label: "未初始化", variant: "" },
    ready: { label: "就绪", variant: "success" },
    extracting: { label: "提取中", variant: "info" },
    disabled: { label: "已停用", variant: "warning" },
    error: { label: "异常", variant: "danger" },
  };

  /**
   * Build a clickable "jump to view" link button.
   * @param {string} viewKey
   * @param {string} label
   * @returns {HTMLElement}
   */
  function jumpButton(viewKey, label) {
    return Ui.createButton({
      label, variant: "ghost", size: "sm", icon: "arrow-right",
      onClick: () => ctx && ctx.navigate(viewKey),
    });
  }

  /**
   * Fetch the overview payload and render the metric grid + summary panels.
   */
  async function loadOverview() {
    if (!metricsRoot || !ctx || ctx.isStale()) return;
    const reqId = overviewGuard.bump();
    try {
      const [data, contextStats] = await Promise.all([
        Api.getOverview(),
        Api.getContextStats({ days: 7 }).catch(() => ({})),
      ]);
      if (overviewGuard.isStale(reqId) || ctx.isStale()) return;
      renderMetrics(data || {}, contextStats || {});
      renderPanels(data || {});
    } catch (err) {
      if (overviewGuard.isStale(reqId) || ctx.isStale()) return;
      metricsRoot.replaceChildren();
      metricsRoot.append(Ui.createAlert({
        variant: "danger",
        title: "加载总览失败",
        message: (err && err.message) || String(err),
      }));
      panelsRoot.replaceChildren();
    }
  }

  /**
   * Render the top metric grid from the overview payload.
   * @param {object} data
   * @param {object} contextStats
   */
  function renderMetrics(data, contextStats) {
    metricsRoot.replaceChildren();
    const jargon = data.jargon || {};
    const protocol = data.protocol || {};
    const statistics = data.statistics || {};

    const grid = document.createElement("div");
    grid.className = "metric-grid";
    grid.style.display = "grid";
    grid.style.gap = "var(--sp-3)";
    grid.style.gridTemplateColumns = "repeat(auto-fit, minmax(160px, 1fr))";

    grid.append(Ui.createMetric({
      label: "黑话词条", value: Core.numberValue(jargon.total),
      hint: "已启用词条总数", icon: "book-open",
    }));
    grid.append(Ui.createMetric({
      label: "待审黑话", value: Core.numberValue(jargon.pending),
      hint: "含候选含义的词条", icon: "tag",
    }));
    grid.append(Ui.createMetric({
      label: "协议成功率", value: Core.formatRate(statistics.success_rate),
      hint: "近 7 天", icon: "chart-no-axes-combined",
    }));
    grid.append(Ui.createMetric({
      label: "上下文运行", value: Core.numberValue(contextStats.runs),
      hint: "近 7 天", icon: "scan-search",
    }));
    grid.append(Ui.createMetric({
      label: "平均 Token", value: Core.numberValue(contextStats.average_tokens),
      hint: "每次上下文构建", icon: "activity",
    }));
    const omitted = Array.isArray(contextStats.sections)
      ? contextStats.sections.reduce((sum, item) => sum + Core.numberValue(item.omitted), 0)
      : 0;
    grid.append(Ui.createMetric({
      label: "省略段", value: omitted,
      hint: "近 7 天预算裁剪", icon: "file-text",
    }));

    metricsRoot.append(grid);
    Core.refreshIcons();
  }

  /**
   * Render the two-column summary panels (protocol + memory).
   * @param {object} data
   */
  function renderPanels(data) {
    panelsRoot.replaceChildren();
    panelsRoot.style.display = "grid";
    panelsRoot.style.gap = "var(--sp-4)";
    panelsRoot.style.gridTemplateColumns = "repeat(auto-fit, minmax(280px, 1fr))";

    panelsRoot.append(renderProtocolPanel(data.protocol || {}, data.statistics || {}));
    // Memory panel is filled asynchronously by loadMemoryStatus().
    const memorySlot = document.createElement("div");
    memorySlot.className = "overview-memory-slot";
    memorySlot.append(Ui.createLoading("加载记忆状态…"));
    panelsRoot.append(memorySlot);
  }

  /**
   * Render the protocol summary panel.
   * @param {object} protocol
   * @param {object} statistics
   * @returns {HTMLElement}
   */
  function renderProtocolPanel(protocol, statistics) {
    const range = statistics.start_date && statistics.end_date
      ? `${statistics.start_date} ~ ${statistics.end_date}`
      : "近 7 天";
    return Ui.createPanel({
      title: "协议合规", icon: "shield-check",
      actions: [() => jumpButton("protocol", "查看日志")],
      body: () => Ui.createDefinitionList([
        { dt: "时间范围", dd: range },
        { dt: "请求总数", dd: String(Core.numberValue(protocol.total)) },
        { dt: "通过", node: Ui.createBadge(String(Core.numberValue(protocol.success)), "success") },
        { dt: "拦截", node: Ui.createBadge(String(Core.numberValue(protocol.blocked)), "danger") },
        { dt: "成功率", dd: Core.formatRate(statistics.success_rate) },
      ], { vertical: true }),
    });
  }

  /**
   * Fetch memory service status and render the memory summary panel.
   */
  async function loadMemoryStatus() {
    if (!ctx || ctx.isStale()) return;
    const reqId = memoryGuard.bump();
    const slot = panelsRoot && panelsRoot.querySelector(".overview-memory-slot");
    if (!slot) return;
    try {
      const data = await Api.getMemoryStatus();
      if (memoryGuard.isStale(reqId) || ctx.isStale()) return;
      slot.replaceChildren();
      slot.append(renderMemoryPanel(data || {}));
    } catch (err) {
      if (memoryGuard.isStale(reqId) || ctx.isStale()) return;
      slot.replaceChildren();
      slot.append(Ui.createAlert({
        variant: "warning",
        title: "记忆状态不可用",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Render the memory service summary panel.
   * @param {object} status
   * @returns {HTMLElement}
   */
  function renderMemoryPanel(status) {
    const stateInfo = MEMORY_STATE_LABELS[status.state] || { label: status.state || "—", variant: "" };
    const entries = [
      { dt: "服务状态", node: Ui.createBadge(stateInfo.label, stateInfo.variant || "") },
      { dt: "总开关", dd: status.enabled ? "启用" : "停用" },
      { dt: "后台任务", dd: status.worker_running ? "运行中" : "空闲" },
    ];
    if (status.openviking_state) {
      entries.push({ dt: "OpenViking", dd: status.openviking_state });
    }
    if (status.last_recall_at) {
      entries.push({
        dt: "最近召回",
        dd: `${Core.formatTime(status.last_recall_at)}（${Core.numberValue(status.last_recall_items)} 条）`,
      });
    }
    if (status.last_error) {
      entries.push({ dt: "最近错误", dd: status.last_error });
    }
    return Ui.createPanel({
      title: "记忆服务", icon: "brain",
      actions: [() => jumpButton("memory", "管理记忆")],
      body: () => Ui.createDefinitionList(entries, { vertical: true }),
    });
  }

  /** Fetch and render observed Provider prompt-cache capabilities. */
  async function loadProviderCache() {
    if (!providerRoot || !ctx || ctx.isStale()) return;
    const reqId = providerGuard.bump();
    try {
      const [cacheData, chatData] = await Promise.all([
        Api.getProviderCacheCapabilities(),
        Api.getChatProviders(),
      ]);
      if (providerGuard.isStale(reqId) || ctx.isStale()) return;
      const observed = Array.isArray(cacheData && cacheData.items) ? cacheData.items : [];
      const providers = Array.isArray(chatData && chatData.providers) ? chatData.providers : [];
      const rows = observed.map((item) => ({
        key: `${item.provider_id || ""}:${item.model || ""}`,
        cells: {
          provider: item.provider_id || "—",
          model: item.model || "—",
          capability: item.capability || "unknown",
          samples: Core.numberValue(item.observed_samples),
          hits: Core.numberValue(item.cached_samples),
          tokens: Core.numberValue(item.input_cached),
        },
      }));
      providerRoot.replaceChildren();
      providerRoot.append(Ui.createPanel({
        title: "Provider Prompt Cache", icon: "database",
        subtitle: `${providers.length} 个 Chat Provider · 真实命中观测，不在插件内复用模型输出`,
        body: () => rows.length ? Ui.createTable({
          columns: [
            { key: "provider", label: "Provider", mono: true },
            { key: "model", label: "Model", mono: true },
            { key: "capability", label: "能力" },
            { key: "samples", label: "样本", num: true },
            { key: "hits", label: "Prefix 命中", num: true },
            { key: "tokens", label: "Cached Token", num: true },
          ],
          rows,
        }) : Ui.createEmptyState({
          title: "暂无 cache 观测",
          message: "usage 缺失保持 unknown，等待 Provider 返回可观测样本。",
        }),
      }));
      Core.refreshIcons();
    } catch (err) {
      if (providerGuard.isStale(reqId) || ctx.isStale()) return;
      providerRoot.replaceChildren(Ui.createAlert({
        variant: "warning", title: "Provider 观测不可用",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /** Fetch and render the latest context runs. */
  async function loadRecentContexts() {
    if (!contextRoot || !ctx || ctx.isStale()) return;
    const reqId = contextGuard.bump();
    try {
      const data = await Api.getContextRuns({ page: 1, page_size: 8 });
      if (contextGuard.isStale(reqId) || ctx.isStale()) return;
      const items = Array.isArray(data && data.items) ? data.items : [];
      const rows = items.map((item) => ({
        key: item.request_id,
        cells: {
          created_at: Core.formatTime(item.created_at),
          request_id: Core.truncateId(item.request_id || "", 12, 4),
          scope: Core.formatScopeLabel(item.scope_type, item.scope_id),
          tokens: Core.numberValue(item.estimated_tokens),
          sections: `${Core.numberValue(item.included_sections)}/${Core.numberValue(item.omitted_sections)}`,
        },
      }));
      contextRoot.replaceChildren(Ui.createPanel({
        title: "最近上下文运行", icon: "scan-search",
        actions: [() => jumpButton("context", "查看全部")],
        body: () => rows.length ? Ui.createTable({
          columns: [
            { key: "created_at", label: "时间", mono: true },
            { key: "request_id", label: "Request ID", mono: true },
            { key: "scope", label: "作用域" },
            { key: "tokens", label: "Token", num: true },
            { key: "sections", label: "包含/省略", num: true },
          ],
          rows,
          onRowClick: () => ctx.navigate("context"),
        }) : Ui.createEmptyState({ title: "暂无运行", message: "尚无上下文构建记录。" }),
      }));
      Core.refreshIcons();
    } catch (err) {
      if (contextGuard.isStale(reqId) || ctx.isStale()) return;
      contextRoot.replaceChildren(Ui.createAlert({
        variant: "warning", title: "上下文运行不可用",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Fetch the latest 5 protocol logs and render a mini list.
   */
  async function loadRecentLogs() {
    if (!logsRoot || !ctx || ctx.isStale()) return;
    const reqId = logsGuard.bump();
    try {
      const data = await Api.getProtocolLogs({ page: 1, page_size: 5 });
      if (logsGuard.isStale(reqId) || ctx.isStale()) return;
      renderRecentLogs(data || {});
    } catch (err) {
      if (logsGuard.isStale(reqId) || ctx.isStale()) return;
      logsRoot.replaceChildren();
      logsRoot.append(Ui.createAlert({
        variant: "warning",
        title: "最近日志加载失败",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Render the recent protocol logs mini list.
   * @param {object} data
   */
  function renderRecentLogs(data) {
    logsRoot.replaceChildren();
    const items = Array.isArray(data.items) ? data.items : [];

    const panel = Ui.createPanel({
      title: "最近协议事件", icon: "history",
      actions: [() => jumpButton("protocol", "查看全部")],
      body: () => {
        if (!items.length) {
          return Ui.createEmptyState({
            title: "暂无日志",
            message: "近期没有回复协议事件。",
          });
        }
        const columns = [
          { key: "created_at", label: "时间", width: "140px", mono: true },
          { key: "action", label: "动作", width: "90px" },
          { key: "stage", label: "阶段", width: "80px" },
          { key: "success", label: "结果", width: "70px" },
          { key: "failure_code", label: "失败码", width: "110px" },
        ];
        const rows = items.map((item) => ({
          key: item.id,
          cells: {
            created_at: Core.formatTime(item.created_at),
            action: item.action || "—",
            stage: { text: item.stage || "—", variant: item.stage === "final" ? "info" : "" },
            success: { text: item.success ? "通过" : "拦截", variant: item.success ? "success" : "danger" },
            failure_code: item.failure_code || "—",
          },
        }));
        return Ui.createTable({ columns, rows });
      },
    });
    logsRoot.append(panel);
    Core.refreshIcons();
  }

  /**
   * Mount the overview view: render scaffolding and load all data in parallel.
   * @param {HTMLElement} root
   * @param {object} viewCtx
   */
  async function mount(root, viewCtx) {
    ctx = viewCtx;
    overviewGuard = Core.requestIdGuard();
    memoryGuard = Core.requestIdGuard();
    logsGuard = Core.requestIdGuard();
    providerGuard = Core.requestIdGuard();
    contextGuard = Core.requestIdGuard();
    root.replaceChildren();

    const shell = document.createElement("div");
    shell.className = "overview-view";
    shell.style.display = "flex";
    shell.style.flexDirection = "column";
    shell.style.gap = "var(--sp-4)";

    metricsRoot = document.createElement("section");
    metricsRoot.className = "overview-metrics";
    shell.append(metricsRoot);

    panelsRoot = document.createElement("section");
    panelsRoot.className = "overview-panels";
    shell.append(panelsRoot);

    providerRoot = document.createElement("section");
    providerRoot.className = "overview-provider";
    shell.append(providerRoot);

    contextRoot = document.createElement("section");
    contextRoot.className = "overview-context";
    shell.append(contextRoot);

    logsRoot = document.createElement("section");
    logsRoot.className = "overview-logs";
    shell.append(logsRoot);

    root.append(shell);

    ctx.setTopbarActions([
      Ui.createButton({
        label: "刷新", variant: "outline", size: "sm", icon: "refresh-cw",
        onClick: () => refreshAll(),
      }),
    ]);

    // Show loading placeholders before parallel fetch completes.
    metricsRoot.append(Ui.createLoading("加载总览数据…"));
    panelsRoot.append(Ui.createLoading("加载面板…"));
    logsRoot.append(Ui.createLoading("加载最近日志…"));
    providerRoot.append(Ui.createLoading("加载 Provider 观测…"));
    contextRoot.append(Ui.createLoading("加载上下文运行…"));

    await Promise.all([
      loadOverview(), loadMemoryStatus(), loadProviderCache(),
      loadRecentContexts(), loadRecentLogs(),
    ]);
  }

  /** Reload all overview sections. */
  function refreshAll() {
    loadOverview();
    loadMemoryStatus();
    loadProviderCache();
    loadRecentContexts();
    loadRecentLogs();
  }

  /** Reset view-scoped state on unmount. */
  function unmount() {
    ctx = null;
    overviewGuard = null;
    memoryGuard = null;
    logsGuard = null;
    providerGuard = null;
    contextGuard = null;
    metricsRoot = null;
    panelsRoot = null;
    providerRoot = null;
    contextRoot = null;
    logsRoot = null;
  }

  global.HumanizeViews.overview = { mount, unmount };
})(window);
