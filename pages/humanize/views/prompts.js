(function registerPromptsView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** View state (reset on each mount). */
  let state = null;
  let ctx = null;
  let loadGuard = null;
  let saveGuard = null;
  let listRoot = null;
  let detailRoot = null;
  let editorEl = null;
  let dirty = false;

  /** Create a fresh state object. */
  function freshState() {
    return {
      items: [],
      templates: {},
      updatedAt: "",
      selectedKey: null,
    };
  }

  /**
   * Fetch all prompt templates and render the list + detail.
   */
  async function loadTemplates() {
    if (!listRoot || !ctx || ctx.isStale()) return;
    const reqId = loadGuard.bump();
    listRoot.replaceChildren();
    listRoot.append(Ui.createLoading("加载模板列表…"));
    try {
      const data = await Api.getPromptTemplates();
      if (loadGuard.isStale(reqId) || ctx.isStale()) return;
      state.items = Array.isArray(data && data.items) ? data.items : [];
      state.templates = (data && data.templates) || {};
      state.updatedAt = (data && data.updatedAt) || "";
      if (!state.selectedKey && state.items.length) {
        state.selectedKey = state.items[0].key;
      }
      renderList();
      renderDetail();
    } catch (err) {
      if (loadGuard.isStale(reqId) || ctx.isStale()) return;
      listRoot.replaceChildren();
      listRoot.append(Ui.createAlert({
        variant: "danger",
        title: "加载失败",
        message: (err && err.message) || String(err),
      }));
      detailRoot.replaceChildren();
    }
  }

  /**
   * Render the template list as clickable cards.
   */
  function renderList() {
    listRoot.replaceChildren();
    if (!state.items.length) {
      listRoot.append(Ui.createEmptyState({
        title: "暂无模板",
        message: "未读取到任何提示词模板。",
      }));
      return;
    }
    const nav = document.createElement("div");
    nav.className = "prompt-nav";
    nav.style.display = "flex";
    nav.style.flexDirection = "column";
    nav.style.gap = "var(--sp-1)";
    state.items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "prompt-nav-item";
      btn.dataset.key = item.key;
      if (item.key === state.selectedKey) {
        btn.setAttribute("aria-current", "true");
      }
      btn.style.display = "flex";
      btn.style.flexDirection = "column";
      btn.style.gap = "var(--sp-1)";
      btn.style.textAlign = "left";
      const label = document.createElement("span");
      label.className = "prompt-nav-label";
      label.style.fontWeight = "600";
      label.textContent = item.label || item.key;
      const desc = document.createElement("span");
      desc.className = "prompt-nav-desc";
      desc.style.fontSize = "var(--fs-sm)";
      desc.style.color = "var(--text-muted)";
      desc.textContent = item.description || "";
      btn.append(label, desc);
      btn.addEventListener("click", () => selectTemplate(item.key));
      nav.append(btn);
    });
    listRoot.append(nav);
  }

  /**
   * Select a template by key, discarding unsaved edits with confirmation.
   * @param {string} key
   */
  function selectTemplate(key) {
    if (dirty && !confirm("当前有未保存的修改，切换模板将丢弃修改。是否继续？")) {
      return;
    }
    state.selectedKey = key;
    dirty = false;
    renderList();
    renderDetail();
  }

  /**
   * Find the currently selected template item.
   * @returns {object|null}
   */
  function currentItem() {
    return state.items.find((it) => it.key === state.selectedKey) || null;
  }

  /**
   * Render the detail/editor pane for the selected template.
   */
  function renderDetail() {
    detailRoot.replaceChildren();
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";
    detailRoot.style.gap = "var(--sp-4)";

    const item = currentItem();
    if (!item) {
      detailRoot.append(Ui.createEmptyState({
        title: "未选择模板",
        message: "从左侧选择一个模板进行编辑。",
      }));
      return;
    }

    detailRoot.append(renderHeaderPanel(item));
    detailRoot.append(renderVariablesPanel(item));
    detailRoot.append(renderEditorPanel(item));
    Core.refreshIcons();
  }

  /**
   * Render the header panel with title, description and action buttons.
   * @param {object} item
   * @returns {HTMLElement}
   */
  function renderHeaderPanel(item) {
    const actions = [
      () => Ui.createButton({
        label: "保存", variant: "primary", size: "sm", icon: "save",
        onClick: () => saveTemplate(),
      }),
      () => Ui.createButton({
        label: "撤销修改", variant: "ghost", size: "sm", icon: "undo-2",
        onClick: () => {
          if (!editorEl) return;
          editorEl.value = item.content || "";
          dirty = false;
        },
      }),
      () => Ui.createButton({
        label: "恢复默认", variant: "outline", size: "sm", icon: "rotate-ccw",
        onClick: () => resetTemplate(item.key),
      }),
    ];
    return Ui.createPanel({
      title: item.label || item.key,
      subtitle: `更新于 ${Core.formatTime(item.updated_at || state.updatedAt)}`,
      icon: "file-text",
      actions,
      body: () => {
        const p = document.createElement("p");
        p.className = "prompt-description";
        p.style.margin = "0";
        p.style.color = "var(--text-muted)";
        p.textContent = item.description || "";
        return p;
      },
    });
  }

  /**
   * Render variables and required variables as chips.
   * @param {object} item
   * @returns {HTMLElement}
   */
  function renderVariablesPanel(item) {
    const variables = Array.isArray(item.variables) ? item.variables : [];
    const required = Array.isArray(item.required_variables) ? item.required_variables : [];
    return Ui.createPanel({
      title: "模板变量", icon: "braces",
      body: () => {
        const wrap = document.createElement("div");
        wrap.style.display = "flex";
        wrap.style.flexDirection = "column";
        wrap.style.gap = "var(--sp-2)";
        if (!variables.length) {
          const note = document.createElement("p");
          note.style.margin = "0";
          note.style.color = "var(--text-muted)";
          note.textContent = "该模板不使用任何变量。";
          wrap.append(note);
          return wrap;
        }
        const chips = document.createElement("div");
        chips.style.display = "flex";
        chips.style.flexWrap = "wrap";
        chips.style.gap = "var(--sp-2)";
        variables.forEach((v) => {
          const isRequired = required.includes(v);
          const chip = Ui.createChip(
            `${v}${isRequired ? " （必填）" : ""}`,
            isRequired ? "star" : null
          );
          chips.append(chip);
        });
        wrap.append(chips);
        return wrap;
      },
    });
  }

  /**
   * Render the editor textarea with the current template content.
   * @param {object} item
   * @returns {HTMLElement}
   */
  function renderEditorPanel(item) {
    const panel = Ui.createPanel({
      title: "模板内容", icon: "scroll-text",
      body: () => {
        editorEl = Ui.createTextarea({
          value: item.content || "",
          rows: 14,
          mono: true,
          onInput: (value) => { dirty = (value !== (item.content || "")); },
        });
        editorEl.style.fontFamily = "var(--font-mono, monospace)";
        editorEl.style.fontSize = "var(--fs-sm)";
        editorEl.style.lineHeight = "1.6";
        return editorEl;
      },
    });
    return panel;
  }

  /**
   * Save the current editor content to the backend.
   */
  async function saveTemplate() {
    if (!ctx || ctx.isStale()) return;
    const item = currentItem();
    if (!item || !editorEl) return;
    const content = editorEl.value;
    if (!content.trim()) {
      ctx.toastError("模板内容不能为空");
      return;
    }
    const reqId = saveGuard.bump();
    ctx.toastInfo("正在保存…");
    try {
      const result = await Api.savePromptTemplate({
        action: "update",
        key: item.key,
        content,
        reason: "web edit",
      });
      if (saveGuard.isStale(reqId) || ctx.isStale()) return;
      // Update local state from response.
      if (result && Array.isArray(result.items)) {
        state.items = result.items;
        state.templates = result.templates || state.templates;
        state.updatedAt = result.updated_at || state.updatedAt;
      }
      dirty = false;
      renderList();
      renderDetail();
      ctx.toastSuccess("模板已保存");
    } catch (err) {
      if (saveGuard.isStale(reqId) || ctx.isStale()) return;
      ctx.toastError((err && err.message) || "保存失败");
    }
  }

  /**
   * Reset a single template (or all) to its default content.
   * @param {string} key Template key, or "all" to reset every template.
   */
  async function resetTemplate(key) {
    if (!ctx || ctx.isStale()) return;
    const message = key === "all"
      ? "确定将所有提示词模板重置为默认值？此操作不可撤销。"
      : `确定将模板 "${key}" 重置为默认值？此操作不可撤销。`;
    if (!confirm(message)) return;
    const reqId = saveGuard.bump();
    ctx.toastInfo("正在重置…");
    try {
      const result = await Api.savePromptTemplate({
        action: "reset",
        key,
        reason: "web reset",
      });
      if (saveGuard.isStale(reqId) || ctx.isStale()) return;
      if (result && Array.isArray(result.items)) {
        state.items = result.items;
        state.templates = result.templates || state.templates;
        state.updatedAt = result.updated_at || state.updatedAt;
      }
      dirty = false;
      renderList();
      renderDetail();
      ctx.toastSuccess(key === "all" ? "全部模板已重置" : "模板已重置为默认");
    } catch (err) {
      if (saveGuard.isStale(reqId) || ctx.isStale()) return;
      ctx.toastError((err && err.message) || "重置失败");
    }
  }

  /**
   * Mount the prompts view: install topbar actions and load templates.
   * @param {HTMLElement} root
   * @param {object} viewCtx
   */
  async function mount(root, viewCtx) {
    ctx = viewCtx;
    state = freshState();
    loadGuard = Core.requestIdGuard();
    saveGuard = Core.requestIdGuard();
    dirty = false;
    root.replaceChildren();

    const split = document.createElement("div");
    split.className = "split-view";
    const left = document.createElement("div");
    left.className = "split-view-left";
    left.style.display = "flex";
    left.style.flexDirection = "column";
    left.style.gap = "var(--sp-2)";
    listRoot = document.createElement("div");
    left.append(listRoot);

    const right = document.createElement("div");
    right.className = "split-view-right";
    detailRoot = document.createElement("div");
    detailRoot.style.display = "flex";
    detailRoot.style.flexDirection = "column";
    detailRoot.style.gap = "var(--sp-4)";
    detailRoot.append(Ui.createEmptyState({
      title: "未选择模板",
      message: "从左侧选择一个模板进行编辑。",
    }));
    right.append(detailRoot);

    split.append(left, right);
    root.append(split);

    ctx.setTopbarActions([
      Ui.createButton({
        label: "重置全部", variant: "ghost", size: "sm", icon: "undo-2",
        onClick: () => resetTemplate("all"),
      }),
      Ui.createButton({
        label: "刷新", variant: "outline", size: "sm", icon: "refresh-cw",
        onClick: () => loadTemplates(),
      }),
    ]);

    await loadTemplates();
  }

  /** Reset view-scoped state on unmount. */
  function unmount() {
    state = null;
    ctx = null;
    loadGuard = null;
    saveGuard = null;
    listRoot = null;
    detailRoot = null;
    editorEl = null;
    dirty = false;
  }

  global.HumanizeViews.prompts = { mount, unmount };
})(window);
