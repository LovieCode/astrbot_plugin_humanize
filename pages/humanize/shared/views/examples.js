/**
 * View: Examples — 回复样例页交互（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET reply-examples / GET reply-example-detail / GET memory-overview / GET memory-agent-options
 *       POST reply-example-action / POST reply-example-recall-debug
 * 降级：api.js 未加载时清空 mock 内容并显示明确错误提示。
 * 安全：所有持久化内容（turns content/ideal_reply/conditions/usage reason/召回 XML 等）
 *       一律通过 textContent 写入，禁止拼入 innerHTML。
 */
(function () {
  HZ.renderSidebar("examples");
  HZ.renderTopbar({
    title: "回复样例",
    sub: "审核通过后作为 few-shot 表达参考 · 绝不直接返回旧样例回复",
    search: "搜索标题、话题、关键词…",
    actions: [{ label: "新建样例", icon: "plus", variant: "primary" }],
  });
  HZ.initReveal();

  /** api.js 缺失时的明确降级：清空 mock 数据容器并插入错误提示条。 */
  function renderApiUnavailable() {
    ["exList", "exPager"].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.innerHTML = "";
    });
    if (document.querySelector(".errbar[data-api-unavailable]")) return;
    const bar = document.createElement("div");
    bar.className = "errbar";
    bar.dataset.apiUnavailable = "1";
    bar.innerHTML =
      '<span class="errbar-icon">' +
      (window.HZ && HZ.icon ? HZ.icon("alert", 15) : "") +
      '</span><span class="errbar-text">共享 API 层未加载，无法显示真实数据</span>';
    const anchor = document.querySelector(".ex-list") || document.querySelector(".main");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(bar, anchor);
  }

  if (!window.HZ || !HZ.api) {
    console.error("共享 API 层（shared/api.js）未加载，无法获取真实数据");
    renderApiUnavailable();
    return;
  }
  const api = HZ.api;

  /* ---------- 共享 UI（本地兜底，避免直接抛错） ---------- */
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));
  const confirmDlg = HZ.confirm || ((o) => o.onConfirm && o.onConfirm());
  const initEmpty = HZ.initEmpty;
  const initErrbar = HZ.initErrbar;
  const fmtTime = (iso) => (api.time ? api.time(iso) : String(iso || ""));
  const fmtAgo = (iso) => (api.ago ? api.ago(iso) : String(iso || ""));
  const scopeFilter = api.scopeFilter || ((o) => ({ scope_type: o.scopeType || undefined, scope_token: o.scopeToken || undefined }));
  const scopeLabelOf = api.scopeLabel || ((type) => SCOPE_TYPE_LABEL[type] || type || "");

  /* ---------- 常量 ---------- */
  const PAGE_SIZE = 10;
  const SCOPE_TYPE_LABEL = { global: "全局", group: "群聊", private_user: "私聊", group_member: "群成员" };
  const SOURCE_LABEL = { manual: "人工整理", extracted: "从对话提取", learned: "自动学习" };
  const STATUS_CLASS = { approved: "tag-approved", draft: "tag-draft", rejected: "tag-rejected3", tombstoned: "tag-tombstoned" };
  const ACT_LABEL = { approve: "审核通过", reject: "拒绝", disable: "停用", enable: "启用", restore: "恢复" };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const listEl = $("#exList");
  const pagerEl = $("#exPager");
  const drawer = $("#drawer");
  const mask = $("#drawerMask");
  const drawerBody = $("#drawerBody");
  const footEl = $("#drawerFoot");

  /* ---------- 状态 ---------- */
  let current = { page: 1, status: "", search: "", topic: "", intent: "", scopeIndex: -1, agentIndex: -1 };
  let detail = null; // 当前抽屉样例
  let busy = false;
  const scopeOptions = []; // {scope_type, scope_token, scope_label}
  const agentOptions = []; // {agent_id, name}

  /* ---------- DOM 小工具 ---------- */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  /** 把 [data-icon] 占位替换为共享图标。 */
  function injectIcons(root) {
    root.querySelectorAll("[data-icon]").forEach((n) => {
      const svg = HZ.icon(n.dataset.icon);
      const afterbegin =
        (n.tagName === "BUTTON" && n.textContent.trim()) ||
        n.classList.contains("m") ||
        n.classList.contains("d-label") ||
        n.classList.contains("ideal");
      if (afterbegin) n.insertAdjacentHTML("afterbegin", svg);
      else n.innerHTML = svg;
    });
  }
  /** quality_score(0-1) → 百分比整数（0-100）。 */
  function pct(v) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n * 100))) : 0;
  }
  function scopeText(item) {
    return item.scope_label || scopeLabelOf(item.scope_type, item.scope_hash) || "未分类";
  }
  function statusTagEl(status) {
    const tag = el("span", "tag " + (STATUS_CLASS[status] || "tag-draft"));
    tag.appendChild(el("span", "tag-dot"));
    tag.appendChild(document.createTextNode(status || "unknown"));
    return tag;
  }
  function section(iconName, label, content) {
    const box = el("div");
    const labelEl = el("div", "d-label", label);
    labelEl.dataset.icon = iconName;
    box.appendChild(labelEl);
    box.appendChild(content);
    return box;
  }
  function field(labelText, inputNode) {
    const wrap = el("label", "ex-field");
    wrap.appendChild(el("span", "ex-field-label", labelText));
    wrap.appendChild(inputNode);
    return wrap;
  }
  function textInput(placeholder, value, id) {
    const i = el("input", "ex-input");
    i.type = "text";
    if (id) i.id = id;
    i.placeholder = placeholder || "";
    i.value = value == null ? "" : String(value);
    return i;
  }
  function textArea(placeholder, value, id, rows) {
    const t = el("textarea", "ex-input");
    if (id) t.id = id;
    t.placeholder = placeholder || "";
    t.rows = rows || 2;
    t.value = value == null ? "" : String(value);
    return t;
  }
  function valOf(id) {
    const node = document.getElementById(id);
    return node ? node.value.trim() : "";
  }
  function splitList(s) {
    return s.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
  }
  function optionsSelect(id, list, labelFn, emptyLabel) {
    const s = el("select", "ex-input");
    s.id = id;
    const all = el("option", null, emptyLabel);
    all.value = "-1";
    s.appendChild(all);
    list.forEach((o, i) => {
      const op = el("option", null, labelFn(o));
      op.value = String(i);
      s.appendChild(op);
    });
    s.value = "-1";
    return s;
  }

  /* ---------- 列表 ---------- */
  async function loadList() {
    if (busy) return;
    busy = true;
    try {
      const scope = current.scopeIndex >= 0 ? scopeOptions[current.scopeIndex] : null;
      const agent = current.agentIndex >= 0 ? agentOptions[current.agentIndex] : null;
      const data = await api.get("reply-examples", {
        ...api.pageParams({ page: current.page, pageSize: PAGE_SIZE }),
        search: current.search || undefined,
        status: current.status || undefined,
        ...scopeFilter({
          scopeType: scope ? scope.scope_type : undefined,
          scopeToken: scope && scope.scope_token ? scope.scope_token : undefined,
        }),
        agent_id: agent ? agent.agent_id : undefined,
        topic: current.topic || undefined,
        intent: current.intent || undefined,
      });
      renderList(data);
      renderPager(data);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      if (initErrbar) initErrbar({ message: err.message });
      renderApiUnavailable();
    } finally {
      busy = false;
    }
  }

  function renderList(data) {
    const items = data.items || [];
    listEl.innerHTML = "";
    if (!items.length) {
      if (initEmpty) {
        listEl.appendChild(initEmpty({ text: current.search ? "没有匹配的样例" : "还没有回复样例" }));
      } else {
        listEl.appendChild(el("div", "ex-empty", "没有符合条件的回复样例"));
      }
      return;
    }
    items.forEach((item) => listEl.appendChild(cardEl(item)));
  }

  function cardEl(item) {
    const card = el("div", "ex-card");
    card.dataset.id = item.id;
    card.dataset.status = item.status || "";
    card.dataset.enabled = item.enabled ? "1" : "0";
    if (item.status === "tombstoned" || item.status === "rejected" || !item.enabled) card.classList.add("dimmed");

    /* 顶部行：标题 + 话题/意图 + 状态 + 来源 + 操作 */
    const top = el("div", "ex-top");
    top.appendChild(el("span", "ex-title", item.title || "（无标题）"));
    if (item.topic) top.appendChild(el("span", "ex-topic", item.topic));
    if (item.intent) top.appendChild(el("span", "ex-intent", item.intent));
    top.appendChild(statusTagEl(item.status));
    if (!item.enabled) top.appendChild(el("span", "tag tag-disabled2", "已停用"));
    if (item.source_type) top.appendChild(el("span", "tag tag-source", SOURCE_LABEL[item.source_type] || item.source_type));

    const actions = el("div", "ex-actions");
    if (!item.enabled || item.status === "rejected" || item.status === "tombstoned") actions.style.opacity = "1";
    const editBtn = el("button", "icon-btn");
    editBtn.dataset.act = "edit";
    editBtn.dataset.icon = "edit";
    editBtn.title = "编辑";
    const toggleBtn = el("button", "icon-btn");
    toggleBtn.dataset.act = "toggle";
    toggleBtn.dataset.icon = item.enabled ? "zap_off" : "refresh";
    toggleBtn.title = item.enabled ? "停用" : "启用";
    actions.appendChild(editBtn);
    actions.appendChild(toggleBtn);
    top.appendChild(actions);
    card.appendChild(top);

    /* turns 气泡：user 左灰 / assistant 右粉（与静态预览一致） */
    const conv = el("div", "conv");
    (item.turns || []).slice(0, 4).forEach((t) => {
      const row = el("div", "conv-row " + (t.role === "user" ? "user" : "assistant"));
      row.appendChild(el("div", "conv-bubble", t.content || ""));
      conv.appendChild(row);
    });
    card.appendChild(conv);

    /* 理想回复 */
    if (item.ideal_reply) {
      const ideal = el("div", "ideal");
      ideal.dataset.icon = "spark";
      const span = el("span");
      span.appendChild(el("b", null, "理想回复："));
      span.appendChild(document.createTextNode(item.ideal_reply));
      ideal.appendChild(span);
      card.appendChild(ideal);
    }

    /* 元信息行 */
    const meta = el("div", "ex-meta");
    const scopeM = el("span", "m", scopeText(item));
    scopeM.dataset.icon = "pin";
    meta.appendChild(scopeM);
    if (item.agent_id) {
      const agentM = el("span", "m", item.agent_id);
      agentM.dataset.icon = "users";
      meta.appendChild(agentM);
    }
    const tags = el("div", "ex-tags");
    (item.style_tags || []).forEach((t) => tags.appendChild(el("span", "ex-tag", t)));
    if (tags.children.length) meta.appendChild(tags);
    if (item.updated_at) {
      const timeM = el("span", "m", "更新于 " + fmtAgo(item.updated_at));
      timeM.dataset.icon = "clock";
      meta.appendChild(timeM);
    }
    const q = el("div", "q-score");
    const bar = el("div", "q-bar");
    const i = el("i");
    i.style.width = pct(item.quality_score) + "%";
    bar.appendChild(i);
    q.appendChild(bar);
    q.appendChild(el("span", "q-num", pct(item.quality_score) + "%"));
    meta.appendChild(q);
    card.appendChild(meta);
    injectIcons(card);
    return card;
  }

  function renderPager(data) {
    const total = data.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    let html =
      `<button class="pg-btn" data-page="prev"${current.page <= 1 ? " disabled" : ""}>` +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>';
    for (let p = 1; p <= totalPages; p++) {
      html += `<button class="pg-btn${p === current.page ? " active" : ""}" data-page="${p}">${p}</button>`;
    }
    html +=
      `<button class="pg-btn" data-page="next"${current.page >= totalPages ? " disabled" : ""}>` +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg></button>';
    pagerEl.innerHTML = html;
    pagerEl.style.display = totalPages <= 1 ? "none" : "";
  }

  /* ---------- 筛选选项（scope / agent 下拉共用数据源） ---------- */
  async function loadOptions() {
    try {
      const [ov, ag] = await Promise.all([
        api.get("memory-overview"),
        api.get("memory-agent-options"),
      ]);
      (ov.scope_options || []).forEach((o) => {
        scopeOptions.push({
          scope_type: o.scope_type,
          scope_token: o.scope_token || "",
          scope_label: o.scope_label || (SCOPE_TYPE_LABEL[o.scope_type] || o.scope_type || "未命名作用域"),
        });
      });
      const agents = Array.isArray(ag) ? ag : (ag.items || ag.agents || []);
      agents.forEach((a) => {
        agentOptions.push({ agent_id: a.agent_id ?? a.id, name: a.name || a.agent_id || a.id });
      });
      fillSelects();
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  function fillSelects() {
    const fill = (selId, list, labelFn, emptyLabel) => {
      const sel = $(selId);
      if (!sel) return;
      sel.innerHTML = "";
      const all = el("option");
      all.value = "-1";
      all.textContent = emptyLabel;
      sel.appendChild(all);
      list.forEach((o, i) => {
        const op = el("option");
        op.value = String(i);
        op.textContent = labelFn(o);
        sel.appendChild(op);
      });
      sel.value = "-1";
    };
    fill("#exScopeSel", scopeOptions, (o) => o.scope_label, "全部作用域");
    fill("#exAgentSel", agentOptions, (o) => o.name, "全部 Agent");
    fill("#recallScope", scopeOptions, (o) => o.scope_label, "选择作用域（必填）");
    fill("#recallAgent", agentOptions, (o) => o.name, "选择 Agent（必填）");
  }

  /* ---------- 详情抽屉 ---------- */
  function openDrawer(id) {
    $$(".ex-card").forEach((c) => c.classList.toggle("selected", c.dataset.id === String(id)));
    drawer.classList.add("open");
    mask.classList.add("open");
    loadDetail(id);
  }

  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    $$(".ex-card").forEach((c) => c.classList.remove("selected"));
    detail = null;
  }

  async function loadDetail(id) {
    try {
      const data = await api.get("reply-example-detail", { id });
      renderDetail(data);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      drawerBody.innerHTML = "";
      drawerBody.appendChild(el("div", "ex-empty", "详情加载失败：" + err.message));
      $("#drawerTitle").textContent = "回复样例详情";
      $("#drawerStatus").innerHTML = "";
    }
  }

  function renderDetail(data) {
    const item = data.item || data;
    detail = item;
    drawerBody.innerHTML = "";

    /* 头部：标题 + 状态 */
    $("#drawerTitle").textContent = item.title || "回复样例";
    const st = $("#drawerStatus");
    st.innerHTML = "";
    st.appendChild(statusTagEl(item.status));
    if (!item.enabled) st.appendChild(el("span", "tag tag-disabled2", "已停用"));

    /* 归属与分类 */
    const chips = el("div", "d-chips");
    chips.appendChild(el("span", "tag tag-lg tag-scope", scopeText(item)));
    if (item.agent_id) chips.appendChild(el("span", "tag tag-lg tag-alias", "Agent · " + item.agent_id));
    if (item.topic) chips.appendChild(el("span", "tag tag-lg tag-pink", item.topic));
    if (item.intent) chips.appendChild(el("span", "tag tag-lg tag-pink", item.intent));
    if (item.source_type) chips.appendChild(el("span", "tag tag-lg tag-source", SOURCE_LABEL[item.source_type] || item.source_type));
    drawerBody.appendChild(section("pin", "归属与分类", chips));

    /* 完整 turns */
    const conv = el("div", "conv");
    (item.turns || []).forEach((t) => {
      const who = el("div", "conv-who", t.role || "-");
      if (t.role === "assistant") who.style.textAlign = "right";
      conv.appendChild(who);
      const row = el("div", "conv-row " + (t.role === "user" ? "user" : "assistant"));
      row.appendChild(el("div", "conv-bubble", t.content || ""));
      conv.appendChild(row);
    });
    drawerBody.appendChild(section("chat", "对话样例（turns）", conv));

    /* 理想回复 */
    if (item.ideal_reply) {
      drawerBody.appendChild(section("spark", "理想回复要点", el("div", "d-note", item.ideal_reply)));
    }

    /* 适用 / 排除条件 */
    if (item.conditions || item.exclusions) {
      const box = el("div");
      if (item.conditions) {
        const n = el("div", "d-note");
        n.style.marginBottom = "8px";
        n.appendChild(el("b", null, "适用："));
        n.appendChild(document.createTextNode(item.conditions));
        box.appendChild(n);
      }
      if (item.exclusions) {
        const n = el("div", "d-note warn");
        n.appendChild(el("b", null, "排除："));
        n.appendChild(document.createTextNode(item.exclusions));
        box.appendChild(n);
      }
      drawerBody.appendChild(section("check", "适用条件 / 排除条件", box));
    }

    /* 风格标签 + 关键词 */
    const tags = el("div", "d-chips");
    (item.style_tags || []).forEach((t) => tags.appendChild(el("span", "ex-tag", t)));
    (item.keywords || []).forEach((k) => tags.appendChild(el("span", "ex-tag dashed", k)));
    if (tags.children.length) drawerBody.appendChild(section("file", "风格标签 / 关键词", tags));

    /* 备注 */
    if (item.notes) drawerBody.appendChild(section("info", "备注", el("div", "d-note", item.notes)));

    /* 质量与版本 */
    const qBox = el("div");
    const qRow = el("div", "ex-q-row");
    qRow.appendChild(el("span", null, "quality " + pct(item.quality_score) + "%"));
    const bar = el("div", "bar");
    const barI = el("i");
    barI.style.width = pct(item.quality_score) + "%";
    bar.appendChild(barI);
    qRow.appendChild(bar);
    qBox.appendChild(qRow);
    const metaLine = el("div", "ex-q-meta");
    if (item.revision != null || item.version != null) {
      metaLine.appendChild(el("span", "mono", "r" + (item.revision ?? item.version)));
    }
    if (item.content_hash) metaLine.appendChild(el("span", "mono", item.content_hash.slice(0, 12)));
    if (item.source_context_run_id) metaLine.appendChild(el("span", "mono", "run " + item.source_context_run_id));
    if (item.created_at) metaLine.appendChild(el("span", null, "创建 " + fmtTime(item.created_at)));
    if (item.updated_at) metaLine.appendChild(el("span", null, "更新 " + fmtTime(item.updated_at)));
    qBox.appendChild(metaLine);
    drawerBody.appendChild(section("spark", "质量与版本", qBox));

    /* 召回使用记录 */
    const usage = data.usage || [];
    if (usage.length) {
      const box = el("div");
      usage.slice(0, 10).forEach((u) => box.appendChild(useItemEl(u)));
      drawerBody.appendChild(section("eye", "召回使用记录", box));
    }

    /* 修订历史 */
    const revisions = data.revisions || [];
    if (revisions.length) {
      const box = el("div");
      revisions.slice(0, 10).forEach((r, i) => {
        const row = el("div", "rev-row");
        const tag = el("span", "rev-tag" + (i === 0 ? " now" : ""), "r" + (r.revision ?? ""));
        row.appendChild(tag);
        row.appendChild(el("span", "rev-text", [r.action, r.actor, r.reason].filter(Boolean).join(" · ")));
        if (r.created_at) row.appendChild(el("span", "rev-time", fmtAgo(r.created_at)));
        box.appendChild(row);
      });
      drawerBody.appendChild(section("history", "修订历史", box));
    }

    /* 审计记录（before/after 折叠 JSON） */
    const audit = data.audit || [];
    if (audit.length) {
      const box = el("div");
      audit.slice(0, 10).forEach((a) => {
        const det = el("details", "audit-row");
        const sum = el("summary");
        sum.appendChild(el("span", "audit-act", a.action || "-"));
        sum.appendChild(
          el("span", "audit-meta", [a.actor, a.reason || "无理由", a.created_at ? fmtTime(a.created_at) : ""].filter(Boolean).join(" · "))
        );
        det.appendChild(sum);
        if (a.before != null || a.after != null) {
          const pre = el("pre", "ex-code audit-json");
          pre.textContent = JSON.stringify({ before: a.before, after: a.after }, null, 2);
          det.appendChild(pre);
        }
        box.appendChild(det);
      });
      drawerBody.appendChild(section("file", "审计记录", box));
    }

    /* 向量嵌入 */
    const embeddings = data.embeddings || [];
    if (embeddings.length) {
      const box = el("div");
      embeddings.forEach((em) => {
        const row = el("div", "embed-row");
        row.appendChild(el("span", "embed-provider", (em.provider_id || "-") + " · " + (em.model || "-")));
        row.appendChild(
          el(
            "span",
            "embed-meta",
            "dim " + (em.dimension ?? "-") + " · gen " + (em.generation ?? "-") + (em.updated_at ? " · " + fmtAgo(em.updated_at) : "")
          )
        );
        box.appendChild(row);
      });
      drawerBody.appendChild(section("spark", "向量嵌入", box));
    }

    injectIcons(drawerBody);
    renderFoot(item);
  }

  function useItemEl(u) {
    const item = el("div", "use-item");
    const rank = el("span", "use-rank" + (u.selected ? " hit" : ""), String(u.rank ?? "-"));
    item.appendChild(rank);
    const main = el("div", "use-main");
    const id = el("div", "use-id");
    id.textContent =
      "#" + String(u.request_id || "").slice(0, 8) +
      " · " + scopeText({ scope_type: u.scope_type, scope_hash: u.scope_hash, scope_label: u.scope_label });
    main.appendChild(id);
    const sub = el("div", "use-sub");
    sub.textContent = [
      "候选 " + (u.candidate_count ?? "-") + " 条",
      "排名第 " + (u.rank ?? "-"),
      u.selected ? "已选用" : "未选用",
      u.duration_ms != null ? u.duration_ms + "ms" : "",
    ].filter(Boolean).join(" · ");
    if (u.reason) {
      sub.appendChild(document.createElement("br"));
      sub.appendChild(document.createTextNode("理由：" + u.reason));
    }
    main.appendChild(sub);
    item.appendChild(main);
    item.appendChild(el("span", "use-score", u.score != null ? Number(u.score).toFixed(2) : "-"));
    return item;
  }

  function renderFoot(item) {
    footEl.innerHTML = "";
    const add = (label, act, variant, icon) => {
      const b = el("button", "btn btn-sm " + variant, label);
      b.dataset.act = act;
      if (icon) b.insertAdjacentHTML("afterbegin", HZ.icon(icon));
      footEl.appendChild(b);
    };
    add("编辑样例", "edit", "btn-primary", "edit");
    if (item.status !== "approved") add("审核通过", "approve", "btn-tonal", "check");
    if (item.status !== "rejected") add("拒绝", "reject", "btn-ghost", "alert");
    if (item.status === "approved" && item.enabled) add("停用", "disable", "btn-ghost", "zap_off");
    if (item.status === "approved" && !item.enabled) add("启用", "enable", "btn-ghost", "refresh");
    if (item.status === "tombstoned") add("恢复", "restore", "btn-ghost", "refresh");
    add("删除", "delete", "btn-ghost ex-danger", "trash");
  }

  /* ---------- 弹窗（本地实现，reason 输入等） ---------- */
  const modal = el("div", "ex-modal");
  modal.style.display = "none";
  document.body.appendChild(modal);

  let modalResolve = null;

  function closeModal(result) {
    modal.style.display = "none";
    modal.innerHTML = "";
    if (modalResolve) {
      const r = modalResolve;
      modalResolve = null;
      r(result === true);
    }
  }

  function openFormModal(title, opts, buildFields) {
    const card = el("div", "ex-modal-card");
    const head = el("div", "ex-modal-head");
    head.appendChild(el("span", "drawer-title", title));
    const closeBtn = el("button", "drawer-close", "✕");
    closeBtn.dataset.mClose = "";
    head.appendChild(closeBtn);
    const body = el("div", "ex-modal-body");
    buildFields(body);
    const f = el("div", "ex-modal-foot");
    const cancel = el("button", "btn btn-ghost", "取消");
    cancel.dataset.mClose = "";
    const ok = el("button", "btn btn-primary" + (opts && opts.danger ? " ex-danger" : ""), (opts && opts.okText) || "确定");
    ok.dataset.mOk = "";
    f.appendChild(cancel);
    f.appendChild(ok);
    card.appendChild(head);
    card.appendChild(body);
    card.appendChild(f);
    modal.innerHTML = "";
    modal.appendChild(card);
    modal.style.display = "flex";
    return new Promise((resolve) => {
      modalResolve = resolve;
    });
  }

  modal.addEventListener("click", (e) => {
    if (e.target === modal) return closeModal(false);
    if (e.target.closest("[data-m-close]")) return closeModal(false);
    if (e.target.closest("[data-m-ok]")) return closeModal(true);
    if (e.target.closest("[data-add-turn]")) {
      const box = $("[data-turns-box]", modal);
      if (box && $$(".ex-turn-row", box).length < 3) box.appendChild(turnRow("user", ""));
      return;
    }
    const delTurn = e.target.closest("[data-del-turn]");
    if (delTurn) {
      const row = delTurn.closest(".ex-turn-row");
      if (row) row.remove();
    }
  });

  function turnRow(role, content) {
    const row = el("div", "ex-turn-row");
    const sel = el("select", "ex-input");
    [["user", "user（提问）"], ["assistant", "assistant（回复）"]].forEach(([v, t]) => {
      const op = el("option", null, t);
      op.value = v;
      sel.appendChild(op);
    });
    sel.value = role === "assistant" ? "assistant" : "user";
    const ta = el("textarea", "ex-input");
    ta.placeholder = "该轮消息内容";
    ta.value = content || "";
    const del = el("button", "icon-btn ex-del-turn", "✕");
    del.dataset.delTurn = "";
    del.title = "删除本轮";
    row.appendChild(sel);
    row.appendChild(ta);
    row.appendChild(del);
    return row;
  }

  function turnsFromModal() {
    return $$(".ex-turn-row", modal)
      .map((r) => ({ role: $("select", r).value, content: $("textarea", r).value.trim() }))
      .filter((t) => t.content);
  }

  function qualityField(value, id) {
    const wrap = el("div", "ex-q-field");
    const range = el("input", "range");
    range.type = "range";
    range.min = "0";
    range.max = "1";
    range.step = "0.01";
    if (id) range.id = id;
    range.value = String(value == null ? 0.8 : value);
    const out = el("span", "range-val", range.value);
    const paint = () => {
      const v = Number(range.value);
      out.textContent = range.value;
      range.style.setProperty("--fill", ((v - Number(range.min)) / (Number(range.max) - Number(range.min))) * 100 + "%");
    };
    range.addEventListener("input", paint);
    paint();
    wrap.appendChild(range);
    wrap.appendChild(out);
    return wrap;
  }

  function promptReason(title, opts) {
    return openFormModal(title, { okText: "确认", danger: opts && opts.danger }, (body) => {
      if (opts && opts.hint) body.appendChild(el("div", "ex-modal-hint", opts.hint));
      body.appendChild(field("操作原因", textArea((opts && opts.placeholder) || "请输入操作原因", "", "mReason", 2)));
    }).then((ok) => (ok ? valOf("mReason") : null));
  }

  /* ---------- 状态操作 ---------- */
  async function postAction(payload) {
    try {
      await api.post("reply-example-action", payload);
      toast("操作成功", { type: "success" });
      if (payload.action === "delete") {
        closeDrawer();
      } else if (payload.id && detail && detail.id) {
        loadDetail(detail.id);
      }
      loadList();
    } catch (e) {
      const err = api.errorOf(e);
      if (err.status === 409) {
        toast("内容已被他人修改（版本冲突），请刷新后重试", { type: "error" });
        if (detail && detail.id) loadDetail(detail.id);
        loadList();
      } else {
        toast(err.message, { type: "error" });
      }
    }
  }

  async function onFootAction(act) {
    if (!detail) return;
    const item = detail;
    if (act === "edit") return openEditModal();
    if (act === "delete") {
      confirmDlg({
        title: "删除样例",
        text: "将物理删除该样例及其全部修订与审计记录，不可恢复。",
        danger: true,
        onConfirm: async () => {
          const reason = await promptReason("删除样例", { danger: true, hint: "删除后不可恢复。", placeholder: "删除原因（必填）" });
          if (reason == null) return;
          if (!reason) return toast("请填写删除原因", { type: "error" });
          postAction({ action: "delete", id: item.id, reason });
        },
      });
      return;
    }
    if (act === "enable" && item.status !== "approved") {
      toast("仅 approved 样例可启用", { type: "error" });
      return;
    }
    const reason = await promptReason(ACT_LABEL[act] || act, { placeholder: "操作原因（可选）" });
    if (reason == null) return;
    postAction({ action: act, id: item.id, reason: reason || undefined });
  }

  function onCardToggle(card) {
    const id = Number(card.dataset.id);
    const enable = card.dataset.enabled !== "1";
    if (enable && card.dataset.status !== "approved") {
      toast("仅 approved 样例可启用", { type: "error" });
      return;
    }
    api
      .post("reply-example-action", { id, action: enable ? "enable" : "disable" })
      .then(() => {
        toast("操作成功", { type: "success" });
        loadList();
      })
      .catch((e) => toast(api.errorOf(e).message, { type: "error" }));
  }

  /* ---------- 编辑 / 新建 ---------- */
  async function openEditModal() {
    if (!detail) return;
    const item = detail;
    const ok = await openFormModal("编辑样例", { okText: "保存" }, (body) => {
      body.appendChild(field("标题 *", textInput("样例标题", item.title, "mTitle")));
      body.appendChild(field("话题", textInput("话题（可选）", item.topic, "mTopic")));
      body.appendChild(field("意图", textInput("意图（可选）", item.intent, "mIntent")));
      const turnsBox = el("div", "ex-turn-box");
      turnsBox.dataset.turnsBox = "";
      (item.turns || []).forEach((t) => turnsBox.appendChild(turnRow(t.role, t.content)));
      if (!(item.turns || []).length) turnsBox.appendChild(turnRow("user", ""));
      body.appendChild(field("对话轮次（1-3 轮）", turnsBox));
      const addBtn = el("button", "btn btn-sm btn-ghost", "添加一轮");
      addBtn.dataset.addTurn = "";
      body.appendChild(addBtn);
      body.appendChild(field("理想回复要点", textArea("期望的回复风格与要点", item.ideal_reply, "mIdeal", 3)));
      body.appendChild(field("关键词（逗号分隔）", textInput("关键词1,关键词2", (item.keywords || []).join("，"), "mKeywords")));
      body.appendChild(field("风格标签（逗号分隔）", textInput("害羞,简短", (item.style_tags || []).join("，"), "mTags")));
      body.appendChild(field("适用条件", textArea("何时使用本样例", item.conditions, "mConditions", 2)));
      body.appendChild(field("排除条件", textArea("何时禁用本样例", item.exclusions, "mExclusions", 2)));
      body.appendChild(field("备注", textArea("补充说明", item.notes, "mNotes", 2)));
      body.appendChild(field("质量分", qualityField(item.quality_score, "mQuality")));
    });
    if (!ok) return;
    const title = valOf("mTitle");
    const turns = turnsFromModal();
    if (!title) return toast("标题不能为空", { type: "error" });
    if (!turns.length) return toast("至少需要 1 轮对话", { type: "error" });
    if (turns.length > 3) return toast("最多 3 轮对话", { type: "error" });
    const q = Number($("#mQuality").value);
    postAction({
      action: "update",
      id: item.id,
      revision: item.revision,
      title,
      topic: valOf("mTopic") || undefined,
      intent: valOf("mIntent") || undefined,
      turns,
      ideal_reply: valOf("mIdeal") || undefined,
      keywords: splitList(valOf("mKeywords")),
      style_tags: splitList(valOf("mTags")),
      conditions: valOf("mConditions") || undefined,
      exclusions: valOf("mExclusions") || undefined,
      notes: valOf("mNotes") || undefined,
      quality_score: Number.isFinite(q) ? q : undefined,
    });
  }

  async function openCreateModal() {
    if (!scopeOptions.length) {
      toast("作用域列表尚未加载，请稍后重试", { type: "error" });
      return;
    }
    const ok = await openFormModal("新建样例", { okText: "创建" }, (body) => {
      body.appendChild(field("作用域 *", optionsSelect("mScope", scopeOptions, (o) => o.scope_label, "请选择作用域")));
      body.appendChild(field("Agent", optionsSelect("mAgent", agentOptions, (o) => o.name, "不指定（默认）")));
      body.appendChild(field("标题 *", textInput("样例标题", "", "mTitle")));
      body.appendChild(field("话题", textInput("话题（可选）", "", "mTopic")));
      body.appendChild(field("意图", textInput("意图（可选）", "", "mIntent")));
      const turnsBox = el("div", "ex-turn-box");
      turnsBox.dataset.turnsBox = "";
      turnsBox.appendChild(turnRow("user", ""));
      turnsBox.appendChild(turnRow("assistant", ""));
      body.appendChild(field("对话轮次（1-3 轮）", turnsBox));
      const addBtn = el("button", "btn btn-sm btn-ghost", "添加一轮");
      addBtn.dataset.addTurn = "";
      body.appendChild(addBtn);
      body.appendChild(field("理想回复要点", textArea("期望的回复风格与要点", "", "mIdeal", 3)));
      body.appendChild(field("关键词（逗号分隔）", textInput("关键词1,关键词2", "", "mKeywords")));
      body.appendChild(field("风格标签（逗号分隔）", textInput("害羞,简短", "", "mTags")));
      body.appendChild(field("适用条件", textArea("何时使用本样例", "", "mConditions", 2)));
      body.appendChild(field("排除条件", textArea("何时禁用本样例", "", "mExclusions", 2)));
      body.appendChild(field("备注", textArea("补充说明", "", "mNotes", 2)));
      body.appendChild(field("质量分", qualityField(0.8, "mQuality")));
    });
    if (!ok) return;
    const scope = scopeOptions[parseInt(valOf("mScope"), 10)];
    if (!scope) return toast("请选择作用域", { type: "error" });
    const agent = agentOptions[parseInt(valOf("mAgent"), 10)];
    const title = valOf("mTitle");
    const turns = turnsFromModal();
    if (!title) return toast("标题不能为空", { type: "error" });
    if (!turns.length) return toast("至少需要 1 轮对话", { type: "error" });
    if (turns.length > 3) return toast("最多 3 轮对话", { type: "error" });
    const q = Number($("#mQuality").value);
    postAction({
      action: "create",
      scope_token: scope.scope_token,
      scope_type: scope.scope_type,
      agent_id: agent ? agent.agent_id : undefined,
      title,
      topic: valOf("mTopic") || undefined,
      intent: valOf("mIntent") || undefined,
      turns,
      ideal_reply: valOf("mIdeal") || undefined,
      keywords: splitList(valOf("mKeywords")),
      style_tags: splitList(valOf("mTags")),
      conditions: valOf("mConditions") || undefined,
      exclusions: valOf("mExclusions") || undefined,
      notes: valOf("mNotes") || undefined,
      quality_score: Number.isFinite(q) ? q : undefined,
    });
  }

  /* ---------- 召回测试 ---------- */
  async function runRecall() {
    const query = $("#recallQuery").value.trim();
    const scope = scopeOptions[parseInt($("#recallScope").value, 10)];
    const agent = agentOptions[parseInt($("#recallAgent").value, 10)];
    if (!query) return toast("请输入测试查询", { type: "error" });
    if (!scope) return toast("请选择作用域", { type: "error" });
    if (!scope.scope_token) return toast("该作用域缺少 scope_token，无法测试召回", { type: "error" });
    if (!agent || !agent.agent_id || agent.agent_id === "*") return toast("请选择具体 Agent（不能为共享 *）", { type: "error" });
    try {
      const data = await api.post("reply-example-recall-debug", {
        query,
        scope_token: scope.scope_token,
        agent_id: agent.agent_id,
        kind: "example",
        limit: 5,
      });
      renderRecall(data);
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  function renderRecall(data) {
    const result = $("#recallResult");
    result.style.display = "";
    const inc = $("#recallIncluded");
    inc.style.display = "";
    inc.className = "tag " + (data.included ? "tag-ok" : "tag-disabled2");
    inc.textContent = data.included ? "included · 将注入" : "included · 未注入";

    const itemsBox = $("#recallItems");
    itemsBox.innerHTML = "";
    const items = data.items || [];
    $("#recallMeta").textContent = "候选 " + items.length + " 条";
    if (!items.length) {
      itemsBox.appendChild(el("div", "ex-empty", "未命中任何样例"));
    }
    items.forEach((it) => {
      const row = el("div", "ex-recall-item");
      const head = el("div", "ex-recall-head");
      head.appendChild(el("span", "ex-recall-title", it.title || "样例 #" + (it.example_id ?? it.id ?? "")));
      if (it.score != null) head.appendChild(el("span", "ex-recall-score", "score " + Number(it.score).toFixed(3)));
      row.appendChild(head);
      const sub = el("div", "ex-recall-sub");
      const parts = [];
      if (it.rank != null) parts.push("rank " + it.rank);
      if (it.selected != null) parts.push(it.selected ? "selected" : "skipped");
      if (it.reason) parts.push(it.reason);
      sub.textContent = parts.join(" · ");
      row.appendChild(sub);
      itemsBox.appendChild(row);
    });

    const contentEl = $("#recallContent");
    contentEl.textContent = data.content || "（无注入内容）";
  }

  /* ---------- 事件绑定 ---------- */

  /* 状态筛选 seg（事件委托） */
  $(".ex-filter").addEventListener("click", (e) => {
    const seg = e.target.closest(".seg-item");
    if (!seg) return;
    seg.parentElement.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
    seg.classList.add("active");
    current.status = seg.dataset.status || "";
    current.page = 1;
    loadList();
  });

  /* 作用域 / Agent 下拉 */
  $("#exScopeSel").addEventListener("change", (e) => {
    current.scopeIndex = parseInt(e.target.value, 10);
    current.page = 1;
    loadList();
  });
  $("#exAgentSel").addEventListener("change", (e) => {
    current.agentIndex = parseInt(e.target.value, 10);
    current.page = 1;
    loadList();
  });

  /* 话题 / 意图输入（防抖 350ms） */
  function bindDebounce(sel, key) {
    const node = $(sel);
    if (!node) return;
    let timer = null;
    node.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        current[key] = node.value.trim();
        current.page = 1;
        loadList();
      }, 350);
    });
  }
  bindDebounce("#exTopicInput", "topic");
  bindDebounce("#exIntentInput", "intent");

  /* 顶栏搜索（防抖 350ms） */
  const searchInput = $("#topbar input");
  if (searchInput) {
    let timer = null;
    searchInput.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        current.search = searchInput.value.trim();
        current.page = 1;
        loadList();
      }, 350);
    });
  }

  /* 顶栏「新建样例」 */
  $(".topbar-actions").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (btn && btn.textContent.includes("新建样例")) openCreateModal();
  });

  /* 列表：卡片点击 / 卡片内快捷操作 */
  listEl.addEventListener("click", (e) => {
    const card = e.target.closest(".ex-card");
    if (!card) return;
    const actBtn = e.target.closest("[data-act]");
    if (actBtn) {
      if (actBtn.dataset.act === "edit") {
        openDrawer(Number(card.dataset.id));
      } else if (actBtn.dataset.act === "toggle") {
        onCardToggle(card);
      }
      return;
    }
    openDrawer(Number(card.dataset.id));
  });

  /* 分页 */
  pagerEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".pg-btn");
    if (!btn || btn.disabled) return;
    const p = btn.dataset.page;
    if (p === "prev") current.page = Math.max(1, current.page - 1);
    else if (p === "next") current.page += 1;
    else current.page = Number(p);
    loadList();
  });

  /* 抽屉开关 */
  mask.addEventListener("click", closeDrawer);
  $("#drawerClose").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  /* 抽屉底部操作（事件委托） */
  footEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    onFootAction(btn.dataset.act);
  });

  /* 召回测试 */
  $("#recallBtn").addEventListener("click", runRecall);

  /* ---------- 启动 ---------- */
  loadOptions();
  loadList();
})();
