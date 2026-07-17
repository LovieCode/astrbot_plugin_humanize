(function registerExamplesView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** 样例状态 → 徽章映射。 */
  const EXAMPLE_STATUS = {
    draft: { label: "草稿", variant: "info" },
    approved: { label: "已批准", variant: "success" },
    rejected: { label: "已拒绝", variant: "danger" },
    tombstoned: { label: "已归档", variant: "warning" },
  };

  /**
   * Build a status badge for a reply example status.
   * @param {string} status
   * @returns {HTMLElement}
   */
  function statusBadge(status) {
    const info = EXAMPLE_STATUS[status] || { label: status || "--", variant: "" };
    return Ui.createBadge(info.label, info.variant);
  }

  /** View state (reset on each mount). */
  let state = null;
  let ctx = null;
  let listGuard = null;
  let detailGuard = null;
  let optionsGuard = null;
  let listRoot = null;
  let detailRoot = null;
  let paginationRoot = null;
  let agentSelect = null;
  let scopeSelect = null;
  let activeDrawer = null;

  /** Create a fresh state object. */
  function freshState() {
    return {
      page: 1, pageSize: 20,
      search: "", status: "", enabled: "",
      topic: "", intent: "",
      agentId: "", scopeToken: "",
      selectedId: null, total: 0,
      agents: [], scopes: [],
    };
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
        Api.getMemoryOverview().catch(() => null),
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
      opts.push({ value: a.id, label: a.label || a.id });
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
   * Fetch and render the reply examples list using current filters.
   */
  async function loadList() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    const reqId = listGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载样例列表…"));
    try {
      const data = await Api.getReplyExamples({
        page: state.page, page_size: state.pageSize,
        search: state.search, status: state.status,
        enabled: state.enabled, topic: state.topic, intent: state.intent,
        scope_token: state.scopeToken, agent_id: state.agentId,
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
        title: "暂无样例",
        message: (state.search || state.status || state.scopeToken)
          ? "未找到匹配的样例，请调整筛选条件。"
          : "尚未创建任何回复样例。",
      }));
      renderPagination();
      return;
    }

    const columns = [
      { key: "title", label: "标题" },
      { key: "scope", label: "作用域", width: "120px" },
      { key: "status", label: "状态", width: "80px" },
      { key: "enabled", label: "启用", width: "60px" },
      { key: "quality_score", label: "质量", width: "70px" },
      { key: "turns", label: "轮次", width: "60px", num: true },
      { key: "updated_at", label: "更新", width: "130px", mono: true },
    ];

    const rows = norm.items.map((item) => ({
      key: item.id,
      cells: {
        title: item.title || "--",
        scope: { text: item.scope_label || item.scope_type || "--" },
        status: { text: item.status, variant: (EXAMPLE_STATUS[item.status] || {}).variant },
        enabled: item.enabled ? "是" : "否",
        quality_score: Core.formatScore(item.quality_score),
        turns: String((item.turns || []).length),
        updated_at: Core.formatTime(item.updated_at),
      },
    }));

    listRoot.append(Ui.createTable({
      columns, rows,
      selectedKey: state.selectedId,
      onRowClick: (row) => selectExample(row.key),
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
   * Select an example and load its detail.
   * @param {number} id
   */
  async function selectExample(id) {
    state.selectedId = id;
    await loadDetail(id);
  }

  /**
   * Fetch and render a single reply example detail.
   * @param {number} id
   */
  async function loadDetail(id) {
    if (!detailRoot || !ctx || ctx.isStale()) return;
    const reqId = detailGuard.bump();
    detailRoot.replaceChildren();
    detailRoot.append(Ui.createLoading("加载样例详情…"));
    try {
      const data = await Api.getReplyExampleDetail(id);
      if (detailGuard.isStale(reqId) || ctx.isStale()) return;
      if (!data) {
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "样例不存在", message: "该样例可能已被删除。",
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
   * Render the full example detail.
   * @param {object} data
   */
  function renderDetail(data) {
    detailRoot.replaceChildren();
    detailRoot.style.gap = "var(--sp-4)";
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";

    detailRoot.append(renderDetailHeader(data));
    detailRoot.append(renderTurnsPanel(data.turns || []));
    detailRoot.append(renderIdealReplyPanel(data.ideal_reply));
    if (data.conditions || data.exclusions || data.notes) {
      detailRoot.append(renderMetaPanel(data));
    }
    detailRoot.append(renderTagsPanel(data));
    if (Array.isArray(data.revisions) && data.revisions.length) {
      detailRoot.append(renderRevisionsPanel(data.revisions));
    }
    if (Array.isArray(data.usage) && data.usage.length) {
      detailRoot.append(renderUsagePanel(data.usage));
    }
    Core.refreshIcons();
  }

  /**
   * Render detail header with review/enable/delete and edit actions.
   * @param {object} data
   */
  function renderDetailHeader(data) {
    const actions = [];
    if (data.status !== "approved") {
      actions.push(() => Ui.createButton({
        label: "批准", variant: "primary", size: "sm", icon: "check",
        onClick: () => doAction("approve"),
      }));
    }
    if (data.status !== "rejected") {
      actions.push(() => Ui.createButton({
        label: "拒绝", variant: "danger", size: "sm", icon: "ban",
        onClick: () => doAction("reject"),
      }));
    }
    if (data.status === "tombstoned") {
      actions.push(() => Ui.createButton({
        label: "恢复", variant: "outline", size: "sm", icon: "rotate-ccw",
        onClick: () => doAction("restore"),
      }));
    }
    actions.push(() => Ui.createButton({
      label: data.enabled ? "禁用" : "启用",
      variant: "ghost", size: "sm", icon: data.enabled ? "circle-minus" : "circle-check",
      onClick: () => doAction(data.enabled ? "disable" : "enable"),
    }));
    actions.push(() => Ui.createButton({
      label: "编辑", variant: "outline", size: "sm", icon: "pencil",
      onClick: () => openEditDrawer(data),
    }));
    actions.push(() => Ui.createButton({
      label: "删除", variant: "ghost", size: "sm", icon: "trash-2",
      onClick: () => {
        if (confirm("确定删除此样例？此操作不可撤销。")) doAction("delete");
      },
    }));

    return Ui.createPanel({
      title: data.title || "--",
      subtitle: `ID ${data.id} · v${data.version ?? data.revision ?? 1} · ${data.scope_label || data.scope_type || ""}`,
      icon: "message-square",
      actions,
      body: () => Ui.createDefinitionList([
        { dt: "状态", node: statusBadge(data.status) },
        { dt: "启用", dd: data.enabled ? "是" : "否" },
        { dt: "Agent", dd: data.agent_id || "--" },
        { dt: "主题", dd: data.topic || "--" },
        { dt: "意图", dd: data.intent || "--" },
        { dt: "质量分", dd: Core.formatScore(data.quality_score) },
        { dt: "来源", dd: data.source_type || "--" },
        { dt: "创建", dd: Core.formatTime(data.created_at) },
        { dt: "更新", dd: Core.formatTime(data.updated_at) },
      ], { vertical: true }),
    });
  }

  /**
   * Render the conversation turns panel.
   * @param {Array} turns
   */
  function renderTurnsPanel(turns) {
    return Ui.createPanel({
      title: `对话轮次（${turns.length}）`, icon: "messages-square",
      body: () => {
        if (!turns.length) return document.createTextNode("无对话轮次。");
        const wrap = document.createElement("div");
        wrap.className = "conversation";
        wrap.style.display = "flex";
        wrap.style.flexDirection = "column";
        wrap.style.gap = "var(--sp-2)";
        turns.forEach((turn) => {
          const item = document.createElement("div");
          item.className = `conversation-turn conversation-turn-${turn.role || "user"}`;
          const role = document.createElement("div");
          role.className = "conversation-turn-role";
          role.textContent = turn.role === "assistant" ? "助手" : "用户";
          const content = document.createElement("div");
          content.className = "conversation-turn-content";
          content.textContent = String(turn.content || "");
          item.append(role, content);
          wrap.append(item);
        });
        return wrap;
      },
    });
  }

  /**
   * Render the ideal reply panel with a trace viewer.
   * @param {string} idealReply
   */
  function renderIdealReplyPanel(idealReply) {
    return Ui.createPanel({
      title: "理想回复", icon: "sparkles",
      body: () => Ui.createTraceViewer({ content: idealReply || "" }),
    });
  }

  /**
   * Render the conditions/exclusions/notes meta panel.
   * @param {object} data
   */
  function renderMetaPanel(data) {
    return Ui.createPanel({
      title: "附加说明", icon: "file-text",
      body: () => Ui.createDefinitionList([
        data.conditions ? { dt: "适用条件", dd: data.conditions } : null,
        data.exclusions ? { dt: "排除情形", dd: data.exclusions } : null,
        data.notes ? { dt: "备注", dd: data.notes } : null,
      ].filter(Boolean), { vertical: true }),
    });
  }

  /**
   * Render the keywords and style tags panel as chips.
   * @param {object} data
   */
  function renderTagsPanel(data) {
    const keywords = Array.isArray(data.keywords) ? data.keywords : [];
    const tags = Array.isArray(data.style_tags) ? data.style_tags : [];
    return Ui.createPanel({
      title: "关键词与风格", icon: "tag",
      body: () => {
        const wrap = document.createElement("div");
        wrap.style.display = "flex";
        wrap.style.flexDirection = "column";
        wrap.style.gap = "var(--sp-2)";
        const kwRow = document.createElement("div");
        kwRow.style.display = "flex";
        kwRow.style.flexWrap = "wrap";
        kwRow.style.gap = "var(--sp-1)";
        const kwLabel = document.createElement("span");
        kwLabel.className = "field-label";
        kwLabel.textContent = "关键词";
        kwRow.append(kwLabel);
        if (keywords.length) {
          keywords.forEach((k) => kwRow.append(Ui.createChip(k)));
        } else {
          const none = document.createElement("span");
          none.textContent = "无";
          none.style.color = "var(--text-muted)";
          kwRow.append(none);
        }
        wrap.append(kwRow);
        const tagRow = document.createElement("div");
        tagRow.style.display = "flex";
        tagRow.style.flexWrap = "wrap";
        tagRow.style.gap = "var(--sp-1)";
        const tagLabel = document.createElement("span");
        tagLabel.className = "field-label";
        tagLabel.textContent = "风格标签";
        tagRow.append(tagLabel);
        if (tags.length) {
          tags.forEach((t) => tagRow.append(Ui.createChip(t)));
        } else {
          const none = document.createElement("span");
          none.textContent = "无";
          none.style.color = "var(--text-muted)";
          tagRow.append(none);
        }
        wrap.append(tagRow);
        return wrap;
      },
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
        title: `v${rev.revision ?? "?"} · ${rev.action || ""}`,
        meta: [rev.actor || "", Core.formatTime(rev.created_at), rev.reason || ""].filter(Boolean),
        body: "",
      }))),
    });
  }

  /**
   * Render the usage stats panel as a record list.
   * @param {Array} usage
   */
  function renderUsagePanel(usage) {
    return Ui.createPanel({
      title: `使用记录（${usage.length}）`, icon: "activity",
      body: () => Ui.createRecords(usage.slice(0, 20).map((u) => ({
        title: `${u.selected ? "已选用" : "候选"} · 排名 ${u.rank ?? "--"}`,
        meta: [
          Core.formatTime(u.created_at),
          u.request_id ? `请求 ${Core.truncateId(u.request_id, 8, 4)}` : "",
          u.score != null ? `得分 ${Core.formatScore(u.score)}` : "",
        ].filter(Boolean),
        body: "",
      }))),
    });
  }

  /**
   * Apply a reply example action and reload detail + list on success.
   * @param {string} action
   * @param {object} [extra]
   */
  async function doAction(action, extra) {
    if (!state.selectedId) return;
    const payload = { action, id: state.selectedId, ...(extra || {}) };
    try {
      await Api.replyExampleAction(payload);
      Ui.toastSuccess("操作已提交");
      if (action === "delete") {
        state.selectedId = null;
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "未选中样例", message: "从左侧列表选择一条样例查看详情。",
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
   * @param {object|null} data Null for create, existing example for edit.
   */
  function openEditDrawer(data) {
    closeDrawer();
    const isCreate = !data;
    const form = buildExampleForm(data);
    const drawer = Ui.createDrawer({
      title: isCreate ? "新增样例" : "编辑样例",
      icon: isCreate ? "plus" : "pencil",
      width: "600px",
      body: form.root,
      onClose: () => { activeDrawer = null; },
    });
    form.submitBtn.addEventListener("click", async () => {
      const payload = form.collect();
      if (!payload) return;
      payload.action = isCreate ? "create" : "update";
      if (!isCreate) {
        payload.id = state.selectedId;
        payload.revision = data.version ?? data.revision;
      }
      try {
        await Api.replyExampleAction(payload);
        Ui.toastSuccess(isCreate ? "样例已创建" : "样例已更新");
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
   * Build the example create/edit form.
   * @param {object|null} data
   * @returns {{root:HTMLElement, submitBtn:HTMLElement, collect:()=>object|null}}
   */
  function buildExampleForm(data) {
    const root = document.createElement("div");
    root.className = "drawer-body";
    root.style.display = "flex";
    root.style.flexDirection = "column";
    root.style.gap = "var(--sp-3)";

    const isCreate = !data;
    const titleInput = Ui.createInput({
      value: data ? data.title : "", placeholder: "样例标题",
    });
    root.append(Ui.createField({
      label: "标题", required: true, control: titleInput,
    }));

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

    const topicInput = Ui.createInput({ value: data ? data.topic : "", placeholder: "主题" });
    const intentInput = Ui.createInput({ value: data ? data.intent : "", placeholder: "意图" });
    const topicRow = document.createElement("div");
    topicRow.className = "flex gap-3 flex-wrap";
    topicRow.append(Ui.createField({ label: "主题", control: topicInput, sm: true }));
    topicRow.append(Ui.createField({ label: "意图", control: intentInput, sm: true }));
    root.append(topicRow);

    const turnsWrap = document.createElement("div");
    turnsWrap.style.display = "flex";
    turnsWrap.style.flexDirection = "column";
    turnsWrap.style.gap = "var(--sp-2)";
    const turnsLabel = document.createElement("div");
    turnsLabel.className = "field-label";
    turnsLabel.textContent = "对话轮次（1-3 轮）";
    turnsWrap.append(turnsLabel);

    /** @type {Array<{role:HTMLSelectElement, content:HTMLTextAreaElement}>} */
    const turnEntries = [];

    /**
     * Append one turn row to the form.
     * @param {{role:string, content:string}} [initial]
     */
    function appendTurn(initial) {
      const row = document.createElement("div");
      row.className = "turn-row";
      row.style.display = "flex";
      row.style.flexDirection = "column";
      row.style.gap = "var(--sp-1)";
      row.style.padding = "var(--sp-2)";
      row.style.background = "var(--pink-faint)";
      row.style.borderRadius = "var(--radius-sm)";
      const head = document.createElement("div");
      head.style.display = "flex";
      head.style.gap = "var(--sp-2)";
      head.style.alignItems = "center";
      const roleSel = Ui.createSelect({
        value: (initial && initial.role) || "user",
        sm: true,
        options: [
          { value: "user", label: "用户" },
          { value: "assistant", label: "助手" },
        ],
      });
      const delBtn = Ui.createButton({
        icon: "x", variant: "ghost", size: "sm", title: "删除此轮",
        onClick: () => {
          const idx = turnEntries.findIndex((e) => e.row === row);
          if (idx >= 0) {
            turnEntries.splice(idx, 1);
            row.remove();
          }
        },
      });
      head.append(roleSel, delBtn);
      const contentTa = Ui.createTextarea({
        value: (initial && initial.content) || "", rows: 2, sm: true,
        placeholder: "本轮内容…",
      });
      row.append(head, contentTa);
      turnsWrap.append(row);
      turnEntries.push({ row, role: roleSel, content: contentTa });
    }

    if (data && Array.isArray(data.turns) && data.turns.length) {
      data.turns.forEach((t) => appendTurn(t));
    } else {
      appendTurn({ role: "user", content: "" });
      appendTurn({ role: "assistant", content: "" });
    }
    const addTurnBtn = Ui.createButton({
      label: "新增轮次", variant: "outline", size: "sm", icon: "plus",
      onClick: () => {
        if (turnEntries.length >= 3) { Ui.toastWarning("最多 3 轮"); return; }
        appendTurn({ role: "user", content: "" });
      },
    });
    turnsWrap.append(addTurnBtn);
    root.append(turnsWrap);

    const idealTa = Ui.createTextarea({
      value: data ? data.ideal_reply : "", rows: 4, placeholder: "理想回复内容…",
    });
    root.append(Ui.createField({
      label: "理想回复", required: true, control: idealTa,
    }));

    const keywordsInput = Ui.createInput({
      value: data && Array.isArray(data.keywords) ? data.keywords.join(", ") : "",
      placeholder: "逗号分隔",
    });
    const tagsInput = Ui.createInput({
      value: data && Array.isArray(data.style_tags) ? data.style_tags.join(", ") : "",
      placeholder: "逗号分隔",
    });
    const tagRow = document.createElement("div");
    tagRow.className = "flex gap-3 flex-wrap";
    tagRow.append(Ui.createField({ label: "关键词", control: keywordsInput, sm: true }));
    tagRow.append(Ui.createField({ label: "风格标签", control: tagsInput, sm: true }));
    root.append(tagRow);

    const condTa = Ui.createTextarea({
      value: data ? data.conditions : "", rows: 2, placeholder: "适用条件…", sm: true,
    });
    const exclTa = Ui.createTextarea({
      value: data ? data.exclusions : "", rows: 2, placeholder: "排除情形…", sm: true,
    });
    const notesTa = Ui.createTextarea({
      value: data ? data.notes : "", rows: 2, placeholder: "备注…", sm: true,
    });
    root.append(Ui.createField({ label: "适用条件", control: condTa }));
    root.append(Ui.createField({ label: "排除情形", control: exclTa }));
    root.append(Ui.createField({ label: "备注", control: notesTa }));

    const scoreInput = Ui.createInput({
      type: "number", value: data ? data.quality_score : 0.8,
    });
    scoreInput.step = "0.05"; scoreInput.min = "0"; scoreInput.max = "1";
    const enabledCb = document.createElement("input");
    enabledCb.type = "checkbox";
    enabledCb.className = "checkbox";
    enabledCb.checked = data ? !!data.enabled : false;
    const enabledLabel = document.createElement("label");
    enabledLabel.className = "checkbox-group";
    enabledLabel.append(enabledCb, document.createTextNode("启用"));
    const optRow = document.createElement("div");
    optRow.className = "flex gap-3 flex-wrap";
    optRow.append(Ui.createField({ label: "质量分", control: scoreInput, sm: true }));
    optRow.append(Ui.createField({ label: "状态", control: enabledLabel, sm: true }));
    root.append(optRow);

    if (!isCreate) {
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
      const title = titleInput.value.trim();
      const ideal = idealTa.value.trim();
      if (!title) { Ui.toastWarning("请填写标题"); return null; }
      if (!ideal) { Ui.toastWarning("请填写理想回复"); return null; }
      if (isCreate && !scopeSel.value) { Ui.toastWarning("请选择作用域"); return null; }
      const selectedScope = state.scopes.find((item) => item.scope_token === scopeSel.value);
      if (isCreate && selectedScope && selectedScope.scope_type === "global"
          && !confirm("全局样例会对这个 Agent 的所有聊天生效，确定继续吗？")) {
        return null;
      }
      const turns = turnEntries.map((e) => ({
        role: e.role.value,
        content: e.content.value.trim(),
      })).filter((t) => t.content);
      if (!turns.length || turns.length > 3) {
        Ui.toastWarning("对话轮次需为 1-3 轮且内容非空");
        return null;
      }
      const payload = {
        title,
        turns,
        ideal_reply: ideal,
        quality_score: Core.numberValue(scoreInput.value, 0.8),
      };
      const keywords = keywordsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
      const tags = tagsInput.value.split(",").map((s) => s.trim()).filter(Boolean);
      if (keywords.length) payload.keywords = keywords;
      if (tags.length) payload.style_tags = tags;
      const cond = condTa.value.trim();
      const excl = exclTa.value.trim();
      const notes = notesTa.value.trim();
      if (cond) payload.conditions = cond;
      if (excl) payload.exclusions = excl;
      if (notes) payload.notes = notes;
      if (isCreate) {
        payload.scope_token = scopeSel.value;
        payload.agent_id = agentInput.value.trim() || "default";
        payload.enabled = enabledCb.checked;
      } else if (data && data.enabled !== enabledCb.checked) {
        payload.enabled = enabledCb.checked;
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
      title: "样例召回测试", icon: "search", width: "640px",
      body: form.root, footer: resultRoot,
      onClose: () => { activeDrawer = null; },
    });
    form.runBtn.addEventListener("click", async () => {
      const params = form.collect();
      if (!params) return;
      resultRoot.replaceChildren();
      resultRoot.append(Ui.createLoading("召回中…"));
      try {
        const data = await Api.debugReplyExamples(params);
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
      message: data.included ? "以下样例将被注入到上下文。" : "未召回任何样例，请调整查询或作用域。",
    }));
    if (Array.isArray(data.items) && data.items.length) {
      data.items.forEach((item) => {
        container.append(Ui.createPanel({
          title: item.title || "--",
          subtitle: `${item.scope_label || item.scope_type || ""} · 质量 ${Core.formatScore(item.quality_score)}`,
          icon: "message-square",
          body: () => Ui.createTraceViewer({ content: item.ideal_reply || item.content || "" }),
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

    const statusSel = Ui.createSelect({
      value: state.status, sm: true,
      options: [
        { value: "", label: "全部状态" },
        { value: "draft", label: "草稿" },
        { value: "approved", label: "已批准" },
        { value: "rejected", label: "已拒绝" },
        { value: "tombstoned", label: "已归档" },
      ],
      onChange: (val) => { state.status = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "状态", control: statusSel, sm: true }));

    const enabledSel = Ui.createSelect({
      value: state.enabled, sm: true,
      options: [
        { value: "", label: "全部" },
        { value: "1", label: "已启用" },
        { value: "0", label: "未启用" },
      ],
      onChange: (val) => { state.enabled = val; state.page = 1; loadList(); },
    });
    toolbar.append(Ui.createField({ label: "启用", control: enabledSel, sm: true }));

    const searchInput = Ui.createInput({
      value: state.search, placeholder: "搜索标题、主题、内容…", sm: true,
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
      icon: "plus", variant: "primary", size: "sm", title: "新增样例",
      onClick: () => openEditDrawer(null),
    }));
    toolbar.append(Ui.createButton({
      icon: "refresh-cw", variant: "ghost", size: "sm", title: "刷新",
      onClick: () => loadList(),
    }));

    return toolbar;
  }

  global.HumanizeViews.examples = {
    async mount(root, context) {
      ctx = context;
      state = freshState();
      listGuard = Core.requestIdGuard();
      detailGuard = Core.requestIdGuard();
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
      title.textContent = "回复样例";
      const sub = document.createElement("p");
      sub.className = "page-subtitle";
      sub.textContent = "1-3 轮对话样例、理想回复与召回测试";
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
        title: "未选中样例",
        message: "从左侧列表选择一条样例查看详情。",
      }));
      right.append(detailRoot);

      split.append(left, right);
      root.append(split);

      await loadOptions();
      await loadList();
    },
    unmount() {
      closeDrawer();
      ctx = null; state = null;
      listRoot = null; detailRoot = null; paginationRoot = null;
      agentSelect = null; scopeSelect = null;
      listGuard = null; detailGuard = null; optionsGuard = null;
    },
  };
})(window);
