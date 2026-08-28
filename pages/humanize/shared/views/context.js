/**
 * View: Context — 上下文追踪页（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET context-runs / GET context-run / GET context-stats
 * 降级：api.js 未加载时停留在静态预览（页面骨架仍可见）。
 * 安全：所有持久化内容（content/raw_output/messages content/failure_detail 等）
 *       一律通过 textContent 写入，禁止拼入 innerHTML。
 */
HZ.views["context"] = { init: function () {

  HZ.topbars["context"] = {
    title: "上下文追踪",
    sub: "每次 LLM 请求的区块组装与 token 预算全记录",
    search: "搜索请求 ID、消息内容…",
    actions: [],
    onRefresh: loadList,
  };
HZ.renderTopbar(HZ.topbars["context"]);
  HZ.initReveal();

  /* 筛选 seg：切换高亮 + 重新加载列表 */
  document.querySelectorAll(".scope-seg").forEach((segGroup) => {
    segGroup.addEventListener("click", (e) => {
      const seg = e.target.closest(".seg-item");
      if (!seg || !seg.dataset.scope) return;
      segGroup.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
      current.scope = seg.dataset.scope;
      current.page = 1;
      loadList();
    });
  });

  /* 共享 API 层缺失：清空 mock 静态数据，显示明确错误提示（幂等，多次调用只插一个错误条） */
  function renderApiUnavailable() {
    ["#cxRunList", "#cxDetail"].forEach((sel) => {
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
    private: "私聊",
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

  /* ---------- 状态 ---------- */
  let current = { page: 1, scope: "" };
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
      if (n.querySelector("svg")) return; /* 幂等：已有图标不再插 */
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

  /* 列表卡片点击：事件委托到 listEl，重渲染不丢绑定 */
  listEl.addEventListener("click", (e) => {
    const card = e.target && e.target.closest ? e.target.closest(".cx-run") : null;
    if (!card) return;
    openDetail(card.dataset.requestId);
  });

  /* 分页点击：事件委托到 pagerEl，重渲染不丢绑定 */
  pagerEl.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest(".pg-btn") : null;
    if (!btn || btn.disabled) return;
    const target = btn.dataset.page;
    if (target === "prev") {
      if (current.page > 1) current.page -= 1;
    } else if (target === "next") {
      current.page += 1;
    } else {
      current.page = Number(target);
    }
    loadList();
  });

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

    /* 消息内容：优先显示 message_preview（当前消息区块预览），降级为 scope + sender */
    const msgPreview = item.message_preview ? String(item.message_preview) : "";
    const msgLine = el("div", "cx-run-msg");
    if (msgPreview) {
      msgLine.textContent = msgPreview;
      msgLine.title = msgPreview;
    } else {
      msgLine.textContent = scopeText(item) + " · 发送者 " + (item.sender_id || "—");
    }
    card.appendChild(msgLine);

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
    const reqSnapFinal = data.request_snapshot_final || {};
    const resSnap = data.response_snapshot || {};
    const protocol = resSnap.protocol || null;
    const response = data.response || null;
    const responseSeq = Array.isArray(data.response_sequence) ? data.response_sequence : [];
    const durationMs =
      (response && response.duration_ms) ||
      (protocol && protocol.duration_ms) ||
      (run.protocol_summary && run.protocol_summary.duration_ms) ||
      null;

    detailEl.innerHTML = "";
    detailEl.appendChild(headCardEl(run, durationMs, protocol));
    detailEl.appendChild(budgetCardEl(sections));
    detailEl.appendChild(sectionsCardEl(sections));
    /* 完整快照：提炼区（含消息/附件/图片转述/工具/思考/响应/实际发送/原始输出）
       + 完整原始 JSON，一张卡展示全部，避免与请求快照重复 */
    detailEl.appendChild(finalRawCardEl(run, reqSnapFinal, resSnap, protocol, response));
    if (responseSeq.length) {
      detailEl.appendChild(responseSeqCardEl(responseSeq));
    }
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
    const row = el("div", "cx-head-row");

    const main = el("div", "cx-head-main");
    const headId = el("div", "cx-head-id");
    headId.appendChild(el("span", null, "#" + shortId(run.request_id)));
    const tag = el("span", "tag tag-required", "protocol");
    headId.appendChild(tag);
    const copyBtn = el("button", "raw-copy", "复制 ID");
    copyBtn.dataset.icon = "copy";
    copyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      copyTextFallback(run.request_id || "", "已复制");
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
    const ps = run.protocol_summary;
    if (ps && ps.success && ps.action === "No Reply" && ps.no_reply_reason) {
      const m = el("span", "m", "不回复原因 " + ps.no_reply_reason);
      m.setAttribute("data-icon", "info");
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

    /* 原因：优先显示中文映射，未映射时保留原始 code */
    if (sec.reason) {
      const label = OMIT_REASON_LABEL[sec.reason] || sec.reason;
      const reason = el("div", "cx-sec-reason", "原因 · " + label);
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

  /* 复制 fallback：插件 iframe 无安全上下文，navigator.clipboard 不可用，
     改用 textarea + execCommand 选中复制；失败时提示手动复制。 */
  function copyTextFallback(text, okMsg) {
    if (!text) return;
    const done = () => toast(okMsg || "已复制", { type: "success" });
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { /* ignore */ }
      ta.remove();
      if (!ok) toast("复制失败，请手动选择文本复制", { type: "error" });
      else done();
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => { fallback(); });
    } else {
      fallback();
    }
  }

  /* 最终完整快照卡：上半区展示提炼后的重要数据（格式化+高亮），
     下半区展示完整原始 JSON（格式化+语法高亮），确保不丢失任何数据 */
  function finalRawCardEl(run, reqSnapFinal, resSnap, protocol, response) {
    const card = el("div", "card");
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "最终完整快照"));
    titleWrap.appendChild(el("span", "tag tag-required", "Agent 运行后 · 完整上下文"));
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
      copyTextFallback(finalRawAllText(run, reqSnapFinal, resSnap, protocol, response), "已复制全部");
    });
    head.appendChild(copyBtn);
    card.appendChild(head);

    const pr = reqSnapFinal.provider_request || {};
    const fields = pr.fields || {};
    const ctxMessages = Array.isArray(fields.contexts) ? fields.contexts : [];

    /* ---------- 上半区：提炼的重要数据 ---------- */
    const list = el("div", "raw-list");
    list.style.marginTop = "14px";
    let rendered = 0;

    /* 系统提示词 */
    const systemPrompt = fields.system_prompt ? String(fields.system_prompt) : "";
    if (systemPrompt) {
      list.appendChild(rawMsgEl({ role: "system", content: systemPrompt }, rendered));
      rendered += 1;
    }
    /* 完整上下文消息（persona / KB / 文件 / 工具注入均已包含） */
    ctxMessages.forEach((msg, i) => {
      if (msg && typeof msg === "object" && (msg.content || msg.tool_calls)) {
        list.appendChild(rawMsgEl({ role: msg.role || "user", content: msg.content || "", tool_calls: msg.tool_calls }, rendered));
        rendered += 1;
      }
    });
    /* 图片转述缓存（ImageCache）：独立区展示，不混入消息编号序列。
       ImageCache 序列化为 {text: "..."} 对象或纯字符串，统一用 imgCacheText() 提取。 */
    const imageCache = Array.isArray(pr.image_cache) ? pr.image_cache : [];
    if (imageCache.length) {
      list.appendChild(el("div", "raw-divider", "图片转述缓存 · " + imageCache.length + " 张"));
      imageCache.forEach((item, i) => {
        const text = imgCacheText(item);
        const row = el("div", "raw-img-cache");
        row.appendChild(el("span", "raw-img-cache-idx", "图片 " + (i + 1)));
        row.appendChild(el("span", "raw-img-cache-text", text || "（无转述文本）"));
        list.appendChild(row);
      });
    }
    /* 临时注入区块（extra_user_content_parts，与请求钩子时的原始上下文对齐） */
    const extraParts = Array.isArray(fields.extra_user_content_parts) ? fields.extra_user_content_parts : [];
    extraParts.forEach((part) => {
      const text = part && typeof part === "object" ? contentToText(part) : String(part);
      if (text) {
        list.appendChild(rawMsgEl({ role: "temp_user", content: text }, rendered));
        rendered += 1;
      }
    });
    /* 请求钩子时的原始 prompt（中间快照独有，并入最终卡避免重复卡） */
    const prompt = fields.prompt ? String(fields.prompt) : "";
    if (prompt) {
      list.appendChild(rawMsgEl({ role: "user", content: prompt }, rendered));
      rendered += 1;
    }
    /* 图片 / 音频附件 */
    const imageUrls = Array.isArray(fields.image_urls) ? fields.image_urls : [];
    const audioUrls = Array.isArray(fields.audio_urls) ? fields.audio_urls : [];
    if (imageUrls.length || audioUrls.length) {
      list.appendChild(el("div", "raw-divider", "媒体附件"));
      imageUrls.forEach((u, i) => {
        list.appendChild(el("div", "raw-msg", "[图片 " + (i + 1) + "] " + String(u)));
      });
      audioUrls.forEach((u, i) => {
        list.appendChild(el("div", "raw-msg", "[音频 " + (i + 1) + "] " + String(u)));
      });
    }
    /* 工具 schema — 整个区块默认折叠，点击分隔行标题展开/收起 */
    const funcTool = fields.func_tool || null;
    if (funcTool && Array.isArray(funcTool.tools) && funcTool.tools.length) {
      const toolDivider = el("div", "raw-divider");
      const toolSectionBtn = el("button", "raw-collapse", "展开");
      const paintToolSectionIcon = () => {
        toolSectionBtn.querySelectorAll("svg").forEach((s) => s.remove());
        toolSectionBtn.insertAdjacentHTML("afterbegin", HZ.icon(toolSectionBtn.dataset.icon));
      };
      toolSectionBtn.dataset.icon = "arrow-down";
      paintToolSectionIcon();
      toolDivider.appendChild(document.createTextNode("工具 " + funcTool.tools.length + " 个"));
      toolDivider.appendChild(toolSectionBtn);
      list.appendChild(toolDivider);

      const toolsContainer = el("div", "raw-tools-block raw-tools-block-collapsed");
      funcTool.tools.forEach((t) => {
        /* openai_schema 结构：{type, function: {name, description, parameters}} */
        const fn = (t && t.function) || t || {};
        const name = (fn && fn.name) || (t && t.name) || "?";
        const desc = (fn && fn.description) ? String(fn.description) : "";
        const params = (fn && fn.parameters) ? fn.parameters : null;
        const row = el("div", "raw-msg tool-call collapsed");
        const rowHead = el("div", "raw-msg-head");
        rowHead.appendChild(el("span", "raw-idx", "[T]"));
        rowHead.appendChild(el("span", "raw-role tool", String(name)));
        if (desc) rowHead.appendChild(el("span", "raw-len", desc.length + " 字"));
        const toolBtn = el("button", "raw-collapse", "展开");
        const paintToolIcon = () => {
          toolBtn.querySelectorAll("svg").forEach((s) => s.remove());
          toolBtn.insertAdjacentHTML("afterbegin", HZ.icon(toolBtn.dataset.icon));
        };
        toolBtn.dataset.icon = "arrow-down";
        paintToolIcon();
        rowHead.appendChild(toolBtn);
        toolBtn.addEventListener("click", () => {
          const collapsed = row.classList.toggle("collapsed");
          toolBtn.textContent = collapsed ? "展开" : "收起";
          toolBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
          paintToolIcon();
        });
        row.appendChild(rowHead);
        if (desc) row.appendChild(el("div", "raw-body", desc));
        if (params) {
          let paramsText = "";
          try {
            paramsText = JSON.stringify(params, null, 2);
          } catch (e) {
            paramsText = String(params);
          }
          const paramsWrap = el("div");
          paramsWrap.style.marginTop = "6px";
          paramsWrap.style.fontSize = "12px";
          try {
            paramsWrap.appendChild(highlightJsonText(paramsText));
          } catch (e) {
            paramsWrap.appendChild(el("div", "raw-body", paramsText));
          }
          row.appendChild(paramsWrap);
        }
        toolsContainer.appendChild(row);
      });
      toolSectionBtn.addEventListener("click", () => {
        const collapsed = toolsContainer.classList.toggle("raw-tools-block-collapsed");
        toolSectionBtn.textContent = collapsed ? "展开" : "收起";
        toolSectionBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintToolSectionIcon();
      });
      list.appendChild(toolsContainer);
    }

    /* 模型思考过程 */
    const reasoning = extractReasoning(pr, resSnap, protocol, response);
    if (reasoning) {
      list.appendChild(el("div", "raw-divider", "模型思考过程"));
      const rBody = el("div", "raw-body reasoning", reasoning);
      rBody.style.color = "var(--muted)";
      list.appendChild(rBody);
    }

    /* 最终响应 */
    const completion = extractCompletion(pr, resSnap, protocol, response);
    if (completion) {
      list.appendChild(el("div", "raw-divider", "最终响应"));
      list.appendChild(el("div", "raw-body", completion));
    }

    /* 实际发送消息：协议解析后的干净文本（用户真正收到的） */
    const sentMessages = Array.isArray(response && response.messages)
      ? response.messages
      : (protocol && Array.isArray(protocol.messages) ? protocol.messages : null);
    if (sentMessages) {
      list.appendChild(el("div", "raw-divider", "实际发送"));
      sentMessages.forEach((text, i) => {
        const item = el("div", "raw-msg sent");
        const itemHead = el("div", "raw-msg-head");
        itemHead.appendChild(el("span", "raw-idx", "[" + (i + 1) + "]"));
        itemHead.appendChild(el("span", "raw-role assistant", "assistant"));
        itemHead.appendChild(el("span", "raw-len", String(text).length + " 字"));
        item.appendChild(itemHead);
        item.appendChild(el("div", "raw-body", String(text)));
        list.appendChild(item);
      });
    }

    /* 模型原始输出（协议原文，含标签），默认折叠 */
    if (protocol && protocol.raw_output) {
      list.appendChild(el("div", "raw-divider", "模型原始输出"));
      const rawBlock = el("div", "raw-raw collapsed");
      const rawHead = el("div", "raw-msg-head");
      rawHead.appendChild(el("span", "raw-idx", "[O]"));
      rawHead.appendChild(el("span", "raw-role", "原文"));
      rawHead.appendChild(el("span", "raw-len", String(protocol.raw_output).length + " 字"));
      const rawBtn = el("button", "raw-collapse", "展开");
      const paintRawIcon = () => {
        rawBtn.querySelectorAll("svg").forEach((s) => s.remove());
        rawBtn.insertAdjacentHTML("afterbegin", HZ.icon(rawBtn.dataset.icon));
      };
      rawBtn.dataset.icon = "arrow-down";
      paintRawIcon();
      rawHead.appendChild(rawBtn);
      rawBtn.addEventListener("click", () => {
        const collapsed = rawBlock.classList.toggle("collapsed");
        rawBtn.textContent = collapsed ? "展开" : "收起";
        rawBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintRawIcon();
      });
      rawBlock.appendChild(rawHead);
      rawBlock.appendChild(el("div", "raw-body", String(protocol.raw_output)));
      list.appendChild(rawBlock);
    }

    if (!rendered && !reasoning && !completion) {
      list.appendChild(el("div", "cx-empty", "暂无可展示的最终快照（此请求可能未完成 Agent 运行）"));
    }
    card.appendChild(list);

    /* ---------- 下半区：完整原始 JSON（格式化 + 语法高亮） ---------- */
    let rawJsonText = "";
    try {
      rawJsonText = JSON.stringify(pr, null, 2);
    } catch (e) {
      rawJsonText = String(pr);
    }
    if (rawJsonText) {
      /* 完整原始 JSON — 默认折叠，避免冗长内容占满视图 */
      const jsonDivider = el("div", "raw-divider");
      jsonDivider.appendChild(document.createTextNode("完整原始 JSON"));
      const jsonToggleBtn = el("button", "raw-collapse", "展开");
      const paintJsonIcon = () => {
        jsonToggleBtn.querySelectorAll("svg").forEach((s) => s.remove());
        jsonToggleBtn.insertAdjacentHTML("afterbegin", HZ.icon(jsonToggleBtn.dataset.icon));
      };
      jsonToggleBtn.dataset.icon = "arrow-down";
      paintJsonIcon();
      jsonDivider.appendChild(jsonToggleBtn);
      card.appendChild(jsonDivider);
      const rawBox = el("div", "raw-body raw-json raw-json-collapsed");
      try {
        rawBox.appendChild(highlightJsonText(rawJsonText));
      } catch (e) {
        rawBox.textContent = rawJsonText;
      }
      jsonToggleBtn.addEventListener("click", () => {
        const collapsed = rawBox.classList.toggle("raw-json-collapsed");
        jsonToggleBtn.textContent = collapsed ? "展开" : "收起";
        jsonToggleBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintJsonIcon();
      });
      card.appendChild(rawBox);
    }
    return card;
  }

  /* 从最终快照 / 响应快照 / 响应序列中提取思考过程 */
  function extractReasoning(pr, resSnap, protocol, response) {
    /* 新结构：provider_request_final 顶层 reasoning 字段 */
    if (pr && typeof pr === "object" && pr.reasoning) return String(pr.reasoning);
    const fields = (pr && pr.fields) || {};
    if (Array.isArray(fields.contexts)) {
      for (const m of fields.contexts) {
        if (m && typeof m === "object" && Array.isArray(m.content)) {
          for (const part of m.content) {
            if (part && part.type === "think" && part.text) return String(part.text);
          }
        }
      }
    }
    const resFields = ((resSnap && resSnap.llm_response && resSnap.llm_response.fields) || {});
    const reasoning =
      resFields.reasoning_content ||
      (resFields.reasoning && (typeof resFields.reasoning === "string" ? resFields.reasoning : (resFields.reasoning.fields && resFields.reasoning.fields.text))) ||
      "";
    if (reasoning) return String(reasoning);
    if (response && response.reasoning_content) return String(response.reasoning_content);
    return "";
  }

  /* 从最终快照 / 响应快照 / 响应序列中提取最终完成文本 */
  function extractCompletion(pr, resSnap, protocol, response) {
    /* 新结构：provider_request_final 内嵌 response */
    if (pr && typeof pr === "object" && pr.response && typeof pr.response === "object") {
      const rFields = pr.response.fields || {};
      const direct =
        rFields._completion_text ||
        rFields.completion_text ||
        (pr.response.completion_text);
      if (direct) return String(direct);
    }
    const resFields = ((resSnap && resSnap.llm_response && resSnap.llm_response.fields) || {});
    const completion =
      resFields._completion_text ||
      resFields.completion_text ||
      "";
    if (completion) return String(completion);
    if (response && response.messages && Array.isArray(response.messages)) {
      return response.messages.map(String).join("\n");
    }
    return "";
  }

  function finalRawAllText(run, reqSnapFinal, resSnap, protocol, response) {
    const parts = [];
    const pr = reqSnapFinal.provider_request || {};
    const fields = pr.fields || {};
    const systemPrompt = fields.system_prompt ? String(fields.system_prompt) : "";
    if (systemPrompt) parts.push("[system]\n" + systemPrompt);
    const ctxMessages = Array.isArray(fields.contexts) ? fields.contexts : [];
    ctxMessages.forEach((m) => {
      if (m && typeof m === "object") {
        parts.push("[" + (m.role || "user") + "]\n" + contentToText(m.content));
      }
    });
    const imageUrls = Array.isArray(fields.image_urls) ? fields.image_urls : [];
    imageUrls.forEach((u, i) => parts.push("[image " + (i + 1) + "]\n" + String(u)));
    const audioUrls = Array.isArray(fields.audio_urls) ? fields.audio_urls : [];
    audioUrls.forEach((u, i) => parts.push("[audio " + (i + 1) + "]\n" + String(u)));
    const imageCache = Array.isArray(pr.image_cache) ? pr.image_cache : [];
    imageCache.forEach((item, i) => parts.push("[image_transcription " + (i + 1) + "]\n" + imgCacheText(item)));
    const reasoning = extractReasoning(pr, resSnap, protocol, response);
    if (reasoning) parts.push("[reasoning]\n" + reasoning);
    const completion = extractCompletion(pr, resSnap, protocol, response);
    if (completion) parts.push("[response]\n" + completion);
    return parts.join("\n\n");
  }

  /* ---- imgCacheText: 从 ImageCache 序列化项中安全提取纯文本 ----
     ImageCache 是 Python dataclass {text: str}，JSON 序列化后为对象；
     也可能已经是字符串（旧数据或直接序列化），统一兜底。 */
  function imgCacheText(item) {
    if (item == null) return "";
    if (typeof item === "string") return item;
    if (typeof item === "object") {
      const t = item.text || item.description || item.content || "";
      if (t) return String(t);
      try { return JSON.stringify(item); } catch (e) { return String(item); }
    }
    return String(item);
  }

  /* 原始上下文卡（已废弃，保留符号引用检查用；renderDetail 不再调用） */
  function rawCardEl(run, reqSnap, resSnap, protocol, response) {
    const card = el("div", "card");

    /* 头部 */
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "原始上下文"));
    titleWrap.appendChild(el("span", "tag tag-src", "请求钩子时 · 对比用"));
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
      copyTextFallback(rawAllText(run, reqSnap, resSnap, protocol, response), "已复制全部");
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

    /* 消息列表：历史上下文（fields.contexts）+ 当前 prompt + system_prompt */
    const list = el("div", "raw-list");
    list.style.marginTop = "14px";
    const pr = reqSnap.provider_request || {};
    const fields = pr.fields || {};
    const ctxMessages = Array.isArray(fields.contexts) ? fields.contexts : [];
    const extraParts = Array.isArray(fields.extra_user_content_parts) ? fields.extra_user_content_parts : [];
    const systemPrompt = fields.system_prompt ? String(fields.system_prompt) : "";
    const prompt = fields.prompt ? String(fields.prompt) : "";
    let rendered = 0;
    if (systemPrompt) {
      list.appendChild(rawMsgEl({ role: "system", content: systemPrompt }, rendered));
      rendered += 1;
    }
    ctxMessages.forEach((msg, i) => {
      if (msg && typeof msg === "object" && msg.content) {
        list.appendChild(rawMsgEl({ role: msg.role || "user", content: msg.content }, rendered));
        rendered += 1;
      }
    });
    extraParts.forEach((part) => {
      const text = part && typeof part === "object" ? contentToText(part) : String(part);
      if (text) {
        list.appendChild(rawMsgEl({ role: "temp_user", content: text }, rendered));
        rendered += 1;
      }
    });
    if (prompt) {
      list.appendChild(rawMsgEl({ role: "user", content: prompt }, rendered));
      rendered += 1;
    }
    if (!rendered) {
      const empty = el("div", "cx-empty", "请求快照中无消息记录");
      empty.style.padding = "20px 16px";
      list.appendChild(empty);
    }

    /* 实际发送消息：协议解析后的干净文本（用户真正收到的） */
    const sentMessages = Array.isArray(response && response.messages)
      ? response.messages
      : (protocol && Array.isArray(protocol.messages) ? protocol.messages : null);
    if (sentMessages) {
      list.appendChild(el("div", "raw-divider", "实际发送"));
      sentMessages.forEach((text, i) => {
        const item = el("div", "raw-msg sent");
        const head = el("div", "raw-msg-head");
        head.appendChild(el("span", "raw-idx", "[" + (i + 1) + "]"));
        const role = el("span", "raw-role assistant", "assistant");
        head.appendChild(role);
        head.appendChild(el("span", "raw-len", String(text).length + " 字"));
        item.appendChild(head);
        item.appendChild(el("div", "raw-body", String(text)));
        list.appendChild(item);
      });
    }

    /* 模型原始输出（协议原文，含标签），默认折叠 */
    if (protocol && protocol.raw_output) {
      list.appendChild(el("div", "raw-divider", "模型原始输出"));
      const rawBlock = el("div", "raw-raw collapsed");
      const rawHead = el("div", "raw-msg-head");
      rawHead.appendChild(el("span", "raw-idx", "[O]"));
      rawHead.appendChild(el("span", "raw-role", "原文"));
      rawHead.appendChild(el("span", "raw-len", String(protocol.raw_output).length + " 字"));
      const rawBtn = el("button", "raw-collapse", "展开");
      const paintRawIcon = () => {
        rawBtn.querySelectorAll("svg").forEach((s) => s.remove());
        rawBtn.insertAdjacentHTML("afterbegin", HZ.icon(rawBtn.dataset.icon));
      };
      rawBtn.dataset.icon = "arrow-down";
      paintRawIcon();
      rawHead.appendChild(rawBtn);
      rawBtn.addEventListener("click", () => {
        const collapsed = rawBlock.classList.toggle("collapsed");
        rawBtn.textContent = collapsed ? "展开" : "收起";
        rawBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintRawIcon();
      });
      rawBlock.appendChild(rawHead);
      const rawBody = el("div", "raw-body", String(protocol.raw_output));
      rawBlock.appendChild(rawBody);
      list.appendChild(rawBlock);
    }
    card.appendChild(list);
    return card;
  }

  /* 把 JSON 文本转成带语法高亮的 DOM 节点（安全：只用 textContent 填内容） */
  function highlightJsonText(text) {
    const container = document.createElement("div");
    container.className = "raw-body";
    const tokenRe = /("(?:\\.|[^"\\])*"|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}[\],:])/g;
    let lastIndex = 0;
    let match;
    while ((match = tokenRe.exec(text)) !== null) {
      if (match.index > lastIndex) {
        container.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const token = match[0];
      const after = text.slice(tokenRe.lastIndex).replace(/^\s*/, "");
      const isKey = token.startsWith("\"") && after.startsWith(":");
      const span = document.createElement("span");
      if (isKey) {
        span.className = "jq-key";
      } else if (token.startsWith("\"")) {
        span.className = "jq-str";
      } else if (token === "true" || token === "false") {
        span.className = "jq-bool";
      } else if (token === "null") {
        span.className = "jq-null";
      } else if (/^-?\d/.test(token)) {
        span.className = "jq-num";
      } else {
        span.className = "jq-punc";
      }
      span.textContent = token;
      container.appendChild(span);
      lastIndex = tokenRe.lastIndex;
    }
    if (lastIndex < text.length) {
      container.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    return container;
  }

  /* 把消息 content（字符串或 parts 数组）转成可读文本 */
  function contentToText(content) {
    if (content == null) return "";
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      const parts = [];
      content.forEach((part) => {
        if (!part || typeof part !== "object") return;
        const type = part.type || "";
        if (type === "text") {
          const t = String(part.text || "");
          if (t) parts.push(t);
        } else if (type === "image_url") {
          parts.push("[图片]");
        } else if (type === "audio_url") {
          parts.push("[音频]");
        } else if (type === "think") {
          const t = String(part.text || "");
          if (t) parts.push("[思考] " + t);
        } else if (type) {
          parts.push("[" + type + "]");
        }
      });
      return parts.join("\n");
    }
    if (typeof content === "object") {
      try {
        return JSON.stringify(content, null, 2);
      } catch (e) {
        return String(content);
      }
    }
    return String(content);
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
    const content = contentToText(msg.content);
    head.appendChild(el("span", "raw-len", content.length + " 字"));
    const long = content.length > 300;
    /* 长消息默认折叠（190px 截断），按钮展开/收起；短消息无按钮 */
    if (long) box.classList.add("collapsed");
    if (long) {
      const collapseBtn = el("button", "raw-collapse", "展开");
      /* 直接插图标（不依赖 injectIcons 时机），点击切换时重插 */
      const paintIcon = () => {
        collapseBtn.querySelectorAll("svg").forEach((s) => s.remove());
        collapseBtn.insertAdjacentHTML("afterbegin", HZ.icon(collapseBtn.dataset.icon));
      };
      collapseBtn.dataset.icon = "arrow-down";
      paintIcon();
      head.appendChild(collapseBtn);
      collapseBtn.addEventListener("click", () => {
        const collapsed = box.classList.toggle("collapsed");
        collapseBtn.textContent = collapsed ? "展开" : "收起";
        collapseBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintIcon();
      });
    }
    box.appendChild(head);
    const body = el("div", "raw-body", content);
    box.appendChild(body);
    /* 工具调用展示（最终快照中的 assistant tool_calls） */
    if (Array.isArray(msg.tool_calls) && msg.tool_calls.length) {
      const toolBox = el("div", "raw-tool-calls");
      msg.tool_calls.forEach((call) => {
        if (!call || typeof call !== "object") return;
        const fn = call.function || {};
        const name = (fn && fn.name) || call.name || "?";
        let argsText = (fn && fn.arguments) || call.arguments || "";
        /* OpenAI 协议：arguments 是 JSON 字符串（中文可能被 \uXXXX 转义），
           先解析成对象再美化，避免显示双重转义原文 */
        if (typeof argsText === "string" && argsText.trim()) {
          try { argsText = JSON.parse(argsText); } catch (e) { /* 保持原样 */ }
        }
        if (argsText && typeof argsText !== "string") {
          try { argsText = JSON.stringify(argsText, null, 2); } catch (e) { argsText = String(argsText); }
        }
        const row = el("div", "raw-msg tool-call");
        row.appendChild(el("span", "raw-role", String(name)));
        if (argsText) {
          /* JSON 参数带语法高亮 */
          try {
            row.appendChild(highlightJsonText(String(argsText)));
          } catch (e) {
            row.appendChild(el("div", "raw-body", String(argsText)));
          }
        }
        toolBox.appendChild(row);
      });
      box.appendChild(toolBox);
    }
    return box;
  }

  function responseSeqCardEl(seq) {
    const card = el("div", "card");
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "响应序列"));
    titleWrap.appendChild(el("span", "tag tag-src", seq.length + " 轮"));
    head.appendChild(titleWrap);
    card.appendChild(head);

    const list = el("div", "raw-list");
    list.style.marginTop = "14px";
    seq.forEach((item, i) => {
      /* 每轮用 .raw-seq-turn 包裹，确保视觉分隔和失败状态样式正确施加 */
      const block = el("div", "raw-seq-turn" + (item.success ? "" : " failed"));

      /* 顶行：轮次号 + 阶段角色标签 + 成功/失败标签 + 协议动作 */
      const headRow = el("div", "raw-msg-head");
      headRow.appendChild(el("span", "raw-idx", "[" + (i + 1) + "]"));
      headRow.appendChild(el("span", "raw-role " + (item.stage === "final" ? "assistant" : "tool"),
        item.stage === "final" ? "final" : (item.stage || "tool")));
      headRow.appendChild(el("span", "tag " + (item.success ? "tag-ok" : "tag-failed"),
        item.success ? "OK" : (item.failure_code || "失败")));
      if (item.action) headRow.appendChild(el("span", "tag tag-src", item.action));
      block.appendChild(headRow);

      /* 快照详情 */
      const snap = item.snapshot || {};
      const fields = (snap.fields || {});
      const snapResp = snap.final_response && snap.final_response.response ? snap.final_response.response : null;
      const snapFields = snapResp ? (snapResp.fields || {}) : fields;

      /* 完成文本 */
      const completion =
        (snapFields._completion_text || snapFields.completion_text || "") ||
        (snap.completion_text || "");
      if (completion) {
        block.appendChild(el("div", "d-label", "完成文本"));
        block.appendChild(el("div", "raw-body", String(completion)));
      }

      /* 思考过程 */
      const reasoning = snapFields.reasoning_content || "";
      if (reasoning) {
        block.appendChild(el("div", "d-label", "思考过程"));
        const rBody = el("div", "raw-body reasoning", String(reasoning));
        rBody.style.color = "var(--muted)";
        block.appendChild(rBody);
      }

      /* 工具调用：rowHead 作为 row 的子节点（修复原先错误地直接 append 到 list） */
      const toolNames = snapFields.tools_call_name || [];
      const toolArgs = snapFields.tools_call_args || [];
      if (toolNames && toolNames.length) {
        block.appendChild(el("div", "d-label", "工具调用"));
        const names = Array.isArray(toolNames) ? toolNames : [toolNames];
        const args = Array.isArray(toolArgs) ? toolArgs : toolArgs ? [toolArgs] : [];
        names.forEach((name, ti) => {
          const row = el("div", "raw-msg tool-call");
          const rowHead = el("div", "raw-msg-head");
          rowHead.appendChild(el("span", "raw-role", String(name)));
          row.appendChild(rowHead);  /* 修复：rowHead 进 row，不直接进 list */
          if (args[ti] != null) {
            let argText = args[ti];
            if (typeof argText !== "string") {
              try { argText = JSON.stringify(argText, null, 2); } catch (e) { argText = String(argText); }
            }
            try {
              row.appendChild(highlightJsonText(String(argText)));
            } catch (e) {
              row.appendChild(el("div", "raw-body", String(argText)));
            }
          }
          block.appendChild(row);
        });
      }

      /* Token 用量 */
      const usage = snapFields.usage || {};
      const usageFields = usage.fields || usage;
      if (usageFields && (usageFields.input_cached !== undefined || usageFields.input_other !== undefined || usageFields.output !== undefined)) {
        block.appendChild(el("div", "d-label", "Tokens"));
        block.appendChild(el("span", "tag tag-src",
          "缓存 " + (usageFields.input_cached ?? 0) +
          " · 输入 " + (usageFields.input_other ?? 0) +
          " · 输出 " + (usageFields.output ?? 0)));
      }

      /* 协议消息 */
      if (item.messages && item.messages.length) {
        block.appendChild(el("div", "d-label", "发送消息"));
        item.messages.forEach((m) => {
          const row = el("div", "raw-msg");
          row.appendChild(el("div", "raw-body", String(m)));
          block.appendChild(row);
        });
      }

      /* 原始输出（默认折叠） */
      if (item.raw_output) {
        const rawBlock = el("div", "raw-raw collapsed");
        const rawHead = el("div", "raw-msg-head");
        rawHead.appendChild(el("span", "raw-idx", "[O]"));
        rawHead.appendChild(el("span", "raw-role", "原始输出"));
        rawHead.appendChild(el("span", "raw-len", String(item.raw_output).length + " 字"));
        const rawBtn = el("button", "raw-collapse", "展开");
        const paintRawIcon = () => {
          rawBtn.querySelectorAll("svg").forEach((s) => s.remove());
          rawBtn.insertAdjacentHTML("afterbegin", HZ.icon(rawBtn.dataset.icon));
        };
        rawBtn.dataset.icon = "arrow-down";
        paintRawIcon();
        rawHead.appendChild(rawBtn);
        rawBtn.addEventListener("click", () => {
          const collapsed = rawBlock.classList.toggle("collapsed");
          rawBtn.textContent = collapsed ? "展开" : "收起";
          rawBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
          paintRawIcon();
        });
        rawBlock.appendChild(rawHead);
        rawBlock.appendChild(el("div", "raw-body", String(item.raw_output)));
        block.appendChild(rawBlock);
      }

      list.appendChild(block);  /* 修复：block 最终 append 到 list */
    });
    card.appendChild(list);
    return card;
  }

  /* rawAllText 仅供 rawCardEl 内部使用（rawCardEl 已废弃），保留函数体避免运行时引用错误 */
  function rawAllText(run, reqSnap, resSnap, protocol, response) { return ""; }

  /* ---------- 启动 ---------- */
  loadList().then(() => {
    /* 默认展示最新一条运行详情 */
    const first = listEl && listEl.querySelector(".cx-run");
    if (first) openDetail(first.dataset.requestId);
  });

} };

