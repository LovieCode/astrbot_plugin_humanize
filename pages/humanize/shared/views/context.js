/**
 * View: Context — 上下文追踪页（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET context-runs / GET context-run / GET context-stats
 * 降级：api.js 未加载时停留在静态预览（页面骨架仍可见）。
 * 安全：所有持久化内容（content/raw_output/messages content/failure_detail 等）
 *       一律通过 textContent 写入，禁止拼入 innerHTML。
 */
HZ.views["context"] = { init: function () {

  HZ.renderTopbar({
    title: "上下文追踪",
    sub: "每次 LLM 请求的区块组装与 token 预算全记录",
    search: "搜索请求 ID、消息内容…",
    actions: [],
  });
  HZ.initReveal();

  /* 筛选 seg 高亮（静态预览与真实模式共用） */
  document.querySelectorAll(".scope-seg").forEach((segGroup) => {
    segGroup.addEventListener("click", (e) => {
      const seg = e.target.closest(".seg-item");
      if (!seg) return;
      segGroup.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });

  /* 共享 API 层缺失：清空 mock 静态数据，显示明确错误提示（幂等，多次调用只插一个错误条） */
  function renderApiUnavailable() {
    ["#cxRunList", "#cxDetail", "#cxStats"].forEach((sel) => {
      const node = document.querySelector(sel);
      if (node) node.innerHTML = "";
    });
    const host = document.querySelector(".cx-layout") || document.querySelector(".main");
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

  /* ---------- 共享 UI（api.js 落地前的本地兜底，避免直接抛错） ---------- */
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));
  const initEmpty = HZ.initEmpty;
  const initErrbar = HZ.initErrbar;
  const fmtAgo = (iso) => (api.ago ? api.ago(iso) : String(iso || ""));
  const fmtTime = (iso) => (api.time ? api.time(iso) : String(iso || ""));

  /* ---------- 常量 ---------- */
  const PAGE_SIZE = 8;
  const SECTION_KEY_LABEL = {
    current_message: "当前消息",
    known_terms: "黑话",
    memory_context: "记忆",
    reply_examples: "回复样例",
    response_protocol: "回复协议",
  };
  const OMIT_REASON_LABEL = {
    current_user_message: "当前用户消息",
    no_matching_trusted_term: "无匹配可信词条",
    no_match: "无匹配",
    required_response_protocol: "协议必选区块",
    token_budget_exhausted: "预算超限",
    matched_current_message: "命中当前消息",
    matched_current_message_budgeted: "命中当前消息（限预算）",
    jargon_disabled: "黑话功能关闭",
    source_error: "来源异常",
    memory_service_not_initialized: "记忆服务未初始化",
  };
  const SOURCE_TYPE_LABEL = {
    message: "用户消息",
    repository: "词库",
    memory: "记忆",
    reply_examples: "样例",
    protocol: "协议",
  };
  const SCOPE_LABEL = {
    group: "群聊",
    private_user: "私聊",
    group_member: "群成员",
  };
  const SECTION_COLORS = {
    current_message: "var(--pink)",
    known_terms: "var(--violet)",
    memory_context: "var(--blue)",
    reply_examples: "var(--green)",
    response_protocol: "var(--amber)",
  };
  const MODE_LABEL = { both: "两者", temp_user: "临时用户" };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const listEl = $("#cxRunList");
  const pagerEl = $("#cxPager");
  const detailEl = $("#cxDetail");
  const statsEl = $("#cxStats");

  /* ---------- 状态 ---------- */
  let current = { page: 1, scope: "" };
  let statsDays = 7;
  let detailRequestId = null; // 当前详情 request_id
  let busy = false;
  let detailBusy = false;

  /* ---------- DOM 小工具 ---------- */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function injectIcons(root) {
    root.querySelectorAll("[data-icon]").forEach((n) => {
      const svg = HZ.icon(n.dataset.icon);
      const afterbegin =
        (n.tagName === "BUTTON" && n.textContent.trim()) ||
        n.classList.contains("m") ||
        n.classList.contains("cx-sec-reason") ||
        n.classList.contains("cx-expand") ||
        n.classList.contains("raw-copy");
      if (afterbegin) n.insertAdjacentHTML("afterbegin", svg);
      else n.innerHTML = svg;
    });
  }
  function fmtNum(v) {
    return v == null ? "—" : Number(v).toLocaleString("zh-CN");
  }
  function shortId(id) {
    return String(id == null ? "" : id).slice(0, 8);
  }
  function scopeText(run) {
    return SCOPE_LABEL[run.scope_type] || run.scope_type || "未知";
  }
  function sectionLabel(key) {
    return SECTION_KEY_LABEL[key] || key || "未知区块";
  }
  function sourceLabel(type) {
    return SOURCE_TYPE_LABEL[type] || type || "未知";
  }

  /* ---------- 协议结果标签（protocol_summary 可空：缺失时显示「—」） ---------- */
  function protocolTagEl(ps) {
    const tag = el("span", "tag");
    if (!ps) {
      tag.classList.add("tag-noreply");
      tag.textContent = "—";
      tag.title = "协议结果暂缺（protocol_summary 未生成）";
      return tag;
    }
    if (ps.success) {
      tag.classList.add("tag-reply");
      tag.textContent = "已回复";
      if (ps.model) tag.title = "模型 · " + ps.model;
    } else {
      tag.classList.add("tag-failed");
      tag.textContent = ps.failure_code ? "失败 " + ps.failure_code : "失败";
    }
    return tag;
  }

  /* ---------- 列表 ---------- */
  async function loadList() {
    if (busy) return;
    busy = true;
    try {
      const data = await api.get("context-runs", {
        ...api.pageParams({ page: current.page, pageSize: PAGE_SIZE }),
        scope_type: current.scope || undefined,
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
        listEl.appendChild(initEmpty({ text: "没有符合条件的上下文运行" }));
      } else {
        const box = el("div", "cx-empty");
        box.appendChild(el("p", null, "没有符合条件的上下文运行"));
        box.appendChild(el("div", "cx-empty-sub", "换个筛选条件试试"));
        listEl.appendChild(box);
      }
      return;
    }
    items.forEach((item) => listEl.appendChild(runCardEl(item)));
  }

  function runCardEl(item) {
    const card = el("div", "cx-run");
    card.dataset.requestId = item.request_id;
    if (detailRequestId === item.request_id) card.classList.add("active");

    /* 顶部行：短 ID + 协议结果标签 + 时间 */
    const top = el("div", "cx-run-top");
    top.appendChild(el("span", "cx-run-id", "#" + shortId(item.request_id)));
    top.appendChild(protocolTagEl(item.protocol_summary));
    top.appendChild(el("span", "cx-run-time", item.created_at ? fmtAgo(item.created_at) : ""));
    card.appendChild(top);

    /* 消息内容（后端未提供 message 文本，用 scope + sender 兜底） */
    card.appendChild(el("div", "cx-run-msg", `${scopeText(item)} · 发送者 ${item.sender_id || "—"}`));

    /* 元信息行 */
    const meta = el("div", "cx-run-meta");
    const tokens = el("span", "cx-tokens", fmtNum(item.estimated_tokens) + " tok");
    tokens.setAttribute("data-icon", "spark");
    meta.appendChild(tokens);
    const dur = el("span", "cx-dur");
    if (item.protocol_summary && item.protocol_summary.duration_ms != null) {
      dur.textContent = (item.protocol_summary.duration_ms / 1000).toFixed(1) + "s";
      if (item.protocol_summary.model) dur.textContent += " · " + item.protocol_summary.model;
    } else {
      dur.textContent = "—";
    }
    meta.appendChild(dur);
    const inc = el("span", "cx-inc");
    const incBar = el("span", "cx-inc-bar");
    const incBarI = el("i");
    const included = Number(item.included_sections || 0);
    const omitted = Number(item.omitted_sections || 0);
    const total = included + omitted;
    incBarI.style.width = (total ? Math.round((included / total) * 100) : 0) + "%";
    incBar.appendChild(incBarI);
    inc.appendChild(incBar);
    inc.appendChild(document.createTextNode(`${included}/${total}`));
    meta.appendChild(inc);
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

  /* ---------- 详情 ---------- */
  async function openDetail(requestId) {
    if (detailBusy) return;
    detailBusy = true;
    detailRequestId = requestId;
    $$(".cx-run", listEl).forEach((c) => c.classList.toggle("active", c.dataset.requestId === requestId));
    if (statsEl) statsEl.style.display = "none";
    detailEl.innerHTML = "";
    const loading = el("div", "cx-detail-loading");
    loading.textContent = "加载运行详情…";
    detailEl.appendChild(loading);
    try {
      const data = await api.get("context-run", { request_id: requestId });
      renderDetail(data);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      detailEl.innerHTML = "";
      const box = el("div", "cx-empty");
      box.appendChild(el("p", null, err.status === 404 ? "上下文追踪不存在" : "详情加载失败"));
      box.appendChild(el("div", "cx-empty-sub", err.message));
      if (initErrbar) initErrbar({ message: err.message });
    } finally {
      detailBusy = false;
    }
  }

  function renderDetail(data) {
    const run = data.run || {};
    const sections = (data.sections || []).slice().sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0));
    const reqSnap = data.request_snapshot || {};
    const resSnap = data.response_snapshot || {};
    const protocol = resSnap.protocol || null;
    const response = data.response || null;
    const durationMs =
      (response && response.duration_ms) ||
      (protocol && protocol.duration_ms) ||
      (run.protocol_summary && run.protocol_summary.duration_ms) ||
      null;

    detailEl.innerHTML = "";
    detailEl.appendChild(headCardEl(run, durationMs, protocol));
    detailEl.appendChild(budgetCardEl(sections));
    detailEl.appendChild(sectionsCardEl(sections));
    detailEl.appendChild(rawCardEl(run, reqSnap, resSnap, protocol, response));
    injectIcons(detailEl);
  }

  function statCell(num, label, cls) {
    const stat = el("div", "cx-stat");
    const n = el("span", "cx-stat-num" + (cls ? " " + cls : ""), num);
    stat.appendChild(n);
    stat.appendChild(el("span", "cx-stat-lab", label));
    return stat;
  }

  /* 概览胶囊 */
  function headCardEl(run, durationMs, protocol) {
    const card = el("div", "card cx-head-card");
    const row = el("div", "cx-stats-row");

    const main = el("div", "cx-head-main");
    const headId = el("div", "cx-head-id");
    const backBtn = el("button", "cx-back", "← 返回列表");
    backBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (statsEl) statsEl.style.display = "";
      detailEl.innerHTML = "";
      detailRequestId = "";
      $$(".cx-run", listEl).forEach((c) => c.classList.remove("active"));
    });
    headId.appendChild(backBtn);
    headId.appendChild(el("span", null, "#" + shortId(run.request_id)));
    const tag = el("span", "tag tag-required", "protocol v1");
    headId.appendChild(tag);
    const copyBtn = el("button", "raw-copy", "复制 ID");
    copyBtn.dataset.icon = "copy";
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await navigator.clipboard.writeText(run.request_id || "");
        toast("已复制", { type: "success" });
      } catch (err) {
        toast("复制失败", { type: "error" });
      }
    });
    headId.appendChild(copyBtn);
    main.appendChild(headId);

    const sub = el("div", "cx-head-sub");
    const m1 = el("span", "m", scopeText(run) + (run.scope_id ? " · " + run.scope_id : ""));
    m1.setAttribute("data-icon", "chat");
    sub.appendChild(m1);
    const m2 = el("span", "m", run.sender_id ? "发送者 " + run.sender_id : "发送者已脱敏");
    m2.setAttribute("data-icon", "shield");
    sub.appendChild(m2);
    if (run.message_id) {
      const m = el("span", "m", "消息 " + shortId(run.message_id));
      m.setAttribute("data-icon", "file");
      sub.appendChild(m);
    }
    if (run.protocol_mode) {
      const m = el("span", "m", "注入模式 " + (MODE_LABEL[run.protocol_mode] || run.protocol_mode));
      m.setAttribute("data-icon", "layers");
      sub.appendChild(m);
    }
    if (run.created_at) {
      const m = el("span", "m", fmtTime(run.created_at));
      m.setAttribute("data-icon", "clock");
      sub.appendChild(m);
    }
    main.appendChild(sub);
    row.appendChild(main);

    const stats = el("div", "cx-head-stats");
    stats.appendChild(statCell(fmtNum(run.estimated_tokens), "估算词元", "pink"));
    const included = Number(run.included_sections || 0);
    const omitted = Number(run.omitted_sections || 0);
    stats.appendChild(statCell(String(included), "纳入区块", "green"));
    stats.appendChild(statCell(String(omitted), "省略区块"));
    stats.appendChild(statCell(durationMs != null ? (durationMs / 1000).toFixed(1) + "s" : "—", "耗时"));
    if (protocol && protocol.model) {
      stats.appendChild(statCell(protocol.model, "模型"));
    }
    row.appendChild(stats);
    card.appendChild(row);
    return card;
  }

  /* Token 分布条 */
  function budgetCardEl(sections) {
    const card = el("div", "card");
    const head = el("div", "card-head");
    head.appendChild(el("span", "card-dot"));
    head.appendChild(el("span", "card-title", "Token 预算分布"));
    card.appendChild(head);

    const budget = el("div", "cx-budget");
    const bar = el("div", "cx-budget-bar");
    const legend = el("div", "cx-budget-legend");
    const total = sections.reduce((s, x) => s + (x.estimated_tokens || 0), 0);

    if (!sections.length || total <= 0) {
      bar.appendChild(el("i", null));
      legend.appendChild(el("span", "cx-legend-item", "暂无数据"));
    } else {
      sections.forEach((sec) => {
        const w = Math.max(2, Math.round(((sec.estimated_tokens || 0) / total) * 100));
        const seg = el("i");
        seg.style.width = w + "%";
        seg.style.background = SECTION_COLORS[sec.section_key] || "var(--pink)";
        seg.title = sec.section_key;
        bar.appendChild(seg);
        const item = el("span", "cx-legend-item");
        const dot = el("span", "cx-legend-dot");
        dot.style.background = SECTION_COLORS[sec.section_key] || "var(--pink)";
        item.appendChild(dot);
        item.appendChild(document.createTextNode(`${sectionLabel(sec.section_key)} ${fmtNum(sec.estimated_tokens)}`));
        legend.appendChild(item);
      });
    }
    budget.appendChild(bar);
    budget.appendChild(legend);
    card.appendChild(budget);
    return card;
  }

  /* 5 区块时间线 */
  function sectionsCardEl(sections) {
    const card = el("div", "card");
    const head = el("div", "card-head");
    head.appendChild(el("span", "card-dot"));
    head.appendChild(el("span", "card-title", "组装区块（按 ordinal）"));
    card.appendChild(head);

    const wrap = el("div", "cx-sections");
    if (!sections.length) {
      wrap.appendChild(el("div", "cx-empty", "该运行暂无区块记录"));
    } else {
      sections.forEach((sec, idx) => wrap.appendChild(sectionRowEl(sec, idx === sections.length - 1)));
    }
    card.appendChild(wrap);
    return card;
  }

  function sectionRowEl(sec, isLast) {
    const row = el("div", "cx-sec");

    const rail = el("div", "cx-sec-rail");
    const dot = el("span", "cx-sec-dot" + (sec.included ? "" : " omitted"));
    dot.dataset.icon = sec.included ? "file" : "eye_off";
    rail.appendChild(dot);
    if (!isLast) rail.appendChild(el("span", "cx-sec-line"));
    row.appendChild(rail);

    const body = el("div", "cx-sec-body");
    const inner = el("div", "cx-sec-card" + (sec.included ? "" : " omitted"));

    /* 顶行 */
    const top = el("div", "cx-sec-top");
    const name = el("span", "cx-sec-name", sectionLabel(sec.section_key));
    if (sec.section_key) name.title = sec.section_key;
    top.appendChild(name);
    if (sec.ordinal != null) top.appendChild(el("span", "cx-sec-ordinal", "#" + sec.ordinal));
    if (sec.required) top.appendChild(el("span", "tag tag-required", "必选"));
    top.appendChild(el("span", "tag " + (sec.included ? "tag-included" : "tag-omitted"), sec.included ? "已纳入" : "已省略"));
    const right = el("div", "cx-sec-right");
    right.appendChild(el("span", "tag tag-src", sourceLabel(sec.source_type)));
    const tokTxt =
      (sec.applied_tokens != null ? fmtNum(sec.applied_tokens) : fmtNum(sec.estimated_tokens)) +
      (sec.budget_tokens != null ? " / " + fmtNum(sec.budget_tokens) : "") +
      " tok";
    right.appendChild(el("span", "cx-sec-tokens", tokTxt));
    top.appendChild(right);
    inner.appendChild(top);

    /* 原因 */
    if (sec.reason) {
      const reason = el("div", "cx-sec-reason", "原因 · " + sec.reason);
      reason.setAttribute("data-icon", "spark");
      inner.appendChild(reason);
    }

    /* 预览：preview_truncated 时显示截断标识 + 展开按钮（全文 textContent） */
    if (sec.content_preview) {
      const preview = el("div", "cx-sec-preview" + (sec.preview_truncated ? " truncated" : ""), sec.content_preview);
      inner.appendChild(preview);
      if (sec.preview_truncated && sec.content) {
        const expandBtn = el("button", "cx-expand", "展开全文");
        expandBtn.dataset.icon = "arrow-right";
        expandBtn.addEventListener("click", () => {
          const holder = el("div", "cx-full-preview", sec.content);
          expandBtn.replaceWith(holder);
        });
        inner.appendChild(expandBtn);
      }
    }

    /* 元信息行 */
    const meta = el("div", "cx-sec-meta");
    const targets = (sec.targets || []).filter(Boolean);
    if (targets.length) {
      const t = el("span", null);
      t.appendChild(document.createTextNode("注入目标 "));
      t.appendChild(el("b", null, targets.join(", ")));
      meta.appendChild(t);
    }
    const refs = (sec.source_refs || []).filter(Boolean);
    if (refs.length) {
      const t = el("span", null);
      t.appendChild(document.createTextNode("来源引用 "));
      t.appendChild(el("b", null, refs.join(", ")));
      meta.appendChild(t);
    }
    if (sec.item_count != null) {
      const t = el("span", null);
      t.appendChild(document.createTextNode("条目 "));
      t.appendChild(el("b", null, String(sec.item_count)));
      meta.appendChild(t);
    }
    if (sec.snapshot_complete != null) {
      const t = el("span", null);
      t.appendChild(document.createTextNode("快照 "));
      t.appendChild(el("b", null, sec.snapshot_complete ? "完整" : "不完整"));
      meta.appendChild(t);
    }
    if (sec.content_chars != null) {
      const t = el("span", null);
      t.appendChild(document.createTextNode("正文 "));
      t.appendChild(el("b", null, sec.content_chars + " 字"));
      meta.appendChild(t);
    }
    inner.appendChild(meta);
    body.appendChild(inner);
    row.appendChild(body);
    return row;
  }

  /* 原始上下文卡 */
  function rawCardEl(run, reqSnap, resSnap, protocol, response) {
    const card = el("div", "card");

    /* 头部 */
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "原始上下文"));
    titleWrap.appendChild(el("span", "tag tag-src", "仅预览 · 来自请求快照"));
    head.appendChild(titleWrap);
    const actionTag = el("span", "tag");
    if (response && response.action) {
      actionTag.classList.add("tag-reply");
      actionTag.textContent = "协议动作 · " + response.action;
    } else if (protocol && protocol.action) {
      actionTag.classList.add("tag-reply");
      actionTag.textContent = "协议动作 · " + protocol.action;
    } else {
      actionTag.classList.add("tag-noreply", "noaction");
      actionTag.textContent = "协议动作 · —";
    }
    head.appendChild(actionTag);
    const copyBtn = el("button", "raw-copy", "复制全部");
    copyBtn.dataset.icon = "copy";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(rawAllText(run, reqSnap, resSnap, protocol));
        toast("已复制", { type: "success" });
      } catch (err) {
        toast("复制失败", { type: "error" });
      }
    });
    head.appendChild(copyBtn);
    card.appendChild(head);

    /* 协议元信息（response_snapshot.protocol 或 response） */
    const meta = el("div", "raw-meta");
    if (protocol && protocol.model) {
      meta.appendChild(el("span", "tag tag-src", "模型 · " + protocol.model));
    }
    if (response && response.success !== undefined) {
      meta.appendChild(el("span", "tag tag-src", response.success ? "校验通过 · 无修复" : "校验失败 · " + (response.failure_code || "未知")));
    } else if (protocol && protocol.success !== undefined) {
      meta.appendChild(el("span", "tag tag-src", protocol.success ? "校验通过 · 无修复" : "校验失败 · " + (protocol.failure_code || "未知")));
    } else {
      meta.appendChild(el("span", "tag tag-src noaction", "协议结果暂缺"));
    }
    if (protocol && protocol.duration_ms != null) {
      meta.appendChild(el("span", "tag tag-src", "耗时 " + (protocol.duration_ms / 1000).toFixed(1) + "s"));
    }
    if (protocol && protocol.stage) {
      meta.appendChild(el("span", "tag tag-src", "阶段 " + protocol.stage));
    }
    card.appendChild(meta);

    /* 失败警示条（failure_code 非空） */
    const failCode = (response && response.failure_code) || (protocol && protocol.failure_code);
    const failDetail = (response && response.failure_detail) || (protocol && protocol.failure_detail);
    if (failCode) {
      const alert = el("div", "cx-alert");
      alert.setAttribute("data-icon", "alert");
      const span = el("span");
      span.appendChild(document.createTextNode("协议失败 "));
      span.appendChild(el("code", null, failCode));
      if (failDetail) {
        span.appendChild(document.createTextNode(" · "));
        span.appendChild(el("code", null, failDetail));
      }
      alert.appendChild(span);
      card.appendChild(alert);
    }

    /* 消息列表 */
    const list = el("div", "raw-list");
    list.style.marginTop = "14px";
    const messages = (reqSnap.provider_request && reqSnap.provider_request.messages) || [];
    if (messages.length) {
      messages.forEach((msg, i) => list.appendChild(rawMsgEl(msg, i)));
    } else {
      const empty = el("div", "cx-empty", "请求快照中无消息记录");
      empty.style.padding = "20px 16px";
      list.appendChild(empty);
    }

    /* 响应分隔 + 响应消息 */
    if (resSnap.llm_response) {
      list.appendChild(el("div", "raw-divider", "响应"));
      const resp = el("div", "raw-msg response");
      const respHead = el("div", "raw-msg-head");
      respHead.appendChild(el("span", "raw-idx", "[R]"));
      const role = el("span", "raw-role assistant", "assistant");
      respHead.appendChild(role);
      if (response && response.success !== undefined) {
        respHead.appendChild(el("span", "tag " + (response.success ? "tag-ok" : "tag-failed"), response.success ? "OK" : "失败"));
      } else if (protocol && protocol.success !== undefined) {
        respHead.appendChild(el("span", "tag " + (protocol.success ? "tag-ok" : "tag-failed"), protocol.success ? "OK" : "失败"));
      }
      respHead.appendChild(el("span", "raw-len", (resSnap.llm_response.length || 0) + " 字"));
      resp.appendChild(respHead);
      const body = el("div", "raw-body", resSnap.llm_response);
      resp.appendChild(body);
      list.appendChild(resp);

      /* raw_output 折叠显示 */
      if (protocol && protocol.raw_output) {
        const rawBlock = el("div", "raw-raw");
        rawBlock.textContent = protocol.raw_output;
        list.appendChild(rawBlock);
      }
    }
    card.appendChild(list);
    return card;
  }

  function rawMsgEl(msg, idx) {
    const box = el("div", "raw-msg");
    const head = el("div", "raw-msg-head");
    head.appendChild(el("span", "raw-idx", "[" + (idx + 1) + "]"));
    const role = String(msg.role || "");
    const roleEl = el("span", "raw-role " + (/^(system|user|assistant)$/.test(role) ? role : "user"), role || "user");
    head.appendChild(roleEl);
    if (role === "temp_user") {
      head.appendChild(el("span", "tag tag-required", "temp_user · 注入区块合并于此"));
    }
    const content = msg.content == null ? "" : String(msg.content);
    head.appendChild(el("span", "raw-len", content.length + " 字"));
    const collapseBtn = el("button", "raw-collapse", "收起");
    collapseBtn.dataset.icon = "arrow-right";
    head.appendChild(collapseBtn);
    box.appendChild(head);
    const body = el("div", "raw-body", content);
    box.appendChild(body);
    return box;
  }

  function rawAllText(run, reqSnap, resSnap, protocol) {
    const parts = [];
    const messages = (reqSnap.provider_request && reqSnap.provider_request.messages) || [];
    messages.forEach((m) => {
      parts.push("[" + (m.role || "user") + "]\n" + (m.content == null ? "" : String(m.content)));
    });
    if (resSnap.llm_response) {
      parts.push("[response]\n" + String(resSnap.llm_response));
    }
    if (protocol && protocol.raw_output) {
      parts.push("[raw_output]\n" + String(protocol.raw_output));
    }
    return parts.join("\n\n");
  }

  /* ---------- 底部统计 ---------- */
  async function loadStats() {
    try {
      const data = await api.get("context-stats", { days: statsDays });
      renderStats(data);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      if (statsEl) {
        statsEl.innerHTML = "";
        statsEl.appendChild(el("div", "cx-empty", "统计加载失败"));
      }
    }
  }

  function renderStats(data) {
    if (!statsEl) return;
    statsEl.innerHTML = "";

    /* 头部：标题 + days 切换 */
    const head = el("div", "cx-stats-head");
    head.appendChild(el("span", "card-dot"));
    head.appendChild(el("span", "card-title", "运行统计（近 " + statsDays + " 天）"));
    const seg = el("div", "scope-seg");
    [7, 14, 30].forEach((d) => {
      const item = el("span", "seg-item" + (d === statsDays ? " active" : ""), d + " 天");
      item.dataset.days = String(d);
      seg.appendChild(item);
    });
    head.appendChild(seg);
    statsEl.appendChild(head);

    const row = el("div", "cx-stats-row");

    /* 区块汇总 */
    const sCard = el("div", "card");
    sCard.appendChild(cardHeadEl("区块汇总"));
    const sections = data.sections || [];
    if (!sections.length) {
      sCard.appendChild(el("div", "cx-empty", "暂无数据"));
    } else {
      sections.forEach((s) => {
        const rowEl = el("div", "cx-stat-bar-row");
        rowEl.appendChild(el("span", "cx-sb-name", sectionLabel(s.section_key)));
        const bar = el("div", "cx-sb-bar");
        const barI = el("i");
        const included = s.included || 0;
        const occurrences = s.occurrences || 0;
        barI.style.width = (occurrences ? Math.round((included / occurrences) * 100) : 0) + "%";
        bar.appendChild(barI);
        rowEl.appendChild(bar);
        rowEl.appendChild(el("span", "cx-sb-num", fmtNum(s.average_tokens) + " tok"));
        rowEl.appendChild(el("span", "cx-sb-num", included + "/" + occurrences));
        sCard.appendChild(rowEl);
      });
    }
    row.appendChild(sCard);

    /* 省略原因 */
    const rCard = el("div", "card");
    rCard.appendChild(cardHeadEl("常见省略原因"));
    const reasons = data.reasons || [];
    if (!reasons.length) {
      rCard.appendChild(el("div", "cx-empty", "暂无数据"));
    } else {
      const maxCount = Math.max(1, ...reasons.map((r) => r.count || 0));
      reasons.forEach((r) => {
        const rowEl = el("div", "cx-reason-row");
        const top = el("div", "cx-reason-top");
        const name = el("span", "cx-reason-name");
        name.textContent = sectionLabel(r.section_key) + " · " + (OMIT_REASON_LABEL[r.reason] || r.reason || "");
        top.appendChild(name);
        top.appendChild(el("span", "cx-reason-count", (r.count || 0) + " 次"));
        rowEl.appendChild(top);
        const bar = el("div", "cx-sb-bar");
        const barI = el("i");
        barI.style.width = Math.round(((r.count || 0) / maxCount) * 100) + "%";
        bar.appendChild(barI);
        rowEl.appendChild(bar);
        rCard.appendChild(rowEl);
      });
    }
    row.appendChild(rCard);

    /* 总览 */
    const oCard = el("div", "card");
    oCard.appendChild(cardHeadEl("总览"));
    const oRow = el("div", "row");
    oRow.style.marginTop = "4px";
    oRow.appendChild(statCell(fmtNum(data.runs), "运行次数", "pink"));
    oRow.appendChild(statCell(fmtNum(data.total_tokens), "总 tokens"));
    oRow.appendChild(statCell(fmtNum(data.average_tokens), "平均 tokens", "green"));
    oCard.appendChild(oRow);
    row.appendChild(oCard);

    statsEl.appendChild(row);
  }

  function cardHeadEl(title) {
    const head = el("div", "card-head");
    head.appendChild(el("span", "card-dot"));
    head.appendChild(el("span", "card-title", title));
    return head;
  }

  /* ---------- 事件绑定（委托 + 防抖） ---------- */

  /* 列表点击：运行卡 */
  listEl.addEventListener("click", (e) => {
    const card = e.target.closest(".cx-run");
    if (!card) return;
    openDetail(card.dataset.requestId);
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

  /* 筛选：scope seg（触发重新请求）；注入模式仅前端高亮（静态 seg 已处理） */
  $(".cx-filter").addEventListener("click", (e) => {
    const seg = e.target.closest(".seg-item[data-scope]");
    if (!seg) return;
    current.scope = seg.dataset.scope;
    current.page = 1;
    loadList();
  });

  /* 顶部搜索框（防抖 350ms） */
  const searchInput = $("#topbar input");
  if (searchInput) {
    let timer = null;
    searchInput.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        toast("搜索功能开发中", { type: "info" });
      }, 350);
    });
  }

  /* 原始上下文消息收起/展开（事件委托） */
  detailEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".raw-collapse");
    if (!btn) return;
    const box = btn.closest(".raw-msg");
    if (!box) return;
    const collapsed = box.classList.toggle("collapsed");
    btn.textContent = collapsed ? "展开" : "收起";
  });

  /* 统计 days 切换（事件委托） */
  statsEl.addEventListener("click", (e) => {
    const seg = e.target.closest(".seg-item[data-days]");
    if (!seg) return;
    statsDays = Number(seg.dataset.days);
    loadStats();
  });

  /* ---------- 启动 ---------- */
  loadList();
  loadStats();

} };

