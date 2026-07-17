(function registerJargonsView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** 词条状态 → 徽章映射。 */
  const ENTRY_STATUS = {
    verified: { label: "已验证", variant: "success" },
    pending: { label: "待处理", variant: "warning" },
    rejected: { label: "已拒绝", variant: "danger" },
    candidate: { label: "候选", variant: "info" },
    provisional: { label: "暂定", variant: "info" },
  };

  /** 含义状态 → 徽章映射。 */
  const SENSE_STATUS = {
    verified: { label: "已验证", variant: "success" },
    candidate: { label: "候选", variant: "info" },
    provisional: { label: "暂定", variant: "warning" },
    rejected: { label: "已拒绝", variant: "danger" },
  };

  /**
   * Build a status badge from a status string using the given map.
   * @param {string} status
   * @param {object} [map]
   * @returns {HTMLElement}
   */
  function statusBadge(status, map) {
    const table = map || ENTRY_STATUS;
    const info = table[status] || { label: status || "--", variant: "" };
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
  let searchDebounce = null;

  /** Create a fresh state object. */
  function freshState() {
    return {
      page: 1, pageSize: 20,
      search: "", status: "", scopeType: "", scopeId: "",
      selectedId: null, total: 0,
    };
  }

  /**
   * Fetch and render the jargon list using current filters.
   */
  async function loadList() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    const reqId = listGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载词条列表…"));
    try {
      const data = await Api.getJargons({
        page: state.page, page_size: state.pageSize,
        search: state.search, status: state.status,
        scope_type: state.scopeType, scope_id: state.scopeId,
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
        title: "暂无词条",
        message: (state.search || state.status || state.scopeType)
          ? "未找到匹配的词条，请调整筛选条件。"
          : "尚未学习到任何黑话词条。",
      }));
      renderPagination();
      return;
    }

    const columns = [
      { key: "term", label: "词条" },
      { key: "scope", label: "作用域", width: "120px" },
      { key: "status", label: "状态", width: "90px" },
      { key: "sense_count", label: "含义", width: "60px", num: true },
      { key: "occurrence_count", label: "出现", width: "60px", num: true },
      { key: "confidence", label: "置信度", width: "80px" },
      { key: "last_seen_at", label: "最近出现", width: "130px", mono: true },
    ];

    const rows = norm.items.map((item) => ({
      key: item.id,
      cells: {
        term: item.term || "--",
        scope: { text: Core.formatScopeLabel(item.scope_type, item.scope_id) },
        status: { text: item.status, variant: (ENTRY_STATUS[item.status] || {}).variant },
        sense_count: String(item.sense_count ?? 0),
        occurrence_count: String(item.occurrence_count ?? 0),
        confidence: Core.formatScore(item.confidence),
        last_seen_at: Core.formatTime(item.last_seen_at),
      },
    }));

    listRoot.append(Ui.createTable({
      columns, rows,
      selectedKey: state.selectedId,
      onRowClick: (row) => selectEntry(row.key),
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
   * Select an entry and load its detail.
   * @param {number} id
   */
  async function selectEntry(id) {
    state.selectedId = id;
    await loadDetail(id);
  }

  /**
   * Fetch and render jargon detail.
   * @param {number} id
   */
  async function loadDetail(id) {
    if (!detailRoot || !ctx || ctx.isStale()) return;
    const reqId = detailGuard.bump();
    detailRoot.replaceChildren();
    detailRoot.append(Ui.createLoading("加载详情…"));
    try {
      const data = await Api.getJargonDetail(id);
      if (detailGuard.isStale(reqId) || ctx.isStale()) return;
      if (!data) {
        detailRoot.replaceChildren();
        detailRoot.append(Ui.createEmptyState({
          title: "词条不存在", message: "该词条可能已被删除。",
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
   * Render the full detail view.
   * @param {object} data { entry, aliases, senses, evidence }
   */
  function renderDetail(data) {
    const entry = data.entry || {};
    detailRoot.replaceChildren();
    detailRoot.append(renderDetailHeader(entry));
    detailRoot.append(renderMatchSettings(entry, data.aliases || []));
    detailRoot.append(renderSensesPanel(entry, data.senses || []));
    if (data.evidence && data.evidence.length) {
      detailRoot.append(renderEvidencePanel(data.evidence));
    }
    detailRoot.append(renderDeleteZone(entry));
    Core.refreshIcons();
  }

  /**
   * Render detail header with primary confirm/reject actions.
   * @param {object} entry
   */
  function renderDetailHeader(entry) {
    return Ui.createPanel({
      title: entry.term || "--",
      subtitle: `ID ${entry.id} · ${Core.formatScopeLabel(entry.scope_type, entry.scope_id)}`,
      icon: "book-open",
      actions: [
        () => Ui.createButton({
          label: "确认", variant: "primary", size: "sm", icon: "check",
          onClick: () => doAction("confirm"),
        }),
        () => Ui.createButton({
          label: "拒绝", variant: "danger", size: "sm", icon: "ban",
          onClick: () => doAction("reject"),
        }),
      ],
      body: () => Ui.createDefinitionList([
        { dt: "状态", node: statusBadge(entry.status) },
        { dt: "出现次数", dd: String(entry.occurrence_count ?? 0) },
        { dt: "置信度", dd: Core.formatScore(entry.confidence) },
        { dt: "最近出现", dd: Core.formatTime(entry.last_seen_at) },
        { dt: "含义数", dd: `${entry.verified_sense_count ?? 0} 验证 / ${entry.sense_count ?? 0} 总计` },
        { dt: "冲突", dd: entry.has_conflict ? "是" : "否" },
      ], { vertical: true }),
    });
  }

  /**
   * Render match settings panel (match_mode, enabled, case_sensitive, aliases).
   * @param {object} entry
   * @param {Array} aliases
   */
  function renderMatchSettings(entry, aliases) {
    const aliasesText = aliases.map((a) => a.alias).join("\n");
    let aliasesTextarea = null;
    let enabledCheckbox = null;
    let caseSensitiveCheckbox = null;
    let matchModeSelect = null;

    const body = document.createElement("div");
    body.style.display = "flex";
    body.style.flexDirection = "column";
    body.style.gap = "var(--sp-3)";

    const row = document.createElement("div");
    row.className = "flex gap-3 flex-wrap";

    matchModeSelect = Ui.createSelect({
      value: entry.match_mode || "contains",
      options: [
        { value: "contains", label: "包含匹配" },
        { value: "exact", label: "精确匹配" },
        { value: "regex", label: "正则匹配" },
        { value: "fuzzy", label: "模糊匹配" },
      ],
      sm: true,
    });
    row.append(Ui.createField({ label: "匹配模式", control: matchModeSelect }));

    enabledCheckbox = document.createElement("input");
    enabledCheckbox.type = "checkbox";
    enabledCheckbox.className = "checkbox";
    enabledCheckbox.checked = !!entry.enabled;
    const enabledLabel = document.createElement("label");
    enabledLabel.className = "checkbox-group";
    enabledLabel.append(enabledCheckbox, document.createTextNode("启用词条"));
    row.append(Ui.createField({ label: "启用", control: enabledLabel }));

    caseSensitiveCheckbox = document.createElement("input");
    caseSensitiveCheckbox.type = "checkbox";
    caseSensitiveCheckbox.className = "checkbox";
    caseSensitiveCheckbox.checked = !!entry.case_sensitive;
    const csLabel = document.createElement("label");
    csLabel.className = "checkbox-group";
    csLabel.append(caseSensitiveCheckbox, document.createTextNode("区分大小写"));
    row.append(Ui.createField({ label: "大小写", control: csLabel }));

    body.append(row);

    aliasesTextarea = Ui.createTextarea({
      value: aliasesText, rows: 3, placeholder: "每行一个别名", sm: true,
    });
    body.append(Ui.createField({
      label: "别名（每行一个）",
      control: aliasesTextarea,
      hint: `当前 ${aliases.length} 个别名`,
    }));

    body.append(Ui.createButton({
      label: "保存匹配设置",
      variant: "primary", size: "sm", icon: "save",
      onClick: async () => {
        const aliasesList = aliasesTextarea.value
          .split("\n").map((s) => s.trim()).filter(Boolean);
        await doAction("update_entry", {
          match_mode: matchModeSelect.value,
          enabled: enabledCheckbox.checked,
          case_sensitive: caseSensitiveCheckbox.checked,
        });
        await doAction("replace_aliases", { aliases: aliasesList });
      },
    }));

    return Ui.createPanel({ title: "匹配设置", icon: "list-filter", body });
  }

  /**
   * Render senses panel with SenseCard list, new sense form, and merge UI.
   * @param {object} entry
   * @param {Array} senses
   */
  function renderSensesPanel(entry, senses) {
    const body = document.createElement("div");
    body.style.display = "flex";
    body.style.flexDirection = "column";
    body.style.gap = "var(--sp-2)";

    if (!senses.length) {
      body.append(Ui.createEmptyState({
        title: "暂无含义", message: "该词条尚无含义记录。",
      }));
    } else {
      senses.forEach((sense) => body.append(renderSenseCard(sense)));
    }

    const newSenseTextarea = Ui.createTextarea({
      rows: 2, placeholder: "输入新含义…", sm: true,
    });
    newSenseTextarea.style.flex = "1";
    const newRow = document.createElement("div");
    newRow.className = "flex gap-2 flex-wrap";
    newRow.append(newSenseTextarea);
    newRow.append(Ui.createButton({
      label: "新增含义", variant: "outline", size: "sm", icon: "plus",
      onClick: async () => {
        const meaning = newSenseTextarea.value.trim();
        if (!meaning) { Ui.toastWarning("请输入含义"); return; }
        await doAction("create_sense", { meaning });
        newSenseTextarea.value = "";
      },
    }));
    body.append(newRow);

    const activeSenses = senses.filter((s) => s.status !== "rejected");
    if (activeSenses.length >= 2) {
      body.append(renderMergeUI(activeSenses));
    }

    return Ui.createPanel({ title: `含义（${senses.length}）`, icon: "list", body });
  }

  /**
   * Render a single sense card with actions.
   * @param {object} sense
   */
  function renderSenseCard(sense) {
    const card = document.createElement("div");
    card.className = "sense-card";
    if (sense.is_preferred || sense.preferred) card.dataset.preferred = "true";

    const header = document.createElement("div");
    header.className = "sense-card-header";
    const meaning = document.createElement("div");
    meaning.className = "sense-card-meaning";
    meaning.textContent = sense.meaning || "--";

    const actions = document.createElement("div");
    actions.className = "sense-card-actions";
    actions.append(Ui.createButton({
      icon: "pencil", size: "sm", variant: "ghost", title: "编辑含义",
      onClick: () => {
        const updated = prompt("编辑含义", sense.meaning || "");
        if (updated !== null && updated.trim() && updated.trim() !== sense.meaning) {
          doAction("update_sense", { sense_id: sense.id, meaning: updated.trim() });
        }
      },
    }));
    if (!sense.is_preferred && !sense.preferred) {
      actions.append(Ui.createButton({
        icon: "star", size: "sm", variant: "ghost", title: "设为首选",
        onClick: () => doAction("set_preferred_sense", { sense_id: sense.id }),
      }));
    }
    if (sense.status !== "verified") {
      actions.append(Ui.createButton({
        icon: "check", size: "sm", variant: "ghost", title: "确认含义",
        onClick: () => doAction("confirm_sense", { sense_id: sense.id }),
      }));
    }
    if (sense.status !== "rejected") {
      actions.append(Ui.createButton({
        icon: "ban", size: "sm", variant: "ghost", title: "拒绝含义",
        onClick: () => doAction("reject_sense", { sense_id: sense.id }),
      }));
    }
    actions.append(Ui.createButton({
      icon: "trash-2", size: "sm", variant: "ghost", title: "删除含义",
      onClick: () => {
        if (confirm("确定删除此含义？相关证据将保留但解除关联。")) {
          doAction("delete_sense", { sense_id: sense.id });
        }
      },
    }));
    header.append(meaning, actions);
    card.append(header);

    const meta = document.createElement("div");
    meta.className = "sense-card-meta";
    meta.append(statusBadge(sense.status, SENSE_STATUS));
    if (sense.is_preferred || sense.preferred) {
      meta.append(Ui.createBadge("首选", "pink"));
    }
    meta.append(Ui.createChip(`置信度 ${Core.formatScore(sense.confidence)}`));
    meta.append(Ui.createChip(`证据 ${sense.evidence_count ?? 0}`));
    if (sense.created_by) meta.append(Ui.createChip(`来源 ${sense.created_by}`));
    card.append(meta);
    return card;
  }

  /**
   * Render merge UI for combining two senses.
   * @param {Array} senses
   */
  function renderMergeUI(senses) {
    const wrap = document.createElement("div");
    wrap.style.padding = "var(--sp-3)";
    wrap.style.background = "var(--pink-faint)";
    wrap.style.borderRadius = "var(--r-md)";
    wrap.style.border = "1px dashed var(--pink-soft)";

    const title = document.createElement("div");
    title.style.fontWeight = "500";
    title.style.color = "var(--pink-strong)";
    title.style.marginBottom = "var(--sp-2)";
    title.textContent = "合并含义";
    wrap.append(title);

    const options = senses.map((s) => ({
      value: String(s.id),
      label: `${s.meaning}（${s.status}）`,
    }));
    const sourceSelect = Ui.createSelect({ options, sm: true });
    const targetSelect = Ui.createSelect({ options, sm: true });
    if (options.length >= 2) {
      sourceSelect.value = options[0].value;
      targetSelect.value = options[1].value;
    }

    const row = document.createElement("div");
    row.className = "flex gap-2 flex-wrap items-center";
    row.append(Ui.createField({ label: "源含义", control: sourceSelect }));
    const arrow = document.createElement("span");
    arrow.textContent = "→";
    arrow.style.color = "var(--pink)";
    arrow.style.alignSelf = "center";
    arrow.style.marginTop = "var(--sp-4)";
    row.append(arrow);
    row.append(Ui.createField({ label: "目标含义", control: targetSelect }));
    row.append(Ui.createButton({
      label: "合并", variant: "outline", size: "sm", icon: "arrow-right",
      onClick: async () => {
        const sourceId = Number(sourceSelect.value);
        const targetId = Number(targetSelect.value);
        if (!sourceId || !targetId || sourceId === targetId) {
          Ui.toastWarning("请选择不同的源和目标含义"); return;
        }
        if (!confirm("合并后源含义将被删除，证据转移到目标含义。继续？")) return;
        await doAction("merge_sense", {
          source_sense_id: sourceId, target_sense_id: targetId,
        });
      },
    }));
    wrap.append(row);
    return wrap;
  }

  /**
   * Render evidence list panel.
   * @param {Array} evidence
   */
  function renderEvidencePanel(evidence) {
    const items = evidence.map((ev) => ({
      title: ev.source_text
        ? `"${ev.source_text.slice(0, 60)}${ev.source_text.length > 60 ? "…" : ""}"`
        : "（无原文）",
      meta: [
        { text: ev.valid ? "有效" : "无效", variant: ev.valid ? "success" : "danger" },
        `发送者 ${ev.sender_id || "--"}`,
        Core.formatTime(ev.observed_at),
      ],
      body: ev.source_text || "",
    }));
    return Ui.createPanel({
      title: `证据（${evidence.length}）`, icon: "quote",
      body: () => Ui.createRecords(items),
    });
  }

  /**
   * Render the danger zone with permanent delete button.
   * @param {object} entry
   */
  function renderDeleteZone(entry) {
    const wrap = document.createElement("div");
    wrap.style.padding = "var(--sp-4)";
    wrap.style.border = "1px solid var(--danger)";
    wrap.style.borderRadius = "var(--r-lg)";
    wrap.style.background = "var(--danger-soft)";
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.justifyContent = "space-between";
    wrap.style.gap = "var(--sp-3)";
    wrap.style.flexWrap = "wrap";

    const text = document.createElement("div");
    const t = document.createElement("div");
    t.style.fontWeight = "500";
    t.style.color = "var(--danger)";
    t.textContent = "删除词条";
    const m = document.createElement("div");
    m.style.fontSize = "var(--fs-sm)";
    m.style.color = "var(--text-muted)";
    m.textContent = "删除后无法恢复，所有含义和证据将被清除。";
    text.append(t, m);
    wrap.append(text);

    wrap.append(Ui.createButton({
      label: "永久删除", variant: "danger", size: "sm", icon: "trash-2",
      onClick: async () => {
        if (!confirm(`确定永久删除词条 "${entry.term}"？此操作无法撤销。`)) return;
        try {
          await Api.jargonAction({ id: entry.id, action: "delete" });
          Ui.toastSuccess("词条已删除");
          state.selectedId = null;
          detailRoot.replaceChildren();
          detailRoot.append(Ui.createEmptyState({
            title: "未选中词条", message: "从左侧列表选择一个词条查看详情。",
          }));
          await loadList();
        } catch (err) {
          Ui.toastError((err && err.message) || "删除失败");
        }
      },
    }));
    return wrap;
  }

  /**
   * Apply a jargon action and refresh detail+list.
   * @param {string} action
   * @param {object} [extra]
   */
  async function doAction(action, extra) {
    if (!state.selectedId) { Ui.toastWarning("请先选择一个词条"); return; }
    try {
      const result = await Api.jargonAction({
        id: state.selectedId, action, meaning: "", ...(extra || {}),
      });
      Ui.toastSuccess(`操作 "${action}" 已执行`);
      if (result && result.detail) renderDetail(result.detail);
      else await loadDetail(state.selectedId);
      await loadList();
    } catch (err) {
      Ui.toastError((err && err.message) || `操作 "${action}" 失败`);
    }
  }

  /** Export current filtered jargons to clipboard. */
  async function exportData() {
    try {
      const data = await Api.exportJargons({
        search: state.search, status: state.status,
        scope_type: state.scopeType, scope_id: state.scopeId,
      });
      const json = JSON.stringify(data, null, 2);
      const ok = await Core.copyText(json);
      Ui.toastSuccess(ok ? "已复制到剪贴板" : "导出完成");
    } catch (err) {
      Ui.toastError((err && err.message) || "导出失败");
    }
  }

  /**
   * Render the toolbar with search, filters, and export.
   * @returns {HTMLElement}
   */
  function renderToolbar() {
    const toolbar = document.createElement("div");
    toolbar.className = "toolbar";

    const searchInput = Ui.createInput({
      placeholder: "搜索词条…", value: state.search, sm: true,
      onInput: (val) => {
        state.search = val;
        state.page = 1;
        searchDebounce(() => loadList());
      },
    });
    searchInput.style.minWidth = "180px";
    toolbar.append(searchInput);

    toolbar.append(Ui.createSelect({
      value: state.status,
      options: [
        { value: "", label: "全部状态" },
        { value: "verified", label: "已验证" },
        { value: "pending", label: "待处理" },
        { value: "candidate", label: "候选" },
        { value: "provisional", label: "暂定" },
        { value: "rejected", label: "已拒绝" },
      ],
      sm: true,
      onChange: (val) => { state.status = val; state.page = 1; loadList(); },
    }));

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

    const spacer = document.createElement("div");
    spacer.className = "toolbar-spacer";
    toolbar.append(spacer);

    toolbar.append(Ui.createButton({
      label: "导出", variant: "ghost", size: "sm", icon: "download",
      onClick: exportData,
    }));

    return toolbar;
  }

  global.HumanizeViews.jargons = {
    async mount(root, context) {
      ctx = context;
      state = freshState();
      listGuard = Core.requestIdGuard();
      detailGuard = Core.requestIdGuard();
      searchDebounce = Core.debounce((fn) => fn(), 300);

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
      title.textContent = "黑话词库";
      const sub = document.createElement("p");
      sub.className = "page-subtitle";
      sub.textContent = "管理词条、含义、别名与合并";
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
        title: "未选中词条", message: "从左侧列表选择一个词条查看详情。",
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
      if (searchDebounce && searchDebounce.cancel) searchDebounce.cancel();
      searchDebounce = null;
    },
  };
})(window);
