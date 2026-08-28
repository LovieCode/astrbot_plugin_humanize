/**
 * View: Context — 上下文追踪页
 *
 * 设计目标：还原"AI 实际看到的输入"——按真实发送顺序展示请求中各段
 * （系统 / 历史 / 当前用户消息 / 注入的临时区块），把"组装的 5 个区块"
 * 的抽象视为同义信息，不再单列。
 *
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET context-runs / GET context-run / GET context-stats
 *
 * 安全：所有持久化内容（content / raw_output / failure_detail / 消息体
 * 等）一律通过 textContent 写入，禁止拼入 innerHTML。
 */
(function () {
  HZ.renderSidebar("context");
  HZ.topbars["context"] = {
    title: "上下文追踪",
    sub: "还原每次 LLM 请求里 AI 实际看到的输入与产出",
    search: "搜索请求 ID、消息内容…",
    actions: [],
    onRefresh: loadList,
  };
  HZ.renderTopbar(HZ.topbars["context"]);
  HZ.initReveal();

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

  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));
  const initEmpty = HZ.initEmpty;
  const initErrbar = HZ.initErrbar;
  const fmtAgo = (iso) => (api.ago ? api.ago(iso) : String(iso || ""));
  const fmtTime = (iso) => (api.time ? api.time(iso) : String(iso || ""));

  /* ---------- 常量 ---------- */
  const PAGE_SIZE = 8;
  const SCOPE_LABEL = {
    group: "群聊",
    private: "私聊",
    private_user: "私聊",
    group_member: "群成员",
  };
  const SECTION_LABEL = {
    current_message: "当前消息",
    known_terms: "黑话",
    memory_context: "记忆",
    reply_examples: "回复样例",
    response_protocol: "回复协议",
  };
  // 6 字母简称用于栈条/侧标
  const SECTION_SHORT = {
    current_message: "用户",
    known_terms: "黑话",
    memory_context: "记忆",
    reply_examples: "样例",
    response_protocol: "协议",
  };
  // 区块颜色（与 token 条 / 角色色一致）
  const SECTION_COLOR = {
    current_message: "var(--blue)",
    known_terms: "var(--violet)",
    memory_context: "var(--green)",
    reply_examples: "var(--amber)",
    response_protocol: "var(--pink)",
  };
  // 省略原因中文（未映射时保留原 code 即可）
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
  const MODE_LABEL = { both: "两者", temp_user: "临时用户", user: "用户" };

  /* ---------- DOM 工具 ---------- */
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const listEl = $("#cxRunList");
  const pagerEl = $("#cxPager");
  const detailEl = $("#cxDetail");
  let current = { page: 1, scope: "" };
  let detailRequestId = null;
  let busy = false;
  let detailBusy = false;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function injectIcons(root) {
    root.querySelectorAll("[data-icon]").forEach((n) => {
      if (n.querySelector("svg")) return;
      const svg = HZ.icon(n.dataset.icon);
      // 仅对纯图标占位（无文本/无子节点）做整体替换；其余场景只追加图标、保留内容
      const hasOwnContent =
        (n.textContent && n.textContent.trim()) ||
        n.children.length > 0;
      if (hasOwnContent) {
        n.insertAdjacentHTML("afterbegin", svg);
      } else {
        n.innerHTML = svg;
      }
    });
  }
  function fmtNum(v) {
    return v == null ? "—" : Number(v).toLocaleString("zh-CN");
  }
  function shortId(id) {
    return String(id == null ? "" : id).slice(0, 8);
  }
  function scopeText(scopeType) {
    return SCOPE_LABEL[scopeType] || scopeType || "未知";
  }
  function modeLabel(m) {
    return MODE_LABEL[m] || m || "—";
  }
  function sectionLabel(key) {
    return SECTION_LABEL[key] || key || "未知区块";
  }
  function reasonLabel(code) {
    return OMIT_REASON_LABEL[code] || code || "";
  }
  function previewText(text, limit) {
    const t = String(text || "");
    if (t.length <= limit) return { text: t, truncated: false };
    return { text: t.slice(0, limit) + "…", truncated: true };
  }

  /* ---------- 协议结果标签 ---------- */
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
      if (ps.action === "No Reply") {
        tag.textContent = "未回复";
        if (ps.no_reply_reason) tag.title = "原因 · " + ps.no_reply_reason;
      } else {
        tag.textContent = "已回复";
        if (ps.model) tag.title = "模型 · " + ps.model;
      }
    } else {
      tag.classList.add("tag-failed");
      tag.textContent = "失败";
      if (ps.failure_code) tag.title = ps.failure_code;
    }
    return tag;
  }

  /* ---------- 复制 fallback ---------- */
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
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
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

  listEl.addEventListener("click", (e) => {
    const card = e.target && e.target.closest ? e.target.closest(".cx-run") : null;
    if (!card) return;
    openDetail(card.dataset.requestId);
  });
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

    const top = el("div", "cx-run-top");
    top.appendChild(el("span", "cx-run-id", "#" + shortId(item.request_id)));
    top.appendChild(protocolTagEl(item.protocol_summary));
    top.appendChild(el("span", "cx-run-time", item.created_at ? fmtAgo(item.created_at) : ""));
    card.appendChild(top);

    const preview = item.message_preview ? String(item.message_preview) : "";
    const msgLine = el("div", "cx-run-msg");
    if (preview) {
      msgLine.textContent = preview;
      msgLine.title = preview;
    } else {
      msgLine.textContent = scopeText(item.scope_type) + " · 发送者 " + (item.sender_id || "—");
    }
    card.appendChild(msgLine);

    const meta = el("div", "cx-run-meta");
    const tok = el("span", "cx-tokens", fmtNum(item.estimated_tokens) + " tok");
    tok.setAttribute("data-icon", "spark");
    meta.appendChild(tok);

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
    inc.appendChild(document.createTextNode(`${included}/${total} 区块`));
    meta.appendChild(inc);
    card.appendChild(meta);
    injectIcons(card);
    return card;
  }

  function renderPager(data) {
    const total = data.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    let html =
      '<button class="pg-btn" data-page="prev"' + (current.page <= 1 ? " disabled" : "") + ">" +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg></button>';
    for (let p = 1; p <= totalPages; p++) {
      html += '<button class="pg-btn' + (p === current.page ? " active" : "") + '" data-page="' + p + '">' + p + "</button>";
    }
    html +=
      '<button class="pg-btn" data-page="next"' + (current.page >= totalPages ? " disabled" : "") + ">" +
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
    const sections = (data.sections || []).slice().sort(
      (a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0)
    );
    const reqSnap = data.request_snapshot || {};
    const resSnap = data.response_snapshot || {};
    const protocol = resSnap.protocol || null;
    const responseSeq = Array.isArray(data.response_sequence) ? data.response_sequence : [];
    // 主展示对象：优先 final 阶段，其次 response，否则 protocol
    const response =
      responseSeq.find((r) => r.stage === "final") ||
      data.response ||
      null;
    const durationMs =
      (response && response.duration_ms) ||
      (protocol && protocol.duration_ms) ||
      (run.protocol_summary && run.protocol_summary.duration_ms) ||
      null;

    detailEl.innerHTML = "";
    detailEl.appendChild(overviewCardEl(run, durationMs, protocol, response));
    detailEl.appendChild(aiInputCardEl(run, reqSnap, sections));
    detailEl.appendChild(modelOutputCardEl(run, response, protocol, responseSeq));
    detailEl.appendChild(debugRawCardEl(reqSnap));
    injectIcons(detailEl);
  }
  /* =============================================================
   * 概览胶囊：协议结果 / 基础信息 / token 摘要
   * ============================================================= */
  function statCell(num, label, cls) {
    const stat = el("div", "cx-stat");
    stat.appendChild(el("span", "cx-stat-num" + (cls ? " " + cls : ""), num));
    stat.appendChild(el("span", "cx-stat-lab", label));
    return stat;
  }

  function overviewCardEl(run, durationMs, protocol, response) {
    const card = el("div", "card cx-head-card");
    const row = el("div", "cx-head-row");

    const main = el("div", "cx-head-main");
    const headId = el("div", "cx-head-id");
    headId.appendChild(el("span", null, "#" + shortId(run.request_id)));
    const ps = run.protocol_summary;
    if (ps) {
      const tag = protocolTagEl(ps);
      headId.appendChild(tag);
    } else if (protocol || response) {
      // Fall back to the in-detail protocol/response
      const fallback = el("span", "tag");
      if (response && response.success) {
        fallback.classList.add("tag-reply");
        fallback.textContent = response.action === "No Reply" ? "未回复" : "已回复";
      } else if (protocol) {
        fallback.classList.add(protocol.success ? "tag-reply" : "tag-failed");
        fallback.textContent = protocol.success
          ? (protocol.action === "No Reply" ? "未回复" : "已回复")
          : "失败";
      } else {
        fallback.classList.add("tag-noreply");
        fallback.textContent = "—";
      }
      headId.appendChild(fallback);
    }
    const copyBtn = el("button", "raw-copy", "复制 ID");
    copyBtn.dataset.icon = "copy";
    copyBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      copyTextFallback(run.request_id || "", "已复制请求 ID");
    });
    headId.appendChild(copyBtn);
    main.appendChild(headId);

    const sub = el("div", "cx-head-sub");
    sub.appendChild(chipEl("chat", scopeText(run.scope_type) + (run.scope_id ? " · " + shortenScope(run.scope_id) : "")));
    if (run.sender_id) sub.appendChild(chipEl("shield", "发送者 " + run.sender_id));
    if (run.message_id) sub.appendChild(chipEl("file", "消息 " + shortId(run.message_id)));
    if (run.protocol_mode) sub.appendChild(chipEl("layers", "注入模式 " + modeLabel(run.protocol_mode)));
    if (run.created_at) sub.appendChild(chipEl("clock", fmtTime(run.created_at)));
    main.appendChild(sub);
    row.appendChild(main);

    const stats = el("div", "cx-head-stats");
    stats.appendChild(statCell(fmtNum(run.estimated_tokens), "估算词元", "pink"));
    stats.appendChild(statCell(String(run.included_sections || 0), "注入区块", "green"));
    stats.appendChild(statCell(String(run.omitted_sections || 0), "省略区块"));
    stats.appendChild(statCell(durationMs != null ? (durationMs / 1000).toFixed(1) + "s" : "—", "耗时"));
    if (protocol && protocol.model) {
      stats.appendChild(statCell(protocol.model, "模型"));
    }
    row.appendChild(stats);
    card.appendChild(row);

    // 失败警示条
    const failCode = (response && response.failure_code) || (protocol && protocol.failure_code);
    const failDetail = (response && response.failure_detail) || (protocol && protocol.failure_detail);
    if (failCode) {
      const alert = el("div", "cx-alert");
      alert.setAttribute("data-icon", "alert");
      const span = el("span");
      span.appendChild(document.createTextNode("协议校验失败 "));
      span.appendChild(el("code", null, failCode));
      if (failDetail) {
        span.appendChild(document.createTextNode(" · "));
        span.appendChild(el("code", null, failDetail));
      }
      alert.appendChild(span);
      card.appendChild(alert);
    }
    return card;
  }

  function chipEl(icon, text) {
    const m = el("span", "m", text);
    m.setAttribute("data-icon", icon);
    return m;
  }

  function shortenScope(id) {
    if (!id) return "";
    const s = String(id);
    if (s.length <= 14) return s;
    return s.slice(0, 6) + "…" + s.slice(-4);
  }

  /* =============================================================
   * AI 实际看到的输入：按真实发送顺序（system → history → user → temp_user）
   * 这是页面最核心的卡片，替代原本"Token 预算分布 + 组装区块"两张卡。
   * ============================================================= */
  function aiInputCardEl(run, reqSnap, sections) {
    const card = el("div", "card cx-input-card");
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "AI 实际看到的输入"));
    titleWrap.appendChild(el("span", "tag tag-src", "按真实发送顺序"));
    head.appendChild(titleWrap);
    card.appendChild(head);

    /* 数据源取舍（重要）：
       provider_request.fields.prompt 在部分部署上只剩时间前缀——用户消息经由
       contexts 传递，且快照常为 snapshot_complete=false，因此 <Msg> 包装并不在
       prompt 字段里。当前用户消息与各注入区块一律以插件自身记录的 sections 为
       准，那是唯一完整保留 <Msg>/<KnownTerms>/<Memory> 等包装文本的来源。
       system_prompt 与 contexts 仍取 provider_request（环境信息，别处没有）。 */
    const pr = (reqSnap && reqSnap.provider_request) || {};
    const f = (pr && pr.fields) || {};
    const systemPrompt = f.system_prompt ? String(f.system_prompt) : "";
    const contexts = Array.isArray(f.contexts) ? f.contexts : [];
    const extraParts = Array.isArray(f.extra_user_content_parts) ? f.extra_user_content_parts : [];

    const byKey = new Map();
    sections.forEach((s) => byKey.set(s.section_key, s));
    const curMsg = byKey.get("current_message");
    // current_message 缺失时降级回 prompt 字段，保证老数据也有东西可看
    const curContent = (curMsg && curMsg.content) || (f.prompt ? String(f.prompt) : "");
    const injected = sections.filter(
      (s) => s.section_key !== "current_message" && s.included
    );

    // 顶部摘要
    const summary = el("div", "cx-input-summary");
    const allText = [
      systemPrompt,
      contexts.map((m) => contentToText(m && m.content)).join(""),
      sections.map((s) => String(s.content || "")).join(""),
    ].join("");
    summary.appendChild(statPill(String(contexts.length), "轮历史"));
    summary.appendChild(statPill(String(injected.length), "段注入"));
    summary.appendChild(statPill(fmtNum(approxTokens(allText)), "估算词元"));
    const omittedCount = sections.filter((s) => !s.included).length;
    if (omittedCount > 0) {
      summary.appendChild(statPill(String(omittedCount), "段已省略", "muted"));
    }
    card.appendChild(summary);

    // 列表：环境（system / 历史）→ 当前用户消息 → 注入区块 → 附带内容
    const list = el("div", "raw-list");
    list.style.marginTop = "14px";
    let n = 0;
    if (systemPrompt) {
      list.appendChild(msgItemEl({ role: "system", content: systemPrompt }, ++n, "system_prompt"));
    }
    if (contexts.length) {
      list.appendChild(historyItemEl(contexts, n + 1));
      n += contexts.length;
    }
    if (curContent) {
      list.appendChild(
        msgItemEl({ role: "user", content: curContent }, ++n, "current_message", {
          originLabel: "替换原始用户消息",
          originClass: "tag-auto",
        })
      );
    }
    injected.forEach((sec) => {
      list.appendChild(
        msgItemEl({ role: "temp_user", content: sec.content || "" }, ++n, sec.section_key, {
          originLabel: sectionLabel(sec.section_key),
          originClass: "tag-src",
        })
      );
    });
    // 随请求附带但不属于任何区块的内容（如 system_reminder、图片附件）
    const sectionTexts = new Set(sections.map((s) => String(s.content || "")));
    extraParts.forEach((part) => {
      const text = contentToText(part);
      if (!text || sectionTexts.has(text)) return;
      list.appendChild(
        msgItemEl({ role: "temp_user", content: text }, ++n, "auto", {
          originLabel: "系统自动注入",
          originClass: "tag-auto",
        })
      );
    });
    if (!list.children.length) {
      list.appendChild(el("div", "cx-empty", "该请求未捕获到任何输入快照"));
    }
    card.appendChild(list);

    // 省略区块摘要（独立折叠区，保留但不放进主列表）
    const omitted = sections.filter((s) => !s.included);
    if (omitted.length) {
      const omit = el("div", "cx-omitted");
      const oh = el("button", "raw-collapse", "展开");
      const paintOh = () => {
        oh.querySelectorAll("svg").forEach((s) => s.remove());
        oh.insertAdjacentHTML("afterbegin", HZ.icon(oh.dataset.icon));
      };
      oh.dataset.icon = "arrow-down";
      paintOh();
      const ohHead = el("div", "cx-omitted-head");
      const ohTitle = el("span", "cx-omitted-title");
      ohTitle.appendChild(
        document.createTextNode("省略了 " + omitted.length + " 个区块 · ")
      );
      omitted.forEach((s, i) => {
        const tag = el("span", "tag tag-omitted-mini", sectionLabel(s.section_key));
        ohTitle.appendChild(tag);
        if (i < omitted.length - 1) ohTitle.appendChild(document.createTextNode(" "));
      });
      ohHead.appendChild(ohTitle);
      ohHead.appendChild(oh);
      omit.appendChild(ohHead);
      const body = el("div", "cx-omitted-body cx-omitted-collapsed");
      omitted.forEach((s) => {
        const row = el("div", "cx-omitted-row");
        const left = el("div");
        left.appendChild(el("span", "cx-omitted-name", sectionLabel(s.section_key)));
        if (s.reason) {
          const reason = el("div", "cx-sec-reason", "原因 · " + reasonLabel(s.reason));
          reason.setAttribute("data-icon", "info");
          left.appendChild(reason);
        }
        row.appendChild(left);
        row.appendChild(
          el("span", "cx-omitted-tokens", fmtNum(s.estimated_tokens || 0) + " tok")
        );
        body.appendChild(row);
      });
      oh.addEventListener("click", () => {
        const collapsed = body.classList.toggle("cx-omitted-collapsed");
        oh.textContent = collapsed ? "展开" : "收起";
        oh.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintOh();
      });
      card.appendChild(omit);
    }
    return card;
  }

  function statPill(num, label, cls) {
    const p = el("span", "cx-stat-pill" + (cls ? " " + cls : ""));
    p.appendChild(el("span", "cx-stat-pill-num", num));
    p.appendChild(el("span", "cx-stat-pill-lab", label));
    return p;
  }

  function historyItemEl(contexts, startIdx) {
    const block = el("div", "raw-msg history");
    const head = el("div", "raw-msg-head");
    head.appendChild(el("span", "raw-idx", "[" + startIdx + "–" + (startIdx + contexts.length - 1) + "]"));
    head.appendChild(el("span", "raw-role history", "历史"));
    head.appendChild(el("span", "raw-len", contexts.length + " 轮 · " + approxTokens(contexts.map((m) => contentToText(m && m.content)).join("")) + " tok"));
    block.appendChild(head);

    const body = el("div", "raw-body history-body");
    // 摘要视图：仅显示首/末各 1 条
    const first = contexts[0];
    const last = contexts[contexts.length - 1];
    if (contexts.length === 1) {
      appendHistoryRow(body, first, 0);
    } else {
      appendHistoryRow(body, first, 0);
      const more = el("div", "history-more");
      more.textContent = "… 中间省略 " + (contexts.length - 2) + " 轮对话 …";
      body.appendChild(more);
      appendHistoryRow(body, last, contexts.length - 1);
    }
    block.appendChild(body);

    const expandBtn = el("button", "raw-collapse", "展开全部");
    const paintBtn = () => {
      expandBtn.querySelectorAll("svg").forEach((s) => s.remove());
      expandBtn.insertAdjacentHTML("afterbegin", HZ.icon(expandBtn.dataset.icon));
    };
    expandBtn.dataset.icon = "arrow-down";
    paintBtn();
    head.appendChild(expandBtn);
    expandBtn.addEventListener("click", () => {
      const collapsed = body.classList.toggle("history-collapsed");
      expandBtn.textContent = collapsed ? "展开全部" : "收起";
      expandBtn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
      paintBtn();
      if (!collapsed) {
        body.innerHTML = "";
        contexts.forEach((m, i) => appendHistoryRow(body, m, i));
      } else {
        body.innerHTML = "";
        if (contexts.length === 1) {
          appendHistoryRow(body, first, 0);
        } else {
          appendHistoryRow(body, first, 0);
          const more = el("div", "history-more");
          more.textContent = "… 中间省略 " + (contexts.length - 2) + " 轮对话 …";
          body.appendChild(more);
          appendHistoryRow(body, last, contexts.length - 1);
        }
      }
    });
    body.classList.add("history-collapsed");
    return block;
  }

  function appendHistoryRow(body, msg, i) {
    const role = (msg && msg.role) || "user";
    const text = contentToText(msg && msg.content);
    const row = el("div", "history-row");
    const tag = el("span", "raw-role " + (role || "user"), role);
    row.appendChild(tag);
    const content = el("span", "history-row-text", text);
    row.appendChild(content);
    body.appendChild(row);
  }

  function msgItemEl(msg, idx, originKey, extra) {
    extra = extra || {};
    const role = String(msg.role || "user");
    const content = contentToText(msg.content);
    const long = content.length > 360;

    const block = el("div", "raw-msg" + (long ? " collapsed" : ""));
    const head = el("div", "raw-msg-head");
    head.appendChild(el("span", "raw-idx", "[" + idx + "]"));
    const roleClass = ({ system: "system", user: "user", assistant: "assistant", temp_user: "temp_user" })[role] || "user";
    head.appendChild(el("span", "raw-role " + roleClass, role));
    if (extra.originLabel) {
      const origin = el("span", "tag " + (extra.originClass || "tag-src"), extra.originLabel);
      head.appendChild(origin);
    }
    head.appendChild(el("span", "raw-len", content.length + " 字 · " + approxTokens(content) + " tok"));
    if (long) {
      const btn = el("button", "raw-collapse", "展开");
      const paintBtn = () => {
        btn.querySelectorAll("svg").forEach((s) => s.remove());
        btn.insertAdjacentHTML("afterbegin", HZ.icon(btn.dataset.icon));
      };
      btn.dataset.icon = "arrow-down";
      paintBtn();
      head.appendChild(btn);
      btn.addEventListener("click", () => {
        const collapsed = block.classList.toggle("collapsed");
        btn.textContent = collapsed ? "展开" : "收起";
        btn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintBtn();
      });
    }
    block.appendChild(head);
    const body = el("div", "raw-body", content);
    block.appendChild(body);
    return block;
  }

  function approxTokens(text) {
    const s = String(text || "");
    if (!s) return 0;
    return Math.max(1, Math.round(s.length / 2));
  }

  /* contentToText：把消息 content（字符串、parts 数组或单个 part 对象）转成可读文本 */
  function contentToText(content) {
    if (content == null) return "";
    if (typeof content === "string") return content;
    // 单个 part 对象 { type, text/text/image_url/... } —— 直接解析
    if (typeof content === "object" && !Array.isArray(content)) {
      const part = content;
      const type = part.type || "";
      if (type === "text" || (!type && typeof part.text === "string")) {
        return String(part.text || "");
      }
      if (type === "image_url") return "[图片]";
      if (type === "audio_url") return "[音频]";
      if (type === "think") {
        const t = String(part.text || "");
        return t ? "[思考] " + t : "";
      }
      // 未知 part：尝试 text 字段；否则 JSON 兜底
      if (typeof part.text === "string") return part.text;
      try { return JSON.stringify(content, null, 2); } catch (e) { return String(content); }
    }
    if (Array.isArray(content)) {
      const parts = [];
      content.forEach((part) => {
        if (!part || typeof part !== "object") return;
        const t = contentToText(part);
        if (t) parts.push(t);
      });
      return parts.join("\n");
    }
    return String(content);
  }

  /* =============================================================
   * 模型响应：思考 / 实际回复 / 协议校验
   * ============================================================= */
  function modelOutputCardEl(run, response, protocol, responseSeq) {
    const card = el("div", "card");
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "模型响应"));
    titleWrap.appendChild(el("span", "tag tag-src", "LLM 返回内容"));
    head.appendChild(titleWrap);
    card.appendChild(head);

    // 优先 final 阶段的数据（更准确：action / success / messages）
    const finalTurn = (responseSeq || []).find((r) => r.stage === "final") || null;
    const action = (finalTurn && finalTurn.action) || (response && response.action) || (protocol && protocol.action) || "—";
    const success = finalTurn ? finalTurn.success : (response && response.success) || (protocol && protocol.success) || false;
    const failCode = (finalTurn && finalTurn.failure_code) || (response && response.failure_code) || (protocol && protocol.failure_code);
    const sentMessages = (finalTurn && Array.isArray(finalTurn.messages) && finalTurn.messages.length)
      ? finalTurn.messages
      : (response && Array.isArray(response.messages) ? response.messages
        : (protocol && Array.isArray(protocol.messages) ? protocol.messages : null));
    const rawOut = (finalTurn && finalTurn.raw_output) || (protocol && protocol.raw_output) || (response && response.raw_output);
    const stage = (finalTurn && finalTurn.stage) || (response && response.stage) || (protocol && protocol.stage);

    const list = el("div", "raw-list");
    list.style.marginTop = "14px";

    // 校验状态
    if (response || protocol || finalTurn) {
      const check = el("div", "cx-validate");
      if (stage) check.appendChild(el("span", "tag tag-src", "阶段 · " + stage));
      const actionTag = el("span", "tag tag-src", "协议动作 · " + action);
      check.appendChild(actionTag);
      const statusTag = el("span", "tag " + (success ? "tag-reply" : "tag-failed"),
        success ? (action === "No Reply" ? "未回复" : "通过") : "失败");
      check.appendChild(statusTag);
      if (!success && failCode) check.appendChild(el("code", null, failCode));
      list.appendChild(check);
    }

    // 多轮响应序列（如有 tool + final）
    if (responseSeq && responseSeq.length > 1) {
      const seqWrap = el("div", "raw-list");
      responseSeq.forEach((item, i) => seqWrap.appendChild(turnItemEl(item, i)));
      list.appendChild(el("div", "raw-divider", "响应轮次 · " + responseSeq.length + " 轮"));
      list.appendChild(seqWrap);
    }

    // 思考过程
    const reasoning = extractReasoning(response, finalTurn, protocol);
    if (reasoning) {
      list.appendChild(el("div", "raw-divider", "思考过程"));
      const block = el("div", "raw-raw collapsed");
      const bhead = el("div", "raw-msg-head");
      bhead.appendChild(el("span", "raw-idx", "[?]"));
      bhead.appendChild(el("span", "raw-role", "thinking"));
      bhead.appendChild(el("span", "raw-len", reasoning.length + " 字"));
      const btn = el("button", "raw-collapse", "展开");
      const paintBtn = () => {
        btn.querySelectorAll("svg").forEach((s) => s.remove());
        btn.insertAdjacentHTML("afterbegin", HZ.icon(btn.dataset.icon));
      };
      btn.dataset.icon = "arrow-down";
      paintBtn();
      bhead.appendChild(btn);
      btn.addEventListener("click", () => {
        const collapsed = block.classList.toggle("collapsed");
        btn.textContent = collapsed ? "展开" : "收起";
        btn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintBtn();
      });
      block.appendChild(bhead);
      block.appendChild(el("div", "raw-body", reasoning));
      list.appendChild(block);
    }

    // 实际回复（解析后的 messages）
    if (sentMessages && sentMessages.length) {
      list.appendChild(el("div", "raw-divider", "实际发送的消息 · " + sentMessages.length + " 条"));
      sentMessages.forEach((text, i) => {
        const item = el("div", "raw-msg");
        const h = el("div", "raw-msg-head");
        h.appendChild(el("span", "raw-idx", "[" + (i + 1) + "]"));
        h.appendChild(el("span", "raw-role assistant", "assistant"));
        h.appendChild(el("span", "raw-len", String(text).length + " 字"));
        item.appendChild(h);
        item.appendChild(el("div", "raw-body", String(text)));
        list.appendChild(item);
      });
    }

    // 模型原始输出（含 <Action> 标签）
    if (rawOut) {
      list.appendChild(el("div", "raw-divider", "模型原始输出"));
      const block = el("div", "raw-raw collapsed");
      const bhead = el("div", "raw-msg-head");
      bhead.appendChild(el("span", "raw-idx", "[O]"));
      bhead.appendChild(el("span", "raw-role", "raw"));
      bhead.appendChild(el("span", "raw-len", String(rawOut).length + " 字"));
      const btn = el("button", "raw-collapse", "展开");
      const paintBtn = () => {
        btn.querySelectorAll("svg").forEach((s) => s.remove());
        btn.insertAdjacentHTML("afterbegin", HZ.icon(btn.dataset.icon));
      };
      btn.dataset.icon = "arrow-down";
      paintBtn();
      bhead.appendChild(btn);
      btn.addEventListener("click", () => {
        const collapsed = block.classList.toggle("collapsed");
        btn.textContent = collapsed ? "展开" : "收起";
        btn.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
        paintBtn();
      });
      block.appendChild(bhead);
      block.appendChild(el("div", "raw-body", String(rawOut)));
      list.appendChild(block);
    }

    if (!list.children.length) {
      list.appendChild(el("div", "cx-empty", "该请求未捕获到模型响应"));
    }
    card.appendChild(list);
    return card;
  }

  function turnItemEl(item, i) {
    const block = el("div", "raw-seq-turn" + (item.success ? "" : " failed"));
    const head = el("div", "raw-msg-head");
    head.appendChild(el("span", "raw-idx", "[" + (i + 1) + "]"));
    head.appendChild(el("span", "raw-role " + (item.stage === "final" ? "assistant" : "tool"),
      item.stage === "final" ? "final" : (item.stage || "tool")));
    head.appendChild(el("span", "tag " + (item.success ? "tag-ok" : "tag-failed"),
      item.success ? "OK" : (item.failure_code || "失败")));
    if (item.action) head.appendChild(el("span", "tag tag-src", item.action));
    block.appendChild(head);
    if (item.raw_output) {
      block.appendChild(el("div", "raw-body", String(item.raw_output)));
    }
    if (item.messages && item.messages.length) {
      block.appendChild(el("div", "d-label", "发送消息"));
      item.messages.forEach((m) => block.appendChild(el("div", "raw-body", String(m))));
    }
    return block;
  }

  function extractReasoning(response, finalTurn, protocol) {
    if (!response && !finalTurn && !protocol) return "";
    // response_sequence 的 snapshot 中可能有 reasoning_content（final turn）
    if (finalTurn && finalTurn.snapshot && finalTurn.snapshot.fields) {
      const r = finalTurn.snapshot.fields.reasoning_content;
      if (r) return String(r);
    }
    if (response && response.reasoning_content) return String(response.reasoning_content);
    return "";
  }

  /* =============================================================
   * 调试：完整原始 JSON（默认折叠）
   * ============================================================= */
  function debugRawCardEl(reqSnap) {
    const card = el("div", "card");
    const head = el("div", "raw-head");
    const titleWrap = el("div", "card-title-wrap");
    titleWrap.appendChild(el("span", "card-dot"));
    titleWrap.appendChild(el("span", "card-title", "完整原始 JSON"));
    titleWrap.appendChild(el("span", "tag tag-src", "进阶调试"));
    head.appendChild(titleWrap);
    const copyBtn = el("button", "raw-copy", "复制 JSON");
    copyBtn.dataset.icon = "copy";
    let rawText = "";
    try {
      rawText = JSON.stringify(reqSnap && reqSnap.provider_request, null, 2) || "";
    } catch (e) { rawText = String(reqSnap); }
    copyBtn.addEventListener("click", () => copyTextFallback(rawText, "已复制 JSON"));
    head.appendChild(copyBtn);
    card.appendChild(head);

    if (!rawText) {
      card.appendChild(el("div", "cx-empty", "未捕获到 provider_request"));
      return card;
    }
    const body = el("div", "raw-body raw-json raw-json-collapsed");
    try { body.appendChild(highlightJsonText(rawText)); }
    catch (e) { body.textContent = rawText; }
    card.appendChild(body);

    const toggle = el("button", "raw-collapse", "展开");
    const paintBtn = () => {
      toggle.querySelectorAll("svg").forEach((s) => s.remove());
      toggle.insertAdjacentHTML("afterbegin", HZ.icon(toggle.dataset.icon));
    };
    toggle.dataset.icon = "arrow-down";
    paintBtn();
    head.appendChild(toggle);
    toggle.addEventListener("click", () => {
      const collapsed = body.classList.toggle("raw-json-collapsed");
      toggle.textContent = collapsed ? "展开" : "收起";
      toggle.dataset.icon = collapsed ? "arrow-down" : "arrow-up";
      paintBtn();
    });
    return card;
  }

  /* JSON 语法高亮 */
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
      const isKey = token.startsWith('"');
      const span = document.createElement("span");
      if (isKey) span.className = "jq-key";
      else if (token === "true" || token === "false") span.className = "jq-bool";
      else if (token === "null") span.className = "jq-null";
      else if (/^-?\d/.test(token)) span.className = "jq-num";
      else span.className = "jq-punc";
      span.textContent = token;
      container.appendChild(span);
      lastIndex = tokenRe.lastIndex;
    }
    if (lastIndex < text.length) {
      container.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
    return container;
  }

  /* ---------- 启动 ---------- */
  loadList().then(() => {
    const first = listEl && listEl.querySelector(".cx-run");
    if (first) openDetail(first.dataset.requestId);
  });
})();
