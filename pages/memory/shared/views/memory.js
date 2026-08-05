/**
 * View: Memory — 长期记忆页真实交互（对接 HZ.api）。
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js（缺失时清空 mock 并提示）
 */
(function () {
  HZ.renderSidebar("memory");
  HZ.renderTopbar({
    title: "长期记忆",
    sub: "OpenViking workspace · L0/L1/L2 分层召回 · 作用域隔离",
    search: "搜索 memory_key、内容…",
    actions: [{ label: "新建记忆", icon: "plus", variant: "primary" }],
  });
  HZ.initReveal();

  /* ========== api.js 缺失降级：清空 mock 并提示 ========== */
  function renderApiUnavailable() {
    /* 清空带 mock 数据的数据容器（列表/统计/详情等） */
    const ids = [
      "badgeMemories", "badgeJobs",
      "scopeSeg", "agentSeg",
      "memList", "memPager",
      "statActive", "statCandidate", "statSuperseded", "statRejected", "statWorker", "statRecall",
      "jobMiniList", "recallScopeSeg", "recallAgent", "recallResult",
      "jobStatusSeg", "jobList", "jobPager",
      "detailTypeTag", "detailStatusTag",
      "dAbstract", "dOverview", "dContent", "dStruct", "dChips",
      "confVal", "confBar", "impVal", "impBar",
      "eviLabel", "eviRows", "revRows", "auditRows", "dUri",
    ];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = "";
    });
    /* 幂等：只插入一个错误条 */
    if (document.querySelector(".errbar[data-api-unavailable]")) return;
    const main = document.querySelector(".main");
    const bar = document.createElement("div");
    bar.className = "errbar";
    bar.dataset.apiUnavailable = "1";
    const icon = document.createElement("span");
    icon.className = "errbar-icon";
    icon.innerHTML = window.HZ && HZ.icon ? HZ.icon("alert", 15) : "";
    const text = document.createElement("span");
    text.className = "errbar-text";
    text.textContent = "共享 API 层未加载，无法显示真实数据";
    bar.appendChild(icon);
    bar.appendChild(text);
    if (main) main.insertBefore(bar, main.firstChild);
  }

  if (!window.HZ || !HZ.api) {
    console.error("共享 API 层（shared/api.js）未加载，无法获取真实数据");
    renderApiUnavailable(); // 清空 mock 内容 + 显示错误提示
    return;
  }

  /* ================= 状态与工具 ================= */
  const $ = (id) => document.getElementById(id);
  const state = {
    page: 1,
    pageSize: 8,
    jobPage: 1,
    jobPageSize: 8,
    status: "",
    type: "",
    agentId: "",
    scopeToken: "",
    search: "",
    memories: [],
    scopeOptions: [],
    agentOptions: [],
    current: null,
    detail: null,
    jobs: [],
    recallScopes: [],
    recallScope: "",
    recallAgent: "default",
  };

  const STATUS_LABEL = {
    active: "active",
    candidate: "candidate",
    rejected: "rejected",
    superseded: "superseded",
  };
  const JOB_STATUS_LABEL = {
    pending: "排队中",
    running: "运行中",
    retry: "重试",
    completed: "已完成",
    dead: "失败",
  };
  const JOB_STATUS_TAG = {
    pending: "tag-candidate",
    running: "tag-candidate",
    retry: "tag-review",
    completed: "tag-ok",
    dead: "tag-rejected",
  };
  const TYPE_DOT = {
    preference: "var(--pink)",
    profile: "var(--blue)",
    entity: "var(--violet)",
    event: "var(--green)",
  };
  const TYPE_TAG = {
    preference: "tag-pink",
    profile: "tag-scope",
    entity: "tag-alias",
    event: "",
  };

  function esc(s) {
    return String(s == null ? "" : s);
  }
  function timeLabel(iso) {
    return HZ.api && HZ.api.time ? HZ.api.time(iso) : iso || "—";
  }
  function agoLabel(iso) {
    return HZ.api && HZ.api.ago ? HZ.api.ago(iso) : iso || "—";
  }
  function scopeLabel(scopeType, scopeHash) {
    if (HZ.api && HZ.api.scopeLabel) return HZ.api.scopeLabel(scopeType, scopeHash);
    const map = { global: "全局", private_user: "私聊用户", group: "群聊", group_member: "群成员" };
    return (map[scopeType] || scopeType) + (scopeHash ? " · " + String(scopeHash).slice(0, 8) : "");
  }
  function pct(v) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? Math.round(Math.min(1, Math.max(0, n)) * 100) : 0;
  }
  function failToast(e) {
    const info = HZ.api && HZ.api.errorOf ? HZ.api.errorOf(e) : { message: String((e && e.message) || e || "请求失败") };
    if (HZ.toast) HZ.toast(info.message || "请求失败", { type: "error" });
    else console.error(info.message);
  }

  /* ================= 初始化并行加载 ================= */
  async function init() {
    // 顶栏「新建记忆」按钮
    const topbarActions = document.querySelector(".topbar-actions");
    if (topbarActions) {
      const newBtn = topbarActions.querySelector(".btn");
      if (newBtn) newBtn.addEventListener("click", openCreateModal);
    }

    try {
      const [status, overview, agentOpts, memPage] = await Promise.all([
        HZ.api.get("memory-status"),
        HZ.api.get("memory-overview").catch((e) => {
          if (e && e.status === 409) return null;
          throw e;
        }),
        HZ.api.get("memory-agent-options").catch(() => null),
        HZ.api.get("memories", HZ.api.pageParams({ page: 1, pageSize: state.pageSize })).catch(() => null),
      ]);

      state.status = status || {};
      state.overview = overview || {};
      state.scopeOptions = (overview && overview.scope_options) || [];
      state.agentOptions = (agentOpts && agentOpts.items) || [];

      fillScopes();
      fillAgents();
      fillRecallControls();
      renderStatusSidebar();

      if (status && status.state !== "ready") {
        showGuide(status);
      } else {
        hideGuide();
        await loadMemories();
      }
      if (memPage) {
        state.memories = memPage.items || [];
        renderMemories();
        renderPager("memPager", state.page, state.pageSize, memPage.total, loadMemories);
        renderBadge("badgeMemories", memPage.total);
        renderStatCounts((overview && overview.memories && overview.memories.by_status) || {});
      }
      loadMiniJobs();
    } catch (e) {
      failToast(e);
      showGuide({ reason: "load_error", openviking_state: "error" });
      renderApiUnavailable(); // 清空 mock 内容 + 显示错误提示，防止假数据误导
    }
  }

  function fillScopes() {
    const seg = $("scopeSeg");
    seg.innerHTML = "";
    const add = (label, token, count) => {
      const el = document.createElement("span");
      el.className = "seg-item" + (!token ? " active" : "");
      el.dataset.scopeToken = token || "";
      if (count != null) {
        const c = document.createElement("span");
        c.className = "seg-count";
        c.textContent = count;
        el.appendChild(c);
      }
      const txt = document.createElement("span");
      txt.textContent = label;
      el.insertBefore(txt, el.firstChild);
      seg.appendChild(el);
    };
    // 全部作用域（无 token）
    add("全部作用域", "");
    // scope_options（后端已把 global 置顶）
    (state.scopeOptions || []).forEach((s) => {
      const label = (HZ.api && HZ.api.scopeLabel ? HZ.api.scopeLabel(s.scope_type, s.scope_hash) : null) || scopeLabel(s.scope_type, s.scope_hash);
      add(label, s.scope_token || "");
    });
    bindSegClick(seg, (el) => {
      state.scopeToken = el.dataset.scopeToken || "";
      state.page = 1;
      loadMemories();
    });
  }

  function fillAgents() {
    const seg = $("agentSeg");
    seg.innerHTML = "";
    const add = (label, id) => {
      const el = document.createElement("span");
      el.className = "seg-item" + (!id ? " active" : "");
      el.dataset.agentId = id || "";
      el.textContent = label;
      seg.appendChild(el);
    };
    add("全部 Agent", "");
    (state.agentOptions || []).forEach((a) => {
      const label = a.label || (a.id === "*" ? "共享记忆" : a.id);
      add(label, a.id);
    });
    bindSegClick(seg, (el) => {
      state.agentId = el.dataset.agentId || "";
      state.page = 1;
      loadMemories();
    });
  }

  function fillRecallControls() {
    // 召回作用域下拉：用 scope_options + 一个默认（第一个非空）
    const seg = $("recallScopeSeg");
    seg.innerHTML = "";
    const scopes = (state.scopeOptions || []).filter((s) => s.scope_token);
    state.recallScopes = scopes;
    if (!scopes.length) {
      const el = document.createElement("span");
      el.className = "seg-item active";
      el.textContent = "无可用作用域";
      seg.appendChild(el);
      state.recallScope = "";
      return;
    }
    scopes.forEach((s, i) => {
      const el = document.createElement("span");
      el.className = "seg-item" + (i === 0 ? " active" : "");
      el.dataset.scopeToken = s.scope_token;
      const label = (HZ.api && HZ.api.scopeLabel ? HZ.api.scopeLabel(s.scope_type, s.scope_hash) : null) || scopeLabel(s.scope_type, s.scope_hash);
      el.textContent = label;
      seg.appendChild(el);
    });
    state.recallScope = scopes[0].scope_token;
    bindSegClick(seg, (el) => {
      state.recallScope = el.dataset.scopeToken || "";
    });
    // 召回 agent 下拉
    const agentSel = $("recallAgent");
    if (agentSel) {
      agentSel.innerHTML = "";
      (state.agentOptions || [])
        .filter((a) => a.id !== "*")
        .forEach((a) => {
          const opt = document.createElement("option");
          opt.value = a.id;
          opt.textContent = a.label || a.id;
          agentSel.appendChild(opt);
        });
      state.recallAgent = (state.agentOptions[0] && state.agentOptions[0].id) || "default";
      agentSel.value = state.recallAgent;
      agentSel.addEventListener("change", () => {
        state.recallAgent = agentSel.value;
      });
    }
  }

  function renderStatusSidebar() {
    const st = state.status || {};
    $("statWorker").textContent = st.worker_running ? "运行中" : st.state === "ready" ? "空闲" : "未启动";
    if (st.last_recall_at) {
      $("statRecall").textContent = agoLabel(st.last_recall_at) + (st.last_recall_items != null ? " · " + st.last_recall_items + " 条" : "");
    } else {
      $("statRecall").textContent = "—";
    }
  }

  function renderStatCounts(byStatus) {
    const map = { active: "statActive", candidate: "statCandidate", superseded: "statSuperseded", rejected: "statRejected" };
    Object.keys(map).forEach((k) => {
      const el = $(map[k]);
      if (el) el.textContent = byStatus[k] != null ? byStatus[k] : 0;
    });
  }

  function showGuide(status) {
    const guide = $("memGuide");
    const list = $("memList");
    const pager = $("memPager");
    guide.style.display = "";
    list.style.display = "none";
    pager.style.display = "none";
    const reasons = {
      memory_service_not_initialized: "记忆服务尚未初始化（未配置记忆服务或身份密钥）。",
      not_initialized: "记忆服务尚未初始化。",
      load_error: "无法连接记忆服务，请稍后重试。",
    };
    $("guideReason").textContent = (status && status.reason && reasons[status.reason]) || (status && status.reason) || "记忆服务不可用";
    const openviking = (status && status.openviking_state) || "disabled";
    const ovErr = (status && status.openviking_error) || "";
    $("guideMeta").textContent = "OpenViking: " + openviking + (ovErr ? " · " + ovErr : "");
  }

  function hideGuide() {
    $("memGuide").style.display = "none";
    $("memList").style.display = "";
    $("memPager").style.display = "";
  }

  /* ================= 记忆列表 ================= */
  async function loadMemories() {
    const query = {
      ...HZ.api.pageParams({ page: state.page, pageSize: state.pageSize }),
      search: state.search,
      status: state.status,
      type: state.type,
      agent_id: state.agentId,
      ...(state.scopeToken ? HZ.api.scopeFilter({ scopeType: "", scopeToken: state.scopeToken }) : {}),
    };
    try {
      const data = await HZ.api.get("memories", query);
      state.memories = data.items || [];
      renderMemories();
      renderPager("memPager", data.page || 1, data.page_size || state.pageSize, data.total || 0, loadMemories);
      renderBadge("badgeMemories", data.total || 0);
    } catch (e) {
      failToast(e);
      if (e && (e.status === 409 || (e.message && e.message.includes("未初始化")))) {
        showGuide({ reason: "memory_service_not_initialized", openviking_state: "disabled" });
      }
    }
  }

  function emptyState(host, text) {
    host.innerHTML = "";
    const box = document.createElement("div");
    box.className = "mem-empty";
    const icon = document.createElement("div");
    icon.className = "mem-empty-icon";
    icon.innerHTML = HZ.icon("memory");
    box.appendChild(icon);
    const p = document.createElement("p");
    p.textContent = text;
    box.appendChild(p);
    host.appendChild(box);
  }

  function renderMemories() {
    const list = $("memList");
    list.innerHTML = "";
    if (!state.memories.length) {
      emptyState(list, state.search || state.status || state.type || state.agentId || state.scopeToken ? "没有符合条件的记忆" : "暂无记忆");
      return;
    }
    state.memories.forEach((m) => {
      const card = document.createElement("div");
      card.className = "mem-card" + (m.status === "rejected" || m.status === "superseded" ? " dimmed" : "");
      card.dataset.id = m.id;

      const top = document.createElement("div");
      top.className = "mem-top";
      const typeTag = document.createElement("span");
      typeTag.className = "tag " + (TYPE_TAG[m.memory_type] || "");
      typeTag.style.background = TYPE_DOT[m.memory_type] ? "var(--" + m.memory_type + "-soft, var(--pink-soft))" : "";
      typeTag.textContent = m.memory_type;
      top.appendChild(typeTag);

      const scopeTag = document.createElement("span");
      scopeTag.className = "tag tag-scope";
      scopeTag.textContent = m.scope_label || scopeLabel(m.scope_type, m.scope_hash);
      top.appendChild(scopeTag);

      const statusTag = document.createElement("span");
      statusTag.className = "tag tag-" + m.status;
      const dot = document.createElement("span");
      dot.className = "tag-dot";
      statusTag.appendChild(dot);
      statusTag.appendChild(document.createTextNode(" " + (STATUS_LABEL[m.status] || m.status)));
      top.appendChild(statusTag);

      const agent = document.createElement("span");
      agent.className = "mem-agent";
      agent.textContent = m.agent_id === "*" ? "共享" : m.agent_id;
      top.appendChild(agent);

      const actions = document.createElement("div");
      actions.className = "mem-actions";
      const editBtn = document.createElement("button");
      editBtn.className = "icon-btn";
      editBtn.title = "编辑";
      editBtn.innerHTML = HZ.icon("edit");
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openEditModal(m);
      });
      const rejectBtn = document.createElement("button");
      rejectBtn.className = "icon-btn";
      rejectBtn.title = "标记 rejected";
      rejectBtn.innerHTML = HZ.icon("trash");
      rejectBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        confirmReject(m);
      });
      actions.appendChild(editBtn);
      actions.appendChild(rejectBtn);
      top.appendChild(actions);
      card.appendChild(top);

      const abstract = document.createElement("div");
      abstract.className = "mem-abstract";
      abstract.textContent = m.content && m.content.length > 80 ? m.content.slice(0, 80) + "…" : m.content || "";
      card.appendChild(abstract);

      const overview = document.createElement("div");
      overview.className = "mem-overview";
      overview.textContent = m.overview || m.content || "";
      card.appendChild(overview);

      const meta = document.createElement("div");
      meta.className = "mem-meta";
      const key = document.createElement("span");
      key.className = "mem-key";
      key.textContent = m.memory_key;
      meta.appendChild(key);
      const evi = document.createElement("span");
      evi.className = "m";
      evi.innerHTML = HZ.icon("quote");
      const eviText = document.createElement("span");
      eviText.textContent = m.evidence_count != null ? m.evidence_count + " 条证据" : "";
      evi.appendChild(eviText);
      meta.appendChild(evi);
      const ver = document.createElement("span");
      ver.className = "m";
      ver.innerHTML = HZ.icon("history");
      const verText = document.createElement("span");
      verText.textContent = `v${m.version || 1} · ${timeLabel(m.updated_at)}`;
      ver.appendChild(verText);
      meta.appendChild(ver);
      const score = document.createElement("span");
      score.className = "m";
      score.innerHTML = HZ.icon("spark");
      const scoreText = document.createElement("span");
      scoreText.textContent = `conf ${Number(m.confidence || 0).toFixed(2)} · imp ${Number(m.importance || 0).toFixed(1)}`;
      score.appendChild(scoreText);
      meta.appendChild(score);
      card.appendChild(meta);

      card.addEventListener("click", () => openDetail(m.id));
      list.appendChild(card);
    });
  }

  function renderPager(hostId, page, pageSize, total, onGo) {
    const host = $(hostId);
    if (!host) return;
    host.innerHTML = "";
    const pages = Math.max(1, Math.ceil((total || 0) / Math.max(1, pageSize)));
    const prev = document.createElement("button");
    prev.className = "pg-btn";
    prev.disabled = page <= 1;
    prev.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>';
    prev.addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        onGo();
      }
    });
    host.appendChild(prev);

    const start = Math.max(1, page - 2);
    const end = Math.min(pages, page + 2);
    if (start > 1) {
      const btn = document.createElement("button");
      btn.className = "pg-btn";
      btn.textContent = "1";
      btn.addEventListener("click", () => {
        state.page = 1;
        onGo();
      });
      host.appendChild(btn);
      if (start > 2) {
        const ell = document.createElement("span");
        ell.style.cssText = "color:var(--muted);font-size:12px;padding:0 4px";
        ell.textContent = "…";
        host.appendChild(ell);
      }
    }
    for (let p = start; p <= end; p++) {
      const btn = document.createElement("button");
      btn.className = "pg-btn" + (p === page ? " active" : "");
      btn.textContent = String(p);
      btn.addEventListener("click", () => {
        state.page = p;
        onGo();
      });
      host.appendChild(btn);
    }
    if (end < pages) {
      if (end < pages - 1) {
        const ell = document.createElement("span");
        ell.style.cssText = "color:var(--muted);font-size:12px;padding:0 4px";
        ell.textContent = "…";
        host.appendChild(ell);
      }
      const btn = document.createElement("button");
      btn.className = "pg-btn";
      btn.textContent = String(pages);
      btn.addEventListener("click", () => {
        state.page = pages;
        onGo();
      });
      host.appendChild(btn);
    }
    const next = document.createElement("button");
    next.className = "pg-btn";
    next.disabled = page >= pages;
    next.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
    next.addEventListener("click", () => {
      if (state.page < pages) {
        state.page += 1;
        onGo();
      }
    });
    host.appendChild(next);
  }

  function renderBadge(id, total) {
    const el = $(id);
    if (el) el.textContent = total != null ? String(total) : "0";
  }

  /* ================= 筛选联动 ================= */
  function bindSegClick(seg, fn) {
    seg.addEventListener("click", (e) => {
      const item = e.target.closest(".seg-item");
      if (!item) return;
      seg.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      item.classList.add("active");
      fn(item);
    });
  }

  function bindPills() {
    document.querySelectorAll(".pill").forEach((pill) => {
      pill.addEventListener("click", () => {
        const group = pill.dataset.group;
        document.querySelectorAll(`.pill[data-group="${group}"]`).forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
        const value = pill.dataset.value || "";
        if (group === "status") state.status = value;
        if (group === "type") state.type = value;
        state.page = 1;
        loadMemories();
      });
    });
  }

  function bindSearch() {
    const topbar = document.querySelector(".topbar-actions .input-box input");
    if (!topbar) return;
    let timer = null;
    topbar.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.search = topbar.value.trim();
        state.page = 1;
        loadMemories();
      }, 300);
    });
  }

  /* ================= 详情抽屉 ================= */
  const drawer = $("drawer");
  const mask = $("drawerMask");

  function openDrawer() {
    drawer.classList.add("open");
    mask.classList.add("open");
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    document.querySelectorAll(".mem-card").forEach((c) => c.classList.remove("selected"));
  }
  mask.addEventListener("click", closeDrawer);
  $("drawerClose").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  async function openDetail(id) {
    document.querySelectorAll(".mem-card").forEach((c) => c.classList.remove("selected"));
    const card = document.querySelector(`.mem-card[data-id="${id}"]`);
    if (card) card.classList.add("selected");
    openDrawer();
    $("drawerBody").classList.add("loading");
    try {
      const d = await HZ.api.get("memory-detail", { id });
      state.detail = d;
      renderDetail(d);
      $("drawerBody").classList.remove("loading");
    } catch (e) {
      failToast(e);
      $("drawerBody").classList.remove("loading");
      closeDrawer();
    }
  }

  function renderDetail(d) {
    // 头部标签
    $("detailTypeTag").textContent = d.memory_type || "";
    $("detailTypeTag").className = "tag " + (TYPE_TAG[d.memory_type] || "");
    $("detailStatusTag").className = "tag tag-" + (d.status || "");
    const statusText = document.createTextNode(" " + (STATUS_LABEL[d.status] || d.status || ""));
    $("detailStatusTag").innerHTML = '<span class="tag-dot"></span>';
    $("detailStatusTag").appendChild(statusText);

    $("dAbstract").textContent = d.abstract || d.content || "";
    $("dOverview").textContent = d.overview || "";
    $("dContent").textContent = d.content || "";
    try {
      const sv = d.structured_value && typeof d.structured_value === "object" ? d.structured_value : {};
      $("dStruct").textContent = JSON.stringify(sv, null, 2);
    } catch (e) {
      $("dStruct").textContent = "";
    }

    const chips = $("dChips");
    chips.innerHTML = "";
    const scopeChip = document.createElement("span");
    scopeChip.className = "tag tag-lg tag-scope";
    scopeChip.textContent = d.scope_label || scopeLabel(d.scope_type, d.scope_hash);
    chips.appendChild(scopeChip);
    const agentChip = document.createElement("span");
    agentChip.className = "tag tag-lg tag-alias";
    agentChip.textContent = "Agent · " + (d.agent_id === "*" ? "共享" : d.agent_id);
    chips.appendChild(agentChip);
    const keyChip = document.createElement("span");
    keyChip.className = "tag tag-lg tag-pink";
    keyChip.textContent = "memory_key · " + (d.memory_key || "");
    chips.appendChild(keyChip);

    $("confVal").textContent = "conf " + Number(d.confidence || 0).toFixed(2);
    $("confBar").style.width = pct(d.confidence) + "%";
    $("impVal").textContent = "imp " + Number(d.importance || 0).toFixed(2);
    $("impBar").style.width = pct(d.importance) + "%";

    // 证据
    const evi = d.evidence || [];
    $("eviLabel").textContent = "证据（" + evi.length + " 条）";
    const eviRows = $("eviRows");
    eviRows.innerHTML = "";
    if (!evi.length) {
      const p = document.createElement("div");
      p.className = "evi-empty";
      p.textContent = "暂无证据";
      eviRows.appendChild(p);
    }
    evi.forEach((e) => {
      const item = document.createElement("div");
      item.className = "evi-item";
      const icon = document.createElement("div");
      icon.className = "evi-icon";
      icon.innerHTML = HZ.icon("quote");
      const text = document.createElement("div");
      text.className = "evi-text";
      const q = document.createElement("div");
      q.textContent = e.quote || "";
      text.appendChild(q);
      const t = document.createElement("div");
      t.className = "evi-time";
      t.textContent = (timeLabel(e.occurred_at)) + " · source_complete: " + (e.source_complete ? "true" : "false");
      text.appendChild(t);
      item.appendChild(icon);
      item.appendChild(text);
      eviRows.appendChild(item);
    });

    // 版本
    const revs = d.revisions || [];
    const revRows = $("revRows");
    revRows.innerHTML = "";
    if (!revs.length) {
      const p = document.createElement("div");
      p.className = "evi-empty";
      p.textContent = "暂无版本记录";
      revRows.appendChild(p);
    }
    revs.forEach((r) => {
      const row = document.createElement("div");
      row.className = "ver-row";
      const tag = document.createElement("span");
      tag.className = "ver-tag";
      tag.textContent = "v" + (r.version || r.revision || "?");
      const text = document.createElement("span");
      text.className = "ver-text";
      const changed = (r.changed_fields || []).join("、") || "内容更新";
      text.textContent = changed + (r.source_commit_ids && r.source_commit_ids.length ? " · 源自 commit " + String(r.source_commit_ids[0]).slice(0, 8) : "");
      const time = document.createElement("span");
      time.className = "ver-time";
      time.textContent = timeLabel(r.created_at);
      row.appendChild(tag);
      row.appendChild(text);
      row.appendChild(time);
      revRows.appendChild(row);
    });

    // 审计
    const audits = d.audit || [];
    const auditRows = $("auditRows");
    auditRows.innerHTML = "";
    if (!audits.length) {
      const p = document.createElement("div");
      p.className = "evi-empty";
      p.textContent = "暂无审计记录";
      auditRows.appendChild(p);
    }
    audits.forEach((a) => {
      const row = document.createElement("div");
      row.className = "ver-row";
      const tag = document.createElement("span");
      tag.className = "ver-tag";
      tag.textContent = a.action || "";
      const text = document.createElement("span");
      text.className = "ver-text";
      text.textContent = (a.reason || "—") + " · " + (a.actor || "") + (a.version != null ? " · v" + a.version : "");
      const time = document.createElement("span");
      time.className = "ver-time";
      time.textContent = timeLabel(a.created_at);
      row.appendChild(tag);
      row.appendChild(text);
      row.appendChild(time);
      auditRows.appendChild(row);
    });

    $("dUri").textContent = d.uri || "";
  }

  /* ================= 操作：编辑 / 标记 rejected ================= */
  function confirmReject(m) {
    if (!HZ.confirm) return;
    HZ.confirm({
      title: "标记 rejected",
      text: "确定将记忆「" + (m.memory_key || "") + "」标记为 rejected 吗？",
      danger: true,
      onConfirm: async () => {
        try {
          await HZ.api.post("memory-action", {
            action: "reject",
            id: m.id,
            revision: m.version,
            reason: "web admin rejected",
          });
          if (HZ.toast) HZ.toast("已标记 rejected", { type: "success" });
          loadMemories();
          if (state.detail && state.detail.id === m.id) closeDrawer();
        } catch (e) {
          failToast(e);
        }
      },
    });
  }

  function openEditModal(m) {
    const modal = $("memModal");
    $("modalTitle").textContent = "编辑记忆";
    $("mScope").disabled = true;
    $("mScope").innerHTML = "";
    const opt = document.createElement("option");
    opt.value = m.scope_token || "";
    opt.textContent = m.scope_label || "当前作用域";
    $("mScope").appendChild(opt);
    fillAgentSelect(m.agent_id);
    $("mType").value = m.memory_type || "preference";
    $("mKey").value = m.memory_key || "";
    $("mKey").disabled = true;
    $("mContent").value = m.content || "";
    $("mConf").value = m.confidence != null ? m.confidence : 0.9;
    $("mImp").value = m.importance != null ? m.importance : 0.5;
    $("mSubmit").textContent = "保存修改";
    $("mSubmit").dataset.mode = "edit";
    $("mSubmit").dataset.id = m.id;
    $("mSubmit").dataset.revision = m.version || "";
    modal.classList.add("open");
    $("modalMask").classList.add("open");
  }

  function fillAgentSelect(selectedId) {
    const sel = $("mAgent");
    sel.innerHTML = "";
    (state.agentOptions || []).forEach((a) => {
      const opt = document.createElement("option");
      opt.value = a.id;
      opt.textContent = a.label || a.id;
      sel.appendChild(opt);
    });
    if (selectedId) sel.value = selectedId;
  }

  function openCreateModal() {
    const modal = $("memModal");
    $("modalTitle").textContent = "创建一条长期记忆";
    $("mScope").disabled = false;
    $("mScope").innerHTML = "";
    (state.scopeOptions || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.scope_token || "";
      opt.textContent = (HZ.api && HZ.api.scopeLabel ? HZ.api.scopeLabel(s.scope_type, s.scope_hash) : null) || scopeLabel(s.scope_type, s.scope_hash);
      $("mScope").appendChild(opt);
    });
    fillAgentSelect("");
    $("mType").value = "preference";
    $("mKey").value = "";
    $("mKey").disabled = false;
    $("mContent").value = "";
    $("mConf").value = 0.9;
    $("mImp").value = 0.5;
    $("mSubmit").textContent = "创建记忆";
    $("mSubmit").dataset.mode = "create";
    $("mSubmit").dataset.id = "";
    $("mSubmit").dataset.revision = "";
    modal.classList.add("open");
    $("modalMask").classList.add("open");
  }

  function closeModal() {
    $("memModal").classList.remove("open");
    $("modalMask").classList.remove("open");
  }
  $("modalClose").addEventListener("click", closeModal);
  $("modalMask").addEventListener("click", closeModal);

  $("mSubmit").addEventListener("click", async () => {
    const scopeToken = $("mScope").value;
    if (!scopeToken) {
      if (HZ.toast) HZ.toast("请选择作用域", { type: "error" });
      return;
    }
    const key = $("mKey").value.trim();
    const content = $("mContent").value.trim();
    if (!key || !content) {
      if (HZ.toast) HZ.toast("memory_key 与 content 不能为空", { type: "error" });
      return;
    }
    const mode = $("mSubmit").dataset.mode || "create";
    const body = {
      action: mode === "edit" ? "update" : "create",
      scope_token: scopeToken,
      agent_id: $("mAgent").value,
      memory_type: $("mType").value,
      memory_key: key,
      content: content,
      confidence: parseFloat($("mConf").value) || 0.9,
      importance: parseFloat($("mImp").value) || 0.5,
    };
    if (mode === "edit") {
      body.id = $("mSubmit").dataset.id;
      body.revision = parseInt($("mSubmit").dataset.revision, 10) || 0;
    }
    try {
      const detail = await HZ.api.post("memory-action", body);
      if (HZ.toast) HZ.toast(mode === "edit" ? "已保存修改" : "已创建记忆", { type: "success" });
      closeModal();
      loadMemories();
      loadMiniJobs();
      if (detail && detail.id) {
        state.detail = detail;
        renderDetail(detail);
        openDrawer();
      }
    } catch (e) {
      failToast(e);
    }
  });

  /* ================= 后台任务 ================= */
  let jobsInited = false;
  async function loadJobs() {
    try {
      const query = {
        ...HZ.api.pageParams({ page: state.jobPage, pageSize: state.jobPageSize }),
        status: state.jobStatus || "",
      };
      const data = await HZ.api.get("memory-jobs", query);
      state.jobs = data.items || [];
      renderJobs();
      renderPager("jobPager", data.page || 1, data.page_size || state.jobPageSize, data.total || 0, loadJobs);
      renderBadge("badgeJobs", data.total || 0);
    } catch (e) {
      failToast(e);
      const list = $("jobList");
      list.innerHTML = "";
      emptyState(list, "后台任务不可用");
    }
  }

  function jobIcon(job) {
    if (job.status === "running") return '<div class="job-icon i-pink">' + HZ.icon("refresh") + "</div>";
    if (job.status === "completed") return '<div class="job-icon i-green">' + HZ.icon("check") + "</div>";
    if (job.status === "dead") return '<div class="job-icon i-amber">' + HZ.icon("alert") + "</div>";
    if (job.status === "retry") return '<div class="job-icon i-violet">' + HZ.icon("history") + "</div>";
    return '<div class="job-icon i-blue">' + HZ.icon("file") + "</div>";
  }

  function renderJobs() {
    const list = $("jobList");
    list.innerHTML = "";
    if (!state.jobs.length) {
      emptyState(list, "暂无后台任务");
      return;
    }
    state.jobs.forEach((j) => {
      const card = document.createElement("div");
      card.className = "job-card";
      card.innerHTML = jobIcon(j);
      const main = document.createElement("div");
      main.className = "job-main";
      const name = document.createElement("span");
      name.className = "job-name";
      const typeLabel = j.job_type === "extract_turn" ? "记忆提取" : j.job_type === "embed_example" ? "示例嵌入" : j.job_type || "任务";
      name.textContent = typeLabel + " · " + (j.request_id ? j.request_id.slice(0, 16) : j.job_key || "—");
      main.appendChild(name);
      const sub = document.createElement("span");
      sub.className = "job-sub";
      const statusLabel = JOB_STATUS_LABEL[j.status] || j.status;
      sub.textContent = statusLabel + " · attempts " + (j.attempts || 0);
      if (j.error) sub.textContent += " · " + j.error;
      if (j.next_run_at) sub.textContent += " · 下次 " + timeLabel(j.next_run_at);
      main.appendChild(sub);
      card.appendChild(main);
      const tag = document.createElement("span");
      tag.className = "tag " + (JOB_STATUS_TAG[j.status] || "");
      tag.textContent = statusLabel;
      card.appendChild(tag);
      list.appendChild(card);
    });
  }

  function loadMiniJobs() {
    HZ.api
      .get("memory-jobs", HZ.api.pageParams({ page: 1, pageSize: 4 }))
      .then((data) => {
        const mini = $("jobMiniList");
        mini.innerHTML = "";
        const items = data.items || [];
        if (!items.length) {
          const p = document.createElement("div");
          p.className = "evi-empty";
          p.textContent = "暂无任务";
          mini.appendChild(p);
          return;
        }
        items.forEach((j) => {
          const row = document.createElement("div");
          row.className = "job-row";
          row.innerHTML = jobIcon(j);
          const main = document.createElement("div");
          main.className = "job-main";
          const name = document.createElement("span");
          name.className = "job-name";
          name.textContent = (j.job_type === "extract_turn" ? "记忆提取" : j.job_type === "embed_example" ? "示例嵌入" : j.job_type || "任务");
          main.appendChild(name);
          const sub = document.createElement("span");
          sub.className = "job-sub";
          sub.textContent = (JOB_STATUS_LABEL[j.status] || j.status) + (j.error ? " · " + j.error : "");
          main.appendChild(sub);
          row.appendChild(main);
          const tag = document.createElement("span");
          tag.className = "tag " + (JOB_STATUS_TAG[j.status] || "");
          tag.textContent = JOB_STATUS_LABEL[j.status] || j.status;
          row.appendChild(tag);
          mini.appendChild(row);
        });
      })
      .catch(() => {});
  }

  /* ================= 数据源 Tab ================= */
  function bindTabs() {
    document.querySelectorAll(".source-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".source-tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const isMem = tab.dataset.source === "memories";
        $("panelMemories").style.display = isMem ? "" : "none";
        $("panelJobs").style.display = isMem ? "none" : "";
        $("memoryFilters").style.display = isMem ? "" : "none";
        if (!isMem && !jobsInited) {
          jobsInited = true;
          bindJobStatusSeg();
          loadJobs();
        }
      });
    });
    // 侧栏「全部 →」跳转后台任务
    $("jobAllLink").addEventListener("click", (e) => {
      e.preventDefault();
      const tab = document.querySelector('.source-tab[data-source="jobs"]');
      if (tab) tab.click();
    });
  }

  function bindJobStatusSeg() {
    const seg = $("jobStatusSeg");
    bindSegClick(seg, (el) => {
      state.jobStatus = el.dataset.value || "";
      state.jobPage = 1;
      loadJobs();
    });
  }

  /* ================= 召回测试 ================= */
  $("recallBtn").addEventListener("click", runRecall);
  $("recallQuery").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runRecall();
  });

  async function runRecall() {
    const query = $("recallQuery").value.trim();
    if (!query) {
      if (HZ.toast) HZ.toast("请输入查询文本", { type: "error" });
      return;
    }
    if (!state.recallScope) {
      if (HZ.toast) HZ.toast("请先选择作用域", { type: "error" });
      return;
    }
    if (!state.recallAgent || state.recallAgent === "*") {
      if (HZ.toast) HZ.toast("召回测试必须指定具体 Agent", { type: "error" });
      return;
    }
    const out = $("recallResult");
    out.innerHTML = "";
    const loading = document.createElement("div");
    loading.className = "recall-loading";
    loading.textContent = "召回中…";
    out.appendChild(loading);
    try {
      const data = await HZ.api.post("memory-recall-debug", {
        query,
        scope_token: state.recallScope,
        agent_id: state.recallAgent,
        kind: "memory",
        limit: 5,
      });
      out.innerHTML = "";
      if (!data || !data.items || !data.items.length) {
        const empty = document.createElement("div");
        empty.className = "recall-empty";
        empty.textContent = "未命中记忆（included: " + (data && data.included ? "true" : "false") + "）";
        out.appendChild(empty);
        return;
      }
      const title = document.createElement("div");
      title.className = "recall-title";
      title.textContent = "命中 " + data.items.length + " 条 · included: " + (data.included ? "true" : "false");
      out.appendChild(title);
      data.items.forEach((it) => {
        const row = document.createElement("div");
        row.className = "recall-item";
        const head = document.createElement("div");
        head.className = "recall-item-head";
        head.textContent = "[" + (it.memory_type || "?") + "] " + (it.memory_key || "") + " · score " + (it.score != null ? Number(it.score).toFixed(3) : "—");
        const body = document.createElement("div");
        body.className = "recall-item-body";
        body.textContent = it.content || "";
        row.appendChild(head);
        row.appendChild(body);
        out.appendChild(row);
      });
      if (data.content) {
        const code = document.createElement("pre");
        code.className = "recall-xml";
        code.textContent = data.content;
        out.appendChild(code);
      }
    } catch (e) {
      out.innerHTML = "";
      failToast(e);
    }
  }

  /* ================= 启动 ================= */
  bindPills();
  bindSearch();
  bindTabs();
  init();
})();
