(function registerMemoryView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** 记忆状态 → 徽章映射。 */
  const MEMORY_STATUS = {
    active: { label: "已激活", variant: "success" },
    candidate: { label: "候选", variant: "info" },
    rejected: { label: "已拒绝", variant: "danger" },
    superseded: { label: "已弃用", variant: "warning" },
  };

  /** 记忆类型 → 中文标签。 */
  const MEMORY_TYPE = {
    profile: "画像",
    preference: "偏好",
    entity: "实体",
    event: "事件",
  };

  /** 任务状态 → 徽章映射。 */
  const JOB_STATUS = {
    pending: { label: "待处理", variant: "warning" },
    running: { label: "进行中", variant: "info" },
    retry: { label: "重试中", variant: "warning" },
    completed: { label: "已完成", variant: "success" },
    dead: { label: "已死亡", variant: "danger" },
  };

  /**
   * Build a status badge from a status string using the given map.
   * @param {string} status
   * @param {object} map
   * @returns {HTMLElement}
   */
  function statusBadge(status, map) {
    const info = (map || MEMORY_STATUS)[status] || { label: status || "--", variant: "" };
    return Ui.createBadge(info.label, info.variant);
  }

  /** View state (reset on each mount). */
  let state = null;
  let ctx = null;
  let listGuard = null;
  let detailGuard = null;
  let statusGuard = null;
  let optionsGuard = null;
  let listRoot = null;
  let detailRoot = null;
  let paginationRoot = null;
  let statusRoot = null;
  let agentSelect = null;
  let scopeSelect = null;
  let activeDrawer = null;

  /** Create a fresh state object. */
  function freshState() {
    return {
      page: 1, pageSize: 20,
      search: "", status: "", memoryType: "",
      agentId: "", scopeToken: "",
      selectedId: null, total: 0,
      agents: [], scopes: [],
      statusReady: false,
    };
  }

  /**
   * Load runtime status banner; degrade gracefully when service is not ready.
   */
  async function loadStatus() {
    if (!statusRoot || !ctx || ctx.isStale()) return;
    const reqId = statusGuard.bump();
    statusRoot.replaceChildren();
    statusRoot.append(Ui.createLoading("加载记忆状态…"));
    try {
      const data = await Api.getMemoryStatus();
      if (statusGuard.isStale(reqId) || ctx.isStale()) return;
      state.statusReady = String(data.state || "") === "ready";
      renderStatus(data);
    } catch (err) {
      if (statusGuard.isStale(reqId) || ctx.isStale()) return;
      statusRoot.replaceChildren();
      statusRoot.append(Ui.createAlert({
        variant: "warning", title: "记忆服务状态未知",
        message: (err && err.message) || String(err),
      }));
    }
  }

  /**
   * Render the runtime status banner with metric cards.
   * @param {object} data
   */
  function renderStatus(data) {
    statusRoot.replaceChildren();
    const grid = document.createElement("div");
    grid.className = "metric-grid";

    const stateInfo = String(data.state || "unknown");
    const stateVariant = stateInfo === "ready" ? "success"
      : stateInfo === "error" ? "danger" : "warning";
    grid.append(Ui.createMetric({
      label: "服务状态", icon: "activity",
      value: stateInfo === "ready" ? "就绪" : stateInfo,
      hint: data.reason || "",
    }));
    grid.append(Ui.createMetric({
      label: "OpenViking", icon: "database",
      value: String(data.openviking_state || "--"),
      hint: data.openviking_error || "",
    }));
    grid.append(Ui.createMetric({
      label: "工作线程", icon: "cpu",
      value: data.worker_running ? "运行中" : "已停止",
    }));
    grid.append(Ui.createMetric({
      label: "最近召回", icon: "search",
      value: data.last_recall_items != null ? String(data.last_recall_items) : "--",
      hint: data.last_recall_at
        ? `${Core.formatTime(data.last_recall_at)} · ${data.last_recall_duration_ms ?? 0}ms`
        : "暂无",
    }));

    const providers = [];
    if (data.embedding_enabled) providers.push("Embedding");
    if (data.extraction_provider_enabled) providers.push("抽取");
    if (data.rerank_enabled) providers.push("Rerank");
    grid.append(Ui.createMetric({
      label: "Provider 链路", icon: "link",
      value: providers.length ? providers.join(" · ") : "仅本地",
    }));

    statusRoot.append(grid);

    if (!state.statusReady) {
      statusRoot.append(Ui.createAlert({
        variant: "warning", title: "记忆服务尚未就绪",
        message: "召回与编辑操作将不可用，请先在设置中启用记忆并完成身份初始化。",
      }));
    }
  }

  /**
   * Load agent options and scope options in parallel, then populate selectors.
   */
  async function loadOptions() {
    if (!ctx || ctx.isStale()) return;
    const reqId = optionsGuard.bump();
    try {
      const [agentData, overviewData] = await Promise.all([
        Api.getMemoryAgentOptions(),
        state.statusReady ? Api.getMemoryOverview() : Promise.resolve(null),
      ]);
      if (optionsGuard.isStale(reqId) || ctx.isStale()) return;
      state.agents = Array.isArray(agentData && agentData.items) ? agentData.items : [];
      populateAgentSelect(state.agents);
      if (overviewData && Array.isArray(overviewData.scope_options)) {
        state.scopes = overviewData.scope_options;
      }
      populateScopeSelect(state.scopes);
    } catch (err) {
      if (optionsGuard.isStale(reqId) || ctx.isStale()) return;
      populateAgentSelect([]);
      populateScopeSelect([]);
    }
  }

  /**
   * Populate the agent selector with agent options.
   * @param {Array} agents
   */
  function populateAgentSelect(agents) {
    if (!agentSelect) return;
    const opts = [{ value: "", label: "全部 Agent" }];
    agents.forEach((a) => {
      if (!a || !a.id) return;
      const label = a.label || a.id;
      opts.push({
        value: a.id,
        label: a.id === "*" ? `${label}（共享）` : label,
      });
    });
    agentSelect.replaceChildren();
    opts.forEach((o) => {
      const node = document.createElement("option");
      node.value = o.value;
      node.textContent = o.label;
      agentSelect.append(node);
    });
    agentSelect.value = state.agentId || "";
  }

  /**
   * Populate the scope selector with signed scope tokens.
   * @param {Array} scopes
   */
  function populateScopeSelect(scopes) {
    if (!scopeSelect) return;
    const opts = [{ value: "", label: "全部作用域" }];
    scopes.forEach((s) => {
      if (!s || !s.scope_token) return;
      opts.push({ value: s.scope_token, label: s.scope_label || s.scope_type });
    });
    scopeSelect.replaceChildren();
    opts.forEach((o) => {
      const node = document.createElement("option");
      node.value = o.value;
      node.textContent = o.label;
      scopeSelect.append(node);
    });
    scopeSelect.value = state.scopeToken || "";
  }

  /**
   * Fetch and render the memory list using current filters.
   */
  async function loadList() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    if (!state.statusReady) {
      listRoot.replaceChildren();
      listRoot.append(Ui.createEmptyState({
        title: "记忆服务未就绪", message: "请在记忆服务就绪后查看条目。",
      }));
      paginationRoot.replaceChildren();
      return;
    }
    const reqId = listGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载记忆列表…"));
    try {
      const data = await Api.getMemories({
        page: state.page, page_size: state.pageSize,
        search: state.search, status: state.status,
        type: state.memoryType,
        scope_token: state.scopeToken,
        agent_id: state.agentId,
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
   * Render the list table from API response.
   * @param {*} data
   */
  function renderList(data) {
    const norm = Core.normalizeCollection(data);
    state.total = norm.total;
    listRoot.replaceChildren();

    if (!norm.items.length) {
      listRoot.append(Ui.createEmptyState({
        title: "暂无记忆",
        message: (state.search || state.status || state.memoryType || state.scopeToken)
          ? "未找到匹配的记忆，请调整筛选条件。"
          : "尚未写入任何长期记忆条目。",
      }));
      renderPagination();
      return;
    }

    const columns = [
      { key: "memory_key", label: "键" },
      { key: "scope", label: "作用域", width: "120px" },
      { key: "memory_type", label: "类型", width: "80px" },
      { key: "status", label: "状态", width: "80px" },
      { key: "confidence", label: "置信度", width: "80px" },
      { key: "updated_at", label: "更新", width: "130px", mono: true },
    ];

    const rows = norm.items.map((item) => ({
      key: item.id,
      cells: {
        memory_key: item.memory_key || "--",
        scope: { text: item.scope_label || item.scope_type || "--" },
        memory_type: MEMORY_TYPE[item.memory_type] || item.memory_type || "--",
        status: { text: item.status, variant: (MEMORY_STATUS[item.status] || {}).variant },
        confidence: Core.formatScore(item.confidence),
        updated_at: Core.formatTime(item.updated_at),
      },
    }));

    listRoot.append(Ui.createTable({
      columns, rows,
      selectedKey: state.selectedId,
      onRowClick: (row) => selectMemory(row.key),
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
   * Select a memory and load its detail.
   * @param {string} id
   */
  async function selectMemory(id) {
    state.selectedId = id;
    await loadDetail(id);
  }

  /**
   * Fetch and render a single memory detail.
   * @param {string} id
   */
  async function loadDetail(id) {
    if (!detailRoot || !ctx || ctx.isStale()) return;
    const reqId = detailGuard.bump();
    detailRoot.replaceChildren();
    detailRoot.append(Ui.createLoading("加载记忆详情…"));
    try {
      const data = await Api.getMemoryDetail(id);
      if (detailGuard.isStale(reqId) || ctx.isStale()) return;
      if (!data) {
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "记忆不存在", message: "该记忆可能已被清理。",
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
   * Render the full memory detail: header, content, meta, evidence, revisions, audit.
   * @param {object} data
   */
  function renderDetail(data) {
    detailRoot.replaceChildren();
    detailRoot.style.gap = "var(--sp-4)";
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";

    detailRoot.append(renderDetailHeader(data));
    detailRoot.append(renderContentPanel(data));
    if (data.structured_value && Object.keys(data.structured_value).length) {
      detailRoot.append(renderStructuredPanel(data.structured_value));
    }
    if (Array.isArray(data.evidence) && data.evidence.length) {
      detailRoot.append(renderEvidencePanel(data.evidence));
    }
    if (Array.isArray(data.revisions) && data.revisions.length) {
      detailRoot.append(renderRevisionsPanel(data.revisions));
    }
    if (Array.isArray(data.audit) && data.audit.length) {
      detailRoot.append(renderAuditPanel(data.audit));
    }
    Core.refreshIcons();
  }

  /**
   * Render detail header with activate/approve/reject/delete and edit actions.
   * @param {object} data
   */
  function renderDetailHeader(data) {
    const actions = [];
    if (data.status !== "active") {
      actions.push(() => Ui.createButton({
        label: "激活", variant: "primary", size: "sm", icon: "check",
        onClick: () => doAction("activate"),
      }));
    }
    if (data.status !== "rejected") {
      actions.push(() => Ui.createButton({
        label: "拒绝", variant: "danger", size: "sm", icon: "ban",
        onClick: () => doAction("reject"),
      }));
    }
    actions.push(() => Ui.createButton({
      label: "编辑", variant: "outline", size: "sm", icon: "pencil",
      onClick: () => openEditDrawer(data),
    }));
    actions.push(() => Ui.createButton({
      label: "删除", variant: "ghost", size: "sm", icon: "trash-2",
      onClick: () => {
        if (confirm("确定删除此记忆？此操作不可撤销。")) doAction("delete");
      },
    }));

    return Ui.createPanel({
      title: data.memory_key || "--",
      subtitle: `${Core.truncateId(String(data.id || ""), 12, 4)} · ${data.scope_label || data.scope_type || ""}`,
      icon: "brain",
      actions,
      body: () => Ui.createDefinitionList([
        { dt: "状态", node: statusBadge(data.status, MEMORY_STATUS) },
        { dt: "类型", dd: MEMORY_TYPE[data.memory_type] || data.memory_type || "--" },
        { dt: "Agent", dd: data.agent_id || "--" },
        { dt: "置信度", dd: Core.formatScore(data.confidence) },
        { dt: "重要度", dd: Core.formatScore(data.importance) },
        { dt: "版本", dd: String(data.version ?? 0) },
        { dt: "创建", dd: Core.formatTime(data.created_at) },
        { dt: "更新", dd: Core.formatTime(data.updated_at) },
        { dt: "URI", dd: data.uri || "--", mono: true },
      ], { vertical: true }),
    });
  }

  /**
   * Render the content panel with a trace viewer.
   * @param {object} data
   */
  function renderContentPanel(data) {
    return Ui.createPanel({
      title: "记忆内容", icon: "file-text",
      body: () => Ui.createTraceViewer({ content: data.content || "" }),
    });
  }

  /**
   * Render the structured value panel as formatted JSON.
   * @param {object} value
   */
  function renderStructuredPanel(value) {
    return Ui.createPanel({
      title: "结构化数据", icon: "braces",
      body: () => Ui.createTraceViewer({
        content: JSON.stringify(value, null, 2), format: "json",
      }),
    });
  }

  /**
   * Render the evidence panel as a record list.
   * @param {Array} evidence
   */
  function renderEvidencePanel(evidence) {
    return Ui.createPanel({
      title: `证据（${evidence.length}）`, icon: "scroll-text",
      body: () => Ui.createRecords(evidence.map((ev) => ({
        title: Core.formatTime(ev.observed_at || ev.created_at || ""),
        meta: [ev.source || ev.message_id || ""].filter(Boolean),
        body: ev.source_text || ev.text || "",
      }))),
    });
  }

  /**
   * Render the revisions panel as a record list.
   * @param {Array} revisions
   */
  function renderRevisionsPanel(revisions) {
    return Ui.createPanel({
      title: `修订历史（${revisions.length}）`, icon: "history",
      body: () => Ui.createRecords(revisions.slice(0, 20).map((rev) => ({
        title: `v${rev.version ?? rev.revision ?? "?"} · ${rev.action || rev.operation || ""}`,
        meta: [
          rev.actor || "",
          Core.formatTime(rev.created_at),
        ].filter(Boolean),
        body: rev.summary || rev.reason || "",
      }))),
    });
  }

  /**
   * Render the audit panel as a record list.
   * @param {Array} audit
   */
  function renderAuditPanel(audit) {
    return Ui.createPanel({
      title: `审计（${audit.length}）`, icon: "shield-check",
      body: () => Ui.createRecords(audit.slice(0, 20).map((row) => ({
        title: `${row.action || ""} · ${row.actor || ""}`,
        meta: [Core.formatTime(row.created_at), row.reason || ""].filter(Boolean),
        body: "",
      }))),
    });
  }

  /**
   * Apply a memory action and reload detail + list on success.
   * @param {string} action
   * @param {object} [extra]
   */
  async function doAction(action, extra) {
    if (!state.selectedId) return;
    const payload = { action, id: state.selectedId, ...(extra || {}) };
    try {
      await Api.memoryAction(payload);
      Ui.toastSuccess("操作已提交");
      if (action === "delete") {
        state.selectedId = null;
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "未选中记忆", message: "从左侧列表选择一条记忆查看详情。",
        }));
        await loadList();
      } else {
        await loadDetail(state.selectedId);
        await loadList();
      }
    } catch (err) {
      Ui.toastError((err && err.message) || String(err));
    }
  }

  /**
   * Open the create/edit drawer with a form.
   * @param {object|null} data Null for create, existing memory for edit.
   */
  function openEditDrawer(data) {
    closeDrawer();
    const isCreate = !data;
    const form = buildMemoryForm(data);
    const drawer = Ui.createDrawer({
      title: isCreate ? "新增记忆" : "编辑记忆",
      icon: isCreate ? "plus" : "pencil",
      width: "560px",
      body: form.root,
      onClose: () => { activeDrawer = null; },
    });
    form.submitBtn.addEventListener("click", async () => {
      const payload = form.collect();
      if (!payload) return;
      payload.action = isCreate ? "create" : "update";
      if (!isCreate) {
        payload.id = state.selectedId;
        payload.revision = data.version;
      }
      try {
        await Api.memoryAction(payload);
        Ui.toastSuccess(isCreate ? "记忆已创建" : "记忆已更新");
        closeDrawer();
        if (isCreate) {
          state.page = 1;
          await loadList();
        } else {
          await loadDetail(state.selectedId);
          await loadList();
        }
      } catch (err) {
        Ui.toastError((err && err.message) || String(err));
      }
    });
    activeDrawer = drawer;
    drawer.open();
  }

  /**
   * Build the memory create/edit form.
   * @param {object|null} data
   * @returns {{root:HTMLElement, submitBtn:HTMLElement, collect:()=>object|null}}
   */
  function buildMemoryForm(data) {
    const root = document.createElement("div");
    root.className = "drawer-body";
    root.style.display = "flex";
    root.style.flexDirection = "column";
    root.style.gap = "var(--sp-3)";

    const isCreate = !data;
    const keyInput = Ui.createInput({
      value: data ? data.memory_key : "", placeholder: "记忆键，如 user_name",
    });
    root.append(Ui.createField({
      label: "记忆键", required: true, control: keyInput,
      hint: "同一作用域与 Agent 下唯一标识。",
    }));

    const typeSelect = Ui.createSelect({
      value: data ? data.memory_type : "preference",
      options: Object.entries(MEMORY_TYPE).map(([v, l]) => ({ value: v, label: l })),
    });
    root.append(Ui.createField({ label: "类型", control: typeSelect }));

    const scopeSel = Ui.createSelect({
      value: data ? data.scope_token : "",
      options: state.scopes
        .filter((s) => s.scope_token)
        .map((s) => ({ value: s.scope_token, label: s.scope_label || s.scope_type })),
    });
    root.append(Ui.createField({
      label: "作用域", required: isCreate, control: scopeSel,
      hint: isCreate ? "新增必须选择作用域。" : "作用域不可变更。",
    }));

    const agentInput = Ui.createInput({
      value: data ? data.agent_id : "default", placeholder: "default",
    });
    root.append(Ui.createField({ label: "Agent", control: agentInput }));

    const contentTa = Ui.createTextarea({
      value: data ? data.content : "", rows: 5, placeholder: "记忆正文内容…",
    });
    root.append(Ui.createField({
      label: "内容", required: true, control: contentTa,
    }));

    const structTa = Ui.createTextarea({
      value: data && data.structured_value ? JSON.stringify(data.structured_value, null, 2) : "",
      rows: 4, placeholder: "{}", mono: true,
    });
    root.append(Ui.createField({
      label: "结构化数据（JSON）", control: structTa,
      hint: "可选，留空或合法 JSON 对象。",
    }));

    const confInput = Ui.createInput({
      type: "number", value: data ? data.confidence : 0.8,
    });
    confInput.step = "0.05"; confInput.min = "0"; confInput.max = "1";
    const impInput = Ui.createInput({
      type: "number", value: data ? data.importance : 0.5,
    });
    impInput.step = "0.05"; impInput.min = "0"; impInput.max = "1";
    const scoreRow = document.createElement("div");
    scoreRow.className = "flex gap-3 flex-wrap";
    scoreRow.append(Ui.createField({ label: "置信度", control: confInput, sm: true }));
    scoreRow.append(Ui.createField({ label: "重要度", control: impInput, sm: true }));
    root.append(scoreRow);

    if (!isCreate) {
      keyInput.disabled = true;
      typeSelect.disabled = true;
      scopeSel.disabled = true;
      agentInput.disabled = true;
    }

    const submitBtn = Ui.createButton({
      label: isCreate ? "创建" : "保存", variant: "primary", icon: "save", block: true,
    });
    root.append(submitBtn);

    /**
     * Collect form values into a payload object.
     * @returns {object|null}
     */
    function collect() {
      const memoryKey = keyInput.value.trim();
      const content = contentTa.value.trim();
      if (isCreate && !memoryKey) { Ui.toastWarning("请填写记忆键"); return null; }
      if (!content) { Ui.toastWarning("请填写内容"); return null; }
      if (isCreate && !scopeSel.value) { Ui.toastWarning("请选择作用域"); return null; }
      const selectedScope = state.scopes.find((item) => item.scope_token === scopeSel.value);
      if (isCreate && selectedScope && selectedScope.scope_type === "global"
          && !confirm("全局记忆会对这个 Agent 的所有聊天生效，确定继续吗？")) {
        return null;
      }
      const payload = {
        memory_key: memoryKey,
        memory_type: typeSelect.value,
        content,
        confidence: Core.numberValue(confInput.value, 0.8),
        importance: Core.numberValue(impInput.value, 0.5),
      };
      if (isCreate) {
        payload.scope_token = scopeSel.value;
        payload.agent_id = agentInput.value.trim() || "default";
        payload.status = "candidate";
      }
      const structRaw = structTa.value.trim();
      if (structRaw) {
        try {
          const parsed = JSON.parse(structRaw);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            payload.structured_value = parsed;
          } else {
            Ui.toastWarning("结构化数据必须是 JSON 对象");
            return null;
          }
        } catch (e) {
          Ui.toastWarning("结构化数据 JSON 格式错误");
          return null;
        }
      }
      return payload;
    }

    return { root, submitBtn, collect };
  }

  /**
   * Open the recall debug drawer.
   */
  function openRecallDrawer() {
    closeDrawer();
    const form = buildRecallForm();
    const resultRoot = document.createElement("div");
    resultRoot.style.display = "flex";
    resultRoot.style.flexDirection = "column";
    resultRoot.style.gap = "var(--sp-3)";

    const drawer = Ui.createDrawer({
      title: "召回测试", icon: "search", width: "640px",
      body: form.root, footer: resultRoot,
      onClose: () => { activeDrawer = null; },
    });
    form.runBtn.addEventListener("click", async () => {
      const params = form.collect();
      if (!params) return;
      resultRoot.replaceChildren();
      resultRoot.append(Ui.createLoading("召回中…"));
      try {
        const data = await Api.debugMemoryRecall(params);
        renderRecallResult(resultRoot, data);
      } catch (err) {
        resultRoot.replaceChildren();
        resultRoot.append(Ui.createAlert({
          variant: "danger", title: "召回失败",
          message: (err && err.message) || String(err),
        }));
      }
    });
    activeDrawer = drawer;
    drawer.open();
  }

  /**
   * Build the recall test form.
   * @returns {{root:HTMLElement, runBtn:HTMLElement, collect:()=>object|null}}
   */
  function buildRecallForm() {
    const root = document.createElement("div");
    root.className = "drawer-body";
    root.style.display = "flex";
    root.style.flexDirection = "column";
    root.style.gap = "var(--sp-3)";

    const queryTa = Ui.createTextarea({
      rows: 3, placeholder: "输入测试查询文本…",
    });
    root.append(Ui.createField({ label: "查询", required: true, control: queryTa }));

    const scopeSel = Ui.createSelect({
      value: state.scopeToken,
      options: state.scopes
        .filter((s) => s.scope_token)
        .map((s) => ({ value: s.scope_token, label: s.scope_label || s.scope_type })),
    });
    root.append(Ui.createField({
      label: "作用域", required: true, control: scopeSel,
      hint: "未选择则使用全局作用域。",
    }));

    const agentSel = Ui.createSelect({
      value: state.agentId,
      options: state.agents
        .filter((a) => a.id && a.id !== "*" && a.debuggable !== false)
        .map((a) => ({ value: a.id, label: a.label || a.id })),
    });
    if (!agentSel.querySelector("option")) {
      const def = document.createElement("option");
      def.value = "default"; def.textContent = "default"; agentSel.append(def);
      agentSel.value = "default";
    }
    root.append(Ui.createField({
      label: "Agent", required: true, control: agentSel,
      hint: "共享 Agent 不可用于召回测试。",
    }));

    const typeSel = Ui.createSelect({
      value: "",
      options: [{ value: "", label: "全部类型" }]
        .concat(Object.entries(MEMORY_TYPE).map(([v, l]) => ({ value: v, label: l }))),
    });
    root.append(Ui.createField({ label: "类型", control: typeSel }));

    const runBtn = Ui.createButton({
      label: "执行召回", variant: "primary", icon: "play", block: true,
    });
    root.append(runBtn);

    /**
     * Collect recall parameters.
     * @returns {object|null}
     */
    function collect() {
      const query = queryTa.value.trim();
      if (!query) { Ui.toastWarning("请输入查询文本"); return null; }
      const params = { query };
      if (scopeSel.value) params.scope_token = scopeSel.value;
      const agentId = agentSel.value || "default";
      if (!agentId || agentId === "*") {
        Ui.toastWarning("召回测试必须指定具体 Agent");
        return null;
      }
      params.agent_id = agentId;
      if (typeSel.value) params.type = typeSel.value;
      return params;
    }

    return { root, runBtn, collect };
  }

  /**
   * Render the recall debug result.
   * @param {HTMLElement} container
   * @param {object} data
   */
  function renderRecallResult(container, data) {
    container.replaceChildren();
    container.append(Ui.createAlert({
      variant: data.included ? "success" : "info",
      title: data.included ? `命中 ${data.items.length} 条` : "无命中",
      message: data.included ? "以下记忆将被注入到上下文。" : "未召回任何记忆，请调整查询或作用域。",
    }));
    if (Array.isArray(data.items) && data.items.length) {
      data.items.forEach((item) => {
        container.append(Ui.createPanel({
          title: item.memory_key || "--",
          subtitle: `${item.scope_label || item.scope_type || ""} · 置信度 ${Core.formatScore(item.confidence)}`,
          icon: "brain",
          body: () => Ui.createTraceViewer({ content: item.content || "" }),
        }));
      });
    }
    if (data.content) {
      container.append(Ui.createPanel({
        title: "注入片段", icon: "file-text",
        body: () => Ui.createTraceViewer({ content: data.content }),
      }));
    }
    Core.refreshIcons();
  }

  /**
   * Open the background jobs drawer.
   */
  function openJobsDrawer() {
    closeDrawer();
    const jobsRoot = document.createElement("div");
    jobsRoot.style.display = "flex";
    jobsRoot.style.flexDirection = "column";
    jobsRoot.style.gap = "var(--sp-3)";

    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";
    const statusSel = Ui.createSelect({
      value: "",
      options: [
        { value: "", label: "全部状态" },
        { value: "pending", label: "待处理" },
        { value: "running", label: "进行中" },
        { value: "retry", label: "重试中" },
        { value: "completed", label: "已完成" },
        { value: "dead", label: "已死亡" },
      ],
      sm: true,
    });
    const refreshBtn = Ui.createButton({
      label: "刷新", variant: "ghost", size: "sm", icon: "refresh-cw",
    });
    const spacer = document.createElement("div");
    spacer.className = "toolbar-spacer";
    toolbar.append(statusSel, spacer, refreshBtn);
    jobsRoot.append(toolbar);

    const listWrap = document.createElement("div");
    listWrap.style.display = "flex";
    listWrap.style.flexDirection = "column";
    listWrap.style.gap = "var(--sp-2)";
    jobsRoot.append(listWrap);

    /** Load jobs into the drawer list. */
    async function loadJobs() {
      const reqId = listGuard.bump();
      listWrap.replaceChildren();
      listWrap.append(Ui.createLoading("加载任务…"));
      try {
        const data = await Api.getMemoryJobs({
          page: 1, page_size: 50, status: statusSel.value,
        });
        if (listGuard.isStale(reqId)) return;
        renderJobs(listWrap, data);
      } catch (err) {
        if (listGuard.isStale(reqId)) return;
        listWrap.replaceChildren();
        listWrap.append(Ui.createAlert({
          variant: "danger", title: "加载失败",
          message: (err && err.message) || String(err),
        }));
      }
    }

    statusSel.addEventListener("change", loadJobs);
    refreshBtn.addEventListener("click", loadJobs);

    const drawer = Ui.createDrawer({
      title: "后台任务", icon: "list-todo", width: "640px",
      body: jobsRoot,
      onClose: () => { activeDrawer = null; },
    });
    activeDrawer = drawer;
    drawer.open();
    loadJobs();
  }

  /**
   * Render the jobs list as record cards.
   * @param {HTMLElement} container
   * @param {object} data
   */
  function renderJobs(container, data) {
    container.replaceChildren();
    const norm = Core.normalizeCollection(data);
    if (!norm.items.length) {
      container.append(Ui.createEmptyState({
        title: "暂无任务", message: "没有符合条件的后台任务。",
      }));
      return;
    }
    norm.items.forEach((job) => {
      const meta = [
        `#${job.id}`,
        job.job_type || "",
        job.agent_id || "",
        Core.formatTime(job.updated_at),
      ].filter(Boolean);
      if (job.attempts) meta.push(`尝试 ${job.attempts}`);
      container.append(Ui.createPanel({
        title: job.job_key || job.request_id || `任务 ${job.id}`,
        subtitle: meta.join(" · "),
        icon: "cpu",
        actions: [() => statusBadge(job.status, JOB_STATUS)],
        body: () => {
          const items = [];
          if (job.provider_id) items.push({ dt: "Provider", dd: job.provider_id });
          if (job.scope_label || job.scope_type) {
            items.push({ dt: "作用域", dd: job.scope_label || job.scope_type });
          }
          if (job.next_run_at) items.push({ dt: "下次运行", dd: Core.formatTime(job.next_run_at) });
          if (job.completed_at) items.push({ dt: "完成时间", dd: Core.formatTime(job.completed_at) });
          if (job.error) items.push({ dt: "错误", dd: job.error });
          if (!items.length) return document.createTextNode("无附加信息。");
          return Ui.createDefinitionList(items, { vertical: true });
        },
      }));
    });
    Core.refreshIcons();
  }

  /** Close the active drawer if any. */
  function closeDrawer() {
    if (activeDrawer) {
      activeDrawer.close();
      activeDrawer = null;
    }
  }

  /**
   * Render the main toolbar with filters and action buttons.
   * @returns {HTMLElement}
   */
  function renderToolbar() {
    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";

    agentSelect = Ui.createSelect({
      value: state.agentId, sm: true, options: [{ value: "", label: "全部 Agent" }],
      onChange: (val) => { state.agentId = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "Agent", control: agentSelect, sm: true }));

    scopeSelect = Ui.createSelect({
      value: state.scopeToken, sm: true, options: [{ value: "", label: "全部作用域" }],
      onChange: (val) => { state.scopeToken = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "作用域", control: scopeSelect, sm: true }));

    const typeSel = Ui.createSelect({
      value: state.memoryType, sm: true,
      options: [{ value: "", label: "全部类型" }]
        .concat(Object.entries(MEMORY_TYPE).map(([v, l]) => ({ value: v, label: l }))),
      onChange: (val) => { state.memoryType = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "类型", control: typeSel, sm: true }));

    const statusSel = Ui.createSelect({
      value: state.status, sm: true,
      options: [
        { value: "", label: "全部状态" },
        { value: "active", label: "已激活" },
        { value: "candidate", label: "候选" },
        { value: "rejected", label: "已拒绝" },
        { value: "superseded", label: "已弃用" },
      ],
      onChange: (val) => { state.status = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "状态", control: statusSel, sm: true }));

    const searchInput = Ui.createInput({
      value: state.search, placeholder: "搜索记忆键或内容…", sm: true,
      onInput: Core.debounce((val) => {
        state.search = val.trim();
        state.page = 1;
        loadList();
      }, 350),
    });
    toolbar.append(Ui.createField({ label: "搜索", control: searchInput, sm: true }));

    const spacer = document.createElement("div");
    spacer.className = "toolbar-spacer";
    toolbar.append(spacer);

    toolbar.append(Ui.createButton({
      icon: "search", variant: "ghost", size: "sm", title: "召回测试",
      onClick: openRecallDrawer,
    }));
    toolbar.append(Ui.createButton({
      icon: "list-todo", variant: "ghost", size: "sm", title: "后台任务",
      onClick: openJobsDrawer,
    }));
    toolbar.append(Ui.createButton({
      icon: "plus", variant: "primary", size: "sm", title: "新增记忆",
      onClick: () => openEditDrawer(null),
    }));
    toolbar.append(Ui.createButton({
      icon: "refresh-cw", variant: "ghost", size: "sm", title: "刷新",
      onClick: () => { loadStatus(); loadList(); },
    }));

    return toolbar;
  }

  global.HumanizeViews.memory = {
    async mount(root, context) {
      ctx = context;
      state = freshState();
      listGuard = Core.requestIdGuard();
      detailGuard = Core.requestIdGuard();
      statusGuard = Core.requestIdGuard();
      optionsGuard = Core.requestIdGuard();

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
      title.textContent = "长期记忆";
      const sub = document.createElement("p");
      sub.className = "page-subtitle";
      sub.textContent = "OpenViking 记忆条目、召回调试与后台任务";
      headerText.append(title, sub);
      header.append(headerText);
      root.append(header);

      statusRoot = document.createElement("div");
      statusRoot.className = "status-banner";
      root.append(statusRoot);

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
        title: "未选中记忆",
        message: "从左侧列表选择一条记忆查看详情。",
      }));
      right.append(detailRoot);

      split.append(left, right);
      root.append(split);

      await loadStatus();
      await loadOptions();
      await loadList();
    },
    unmount() {
      closeDrawer();
      ctx = null; state = null;
      listRoot = null; detailRoot = null; paginationRoot = null; statusRoot = null;
      agentSelect = null; scopeSelect = null;
      listGuard = null; detailGuard = null; statusGuard = null;
      optionsGuard = null;
    },
  };
})(window);
