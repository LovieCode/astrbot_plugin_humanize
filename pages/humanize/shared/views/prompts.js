/**
 * View: Prompts — 提示词模板页交互（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET prompt-templates / GET prompt-template-audit / POST prompt-templates
 * 降级：api.js 未加载时停留在静态预览（列表切换、字数统计、脏标记、芯片复制等预览交互仍可用）。
 * 安全：模板内容一律通过 textarea.value 读写；审计字段一律通过 textContent 写入。
 */
HZ.views["prompts"] = { init: function () {

  const KEY_LABEL = {
    rule: "基础规则",
    protocol: "回复协议",
    repair: "修复指令",
    memory_extraction: "记忆提取",
    reply_examples: "回复样例",
  };
  HZ.renderTopbar({
    title: "提示词模板",
    sub: "5 个全局模板 · 修改立即生效并记录审计",
    search: "",
    actions: [{ label: "全部重置", icon: "refresh", variant: "ghost" }],
  });
  HZ.initReveal();

  const $ = (sel) => document.querySelector(sel);
  const listEl = $(".pt-list-col");
  const editorName = $("#ptEditorName");
  const editorDesc = $("#ptEditorDesc");
  const diffNote = $(".pt-diff-note");
  const varsEl = $("#ptVars");
  const textarea = $("#ptTextarea");
  const dirtyEl = $("#ptDirty");
  const counter = $("#ptCharCount");
  const auditList = $("#ptAuditList");
  const auditEmpty = $("#ptAuditEmpty");
  const resetBtn = $("#ptResetBtn");
  const copyBtn = $("#ptCopyBtn");
  const saveBtn = $("#ptSaveBtn");

  /* ---------- 预览交互（api.js 缺失时依然可用） ---------- */
  let baseContent = textarea ? textarea.value : "";
  function isDirty() {
    return !!textarea && textarea.value !== baseContent;
  }
  function refreshCount() {
    if (counter && textarea) counter.textContent = textarea.value.length + " 字";
  }
  if (textarea) {
    textarea.addEventListener("input", () => {
      if (dirtyEl) dirtyEl.classList.toggle("on", isDirty());
      refreshCount();
    });
  }
  refreshCount();

  /* 模板列表切换（仅 active 高亮，真实数据由 api 版接管） */
  document.querySelectorAll(".pt-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".pt-item").forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
    });
  });

  /* 变量芯片复制（预览） */
  document.querySelectorAll(".pt-var-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const original = chip.textContent;
      chip.textContent = "已复制";
      setTimeout(() => { chip.textContent = original; }, 900);
    });
  });

  /* 共享 API 层缺失：清空 mock 模板内容，显示明确错误提示（幂等）；列表高亮/字数统计/脏标记/芯片复制等纯 UI 交互保留 */
  function renderApiUnavailable() {
    if (listEl) listEl.innerHTML = "";
    if (editorName) editorName.innerHTML = "";
    if (editorDesc) editorDesc.innerHTML = "";
    if (varsEl) varsEl.innerHTML = "";
    if (auditList) auditList.innerHTML = "";
    if (diffNote) diffNote.style.display = "none";
    if (textarea) {
      textarea.value = "";
      baseContent = "";
      refreshCount();
    }
    if (dirtyEl) dirtyEl.classList.remove("on");
    const host = document.querySelector(".pt-layout") || document.querySelector(".main");
    if (!host || host.querySelector(".errbar[data-api-unavailable]")) return;
    const bar = document.createElement("div");
    bar.className = "errbar";
    bar.dataset.apiUnavailable = "1";
    bar.innerHTML =
      '<span class="errbar-icon">' +
      (window.HZ && HZ.icon ? HZ.icon("alert", 15) : "") +
      '</span><span class="errbar-text">共享 API 层未加载，无法显示真实数据</span>';
    host.parentNode.insertBefore(bar, host);
  }

  if (!window.HZ || !HZ.api) {
    console.error("共享 API 层（shared/api.js）未加载，无法获取真实数据");
    renderApiUnavailable();
    return;
  }
  const api = HZ.api;
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));
  const confirmDlg = HZ.confirm || ((opts) => opts.onConfirm && opts.onConfirm());

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    const p = (n) => String(n).padStart(2, "0");
    return d.getMonth() + 1 + "月" + d.getDate() + "日 " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  /* ---------- 状态 ---------- */
  let items = []; // GET prompt-templates → items[]
  let currentKey = null;
  let busy = false;

  function currentItem() {
    return items.find((i) => i.key === currentKey) || null;
  }

  /* ---------- 模板列表 ---------- */
  function renderList() {
    if (!listEl) return;
    listEl.innerHTML = "";
    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "pt-item" + (item.key === currentKey ? " active" : "");
      card.dataset.key = item.key;

      const top = document.createElement("div");
      top.className = "pt-item-top";
      const name = document.createElement("span");
      name.className = "pt-item-name";
      name.textContent = item.label;
      const key = document.createElement("span");
      key.className = "pt-item-key";
      key.textContent = KEY_LABEL[item.key] || item.key;
      top.appendChild(name);
      top.appendChild(key);
      if (item.content !== item.default_content) {
        const tag = document.createElement("span");
        tag.className = "tag tag-custom";
        tag.style.marginLeft = "auto";
        tag.textContent = "已修改";
        top.appendChild(tag);
      }
      card.appendChild(top);

      const desc = document.createElement("div");
      desc.className = "pt-item-desc";
      desc.textContent = item.description || "";
      card.appendChild(desc);

      const foot = document.createElement("div");
      foot.className = "pt-item-foot";
      const vars = item.variables || [];
      if (vars.length) {
        vars.forEach((v) => {
          const chip = document.createElement("span");
          chip.className = "pt-var" + ((item.required_variables || []).includes(v) ? " required" : "");
          chip.textContent = v;
          foot.appendChild(chip);
        });
      } else {
        const note = document.createElement("span");
        note.className = "pt-var-note";
        note.textContent = "无变量";
        foot.appendChild(note);
      }
      card.appendChild(foot);
      listEl.appendChild(card);
    });
  }

  /* ---------- 编辑器 ---------- */
  function renderEditor(item) {
    if (!item) return;
    if (editorName) {
      editorName.innerHTML = "";
      editorName.appendChild(document.createTextNode(item.label));
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = KEY_LABEL[item.key] || item.key;
      editorName.appendChild(k);
      if (item.content !== item.default_content) {
        const tag = document.createElement("span");
        tag.className = "tag tag-custom";
        tag.textContent = "已修改";
        editorName.appendChild(tag);
      }
    }
    if (editorDesc) editorDesc.textContent = item.description || "";
    if (diffNote) diffNote.style.display = item.content !== item.default_content ? "" : "none";
    if (varsEl) {
      varsEl.innerHTML = "";
      const label = document.createElement("span");
      label.className = "pt-vars-label";
      label.textContent = "可用变量";
      varsEl.appendChild(label);
      const vars = item.variables || [];
      if (vars.length) {
        vars.forEach((v) => {
          const chip = document.createElement("span");
          chip.className = "pt-var-chip" + ((item.required_variables || []).includes(v) ? " required" : "");
          chip.textContent = v;
          chip.title = "点击复制";
          chip.addEventListener("click", () => copyText(v, chip));
          varsEl.appendChild(chip);
        });
      } else {
        const note = document.createElement("span");
        note.className = "pt-var-note";
        note.textContent = "无变量";
        varsEl.appendChild(note);
      }
    }
    if (textarea) textarea.value = item.content || "";
    baseContent = item.content || "";
    if (dirtyEl) dirtyEl.classList.remove("on");
    refreshCount();
  }

  function selectTemplate(key) {
    if (busy) return;
    const item = items.find((i) => i.key === key);
    if (!item) return;
    if (isDirty()) {
      confirmDlg({
        title: "放弃未保存的修改？",
        text: "切换模板将丢弃当前未保存的编辑内容。",
        danger: true,
        onConfirm: () => {
          currentKey = key;
          renderList();
          renderEditor(item);
        },
      });
      return;
    }
    currentKey = key;
    renderList();
    renderEditor(item);
  }

  /* ---------- 加载模板集 ---------- */
  async function loadTemplates() {
    try {
      const data = await api.get("prompt-templates");
      items = data.items || [];
      if (!items.length) return;
      if (!currentKey || !items.some((i) => i.key === currentKey)) {
        currentKey = items[0].key;
      }
      renderList();
      renderEditor(currentItem());
      loadAudit();
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
      renderApiUnavailable();
    }
  }

  /* ---------- 保存 ---------- */
  async function saveCurrent() {
    const item = currentItem();
    if (!item || busy) return;
    if (!isDirty()) {
      toast("没有需要保存的修改", { type: "info" });
      return;
    }
    busy = true;
    if (saveBtn) saveBtn.disabled = true;
    try {
      const data = await api.post("prompt-templates", {
        action: "update",
        key: item.key,
        content: textarea.value,
        reason: "后台更新",
      });
      const updated = data && data.item ? data.item : null;
      if (updated) {
        const idx = items.findIndex((i) => i.key === updated.key);
        if (idx >= 0) items[idx] = updated;
        baseContent = updated.content || "";
      } else {
        baseContent = textarea.value;
      }
      if (dirtyEl) dirtyEl.classList.remove("on");
      renderList();
      loadAudit();
      toast("模板已保存", { type: "success" });
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    } finally {
      busy = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  /* ---------- 恢复默认 ---------- */
  function resetCurrent() {
    const item = currentItem();
    if (!item) return;
    confirmDlg({
      title: "恢复默认模板",
      text: "将「" + item.label + "」恢复为内置默认版本，当前修改将被覆盖。",
      danger: true,
      onConfirm: async () => {
        try {
          await api.post("prompt-templates", { action: "reset", key: item.key, reason: "恢复默认模板" });
          toast("已恢复默认模板", { type: "success" });
          await loadTemplates();
        } catch (e) {
          toast(api.errorOf(e).message, { type: "error" });
        }
      },
    });
  }

  function resetAll() {
    confirmDlg({
      title: "全部恢复默认",
      text: "将全部模板恢复为内置默认版本，所有修改将被覆盖。",
      danger: true,
      onConfirm: async () => {
        try {
          await api.post("prompt-templates", { action: "reset", key: "all", reason: "全部恢复默认模板" });
          toast("已全部恢复默认模板", { type: "success" });
          await loadTemplates();
        } catch (e) {
          toast(api.errorOf(e).message, { type: "error" });
        }
      },
    });
  }

  /* ---------- 复制 ---------- */
  function fallbackCopy(text) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* ignore */ }
    ta.remove();
  }

  function copyText(text, chip) {
    if (!text) return;
    const done = () => {
      if (chip) {
        const original = chip.textContent;
        chip.textContent = "已复制";
        setTimeout(() => { chip.textContent = original; }, 900);
      }
      toast("已复制 " + text, { type: "success" });
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text);
      done();
    }
  }

  /* ---------- 审计记录（接口未落地时静默降级为「暂无审计记录」） ---------- */
  async function loadAudit() {
    try {
      const data = await api.get("prompt-template-audit", { page: 1, page_size: 20 });
      renderAudit(data);
    } catch (e) {
      if (auditList) auditList.innerHTML = "";
      if (auditEmpty) auditEmpty.style.display = "";
    }
  }

  function renderAudit(data) {
    if (!auditList || !auditEmpty) return;
    auditList.innerHTML = "";
    const rows = (data && data.items) || [];
    if (!rows.length) {
      auditEmpty.style.display = "";
      return;
    }
    auditEmpty.style.display = "none";
    rows.forEach((a) => {
      const row = document.createElement("div");
      row.className = "audit-row";
      const isReset = a.action === "reset";
      const tag = document.createElement("span");
      tag.className = "audit-tag " + (isReset ? "reset" : "update");
      tag.textContent = isReset ? "reset" : "update";
      const text = document.createElement("span");
      text.className = "audit-text";
      const actor = document.createElement("b");
      actor.textContent = a.actor || "后台管理";
      text.appendChild(actor);
      text.appendChild(document.createTextNode(isReset ? " · 重置了 " : " · 更新了 "));
      const keys = changedKeys(a);
      const target = document.createElement("b");
      target.textContent = keys.length ? keys.join(", ") : "模板";
      text.appendChild(target);
      text.appendChild(document.createTextNode(" · 原因：" + (a.reason || "-")));
      const time = document.createElement("span");
      time.className = "audit-time";
      time.textContent = fmtTime(a.created_at);
      row.appendChild(tag);
      row.appendChild(text);
      row.appendChild(time);
      auditList.appendChild(row);
    });
    const total = (data && data.total) || 0;
    if (total > rows.length) {
      const note = document.createElement("div");
      note.className = "pt-audit-more";
      note.textContent = "仅显示最近 " + rows.length + " 条审计记录";
      auditList.appendChild(note);
    }
  }

  /** 通过 before/after 快照差异推导本次变更的模板 key。 */
  function changedKeys(a) {
    const before = (a && a.before) || {};
    const after = (a && a.after) || {};
    return Object.keys(after).filter((k) => String(after[k]) !== String(before[k]));
  }

  /* ---------- 事件绑定 ---------- */
  listEl.addEventListener("click", (e) => {
    const item = e.target.closest(".pt-item");
    if (item && item.dataset.key) selectTemplate(item.dataset.key);
  });
  if (saveBtn) saveBtn.addEventListener("click", saveCurrent);
  if (resetBtn) resetBtn.addEventListener("click", resetCurrent);
  if (copyBtn) copyBtn.addEventListener("click", () => copyText(textarea ? textarea.value : "", null));
  const topActions = $(".topbar-actions");
  if (topActions) {
    topActions.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (btn && btn.textContent.includes("全部重置")) resetAll();
    });
  }

  /* ---------- 启动 ---------- */
  loadTemplates();

} };

