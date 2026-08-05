/**
 * View: Dashboard — 仪表盘（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET overview（pending_items 可能未落地，字段缺失时容错为默认值）
 * 降级：api.js 未加载时清空 mock 内容并显示明确错误提示。
 * 安全：所有渲染一律通过 textContent / data 属性写入，禁止拼入 innerHTML。
 */
(function () {
  /* 侧边栏 + 顶栏 */
  HZ.renderSidebar("dashboard");
  HZ.renderTopbar({
    title: "仪表盘",
    sub: "2026年3月30日 星期一 · 今天也要元气满满哦",
    search: "搜索记忆、黑话、样例…",
    actions: [
      { label: "导出", icon: "export", variant: "ghost" },
      { label: "新建记忆", icon: "plus", variant: "primary" },
    ],
  });

  /* 通用行为 */
  HZ.initReveal();
  HZ.initCounters();
  HZ.initBars();
  HZ.initRanges();

  /* 协议 Tab 切换（静态预览与真实模式共用） */
  document.querySelectorAll(".p-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".p-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const isReply = tab.dataset.tab === "reply";
      document.getElementById("protocolReply").style.display = isReply ? "" : "none";
      document.getElementById("protocolNoreply").style.display = isReply ? "none" : "";
    });
  });

  /* ---------- 降级保护 ---------- */
  function renderApiUnavailable() {
    /* 清空带 mock 数据的数据容器 */
    const ids = [
      "heroDesc", "heroTagRate", "heroTagPending",
      "statTrendPending", "statLearned", "statTrendSamples", "statRate",
      "statTrendOmitted", "statRuns", "statTrendBlocked", "statTokens",
      "actionRing", "actionNum", "actionReplyCount", "actionNoReplyCount",
      "trendChart", "scopeList", "pendingList",
    ];
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = "";
      /* 移除计数动画数据源与参数，防止 initCounters 的观察器把假数字写回 */
      el.removeAttribute("data-count");
      el.removeAttribute("data-count-parent");
      el.removeAttribute("data-decimal");
      el.removeAttribute("data-suffix");
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
  const api = HZ.api;

  /* ---------- 共享 UI（api.js 落地前的本地兜底，避免直接抛错） ---------- */
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));

  /* ---------- 常量 ---------- */
  const SCOPE_LABEL = {
    global: "全局",
    group: "群聊",
    private_user: "私聊",
    group_member: "群成员",
  };
  const SCOPE_SUB = {
    global: "跨会话共享",
    group: "群聊作用域",
    private_user: "私聊作用域",
    group_member: "群成员作用域",
  };
  const STATUS_LABEL = {
    verified: "verified",
    provisional: "provisional",
    candidate: "candidate",
    ambiguous: "ambiguous",
    rejected: "rejected",
    disabled: "disabled",
  };

  const $ = (sel) => document.querySelector(sel);

  /* ---------- 统计卡（真实数据后重触发数字滚动动画） ---------- */
  const statEls = [
    { card: $(".stats-row"), el: $("#statLearned"), key: "learned" },
    { card: $(".stats-row"), el: $("#statRate"), key: "protocol_success_rate" },
    { card: $(".stats-row"), el: $("#statRuns"), key: "context_stats.total_runs" },
    { card: $(".stats-row"), el: $("#statTokens"), key: "context_stats.average_tokens" },
  ];
  function setStatValue(stat, value, decimal) {
    const v = value == null ? 0 : value;
    stat.el.dataset.count = String(v);
    stat.el.dataset.decimal = String(decimal || 0);
    stat.el.textContent = decimal ? v.toFixed(decimal) : String(v);
  }
  function triggerCounters() {
    if (!HZ.initCounters) return;
    /* 重新初始化后由 IntersectionObserver 触发 data-count 滚动动画 */
    HZ.initCounters();
  }

  /* ---------- 回复占比环（数据驱动，保留原动画） ---------- */
  function renderActionRing(replyCount, noReplyCount) {
    const ring = document.getElementById("actionRing");
    const num = document.getElementById("actionNum");
    if (!ring || !num) return;
    const total = (replyCount || 0) + (noReplyCount || 0);
    const value = total > 0 ? Math.round(((replyCount || 0) / total) * 100) : null;

    const replyEl = $("#actionReplyCount");
    const noReplyEl = $("#actionNoReplyCount");
    if (replyEl) replyEl.textContent = String(replyCount || 0);
    if (noReplyEl) noReplyEl.textContent = String(noReplyCount || 0);

    if (value == null) {
      num.textContent = "—";
      ring.style.strokeDashoffset = 364.4;
      return;
    }

    const C = 2 * Math.PI * 58;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          io.unobserve(e.target);
          const start = performance.now();
          const dur = 1400;
          (function tick(now) {
            const p = Math.min((now - start) / dur, 1);
            const eased = 1 - Math.pow(1 - p, 3);
            ring.style.strokeDashoffset = C * (1 - (value / 100) * eased);
            num.textContent = Math.round(value * eased) + "%";
            if (p < 1) requestAnimationFrame(tick);
          })(start);
        });
      },
      { threshold: 0.4 }
    );
    io.observe(ring);
  }

  /* ---------- 7 天协议通过率柱状图 ---------- */
  function renderTrend(trend) {
    const host = document.getElementById("trendChart");
    if (!host) return;
    host.innerHTML = "";
    const days = trend || [];
    if (!days.length) {
      const empty = document.createElement("div");
      empty.className = "trend-empty";
      empty.textContent = "暂无数据";
      host.appendChild(empty);
      return;
    }
    days.forEach((d, idx) => {
      const col = document.createElement("div");
      col.className = "trend-col" + (idx === days.length - 1 ? " today" : "");
      const tip = document.createElement("span");
      tip.className = "trend-tip";
      tip.textContent = `${d.label || ""} · 当日 ${d.total || 0} 次 · 通过率 ${
        d.value == null ? "—" : Number(d.value).toFixed(1) + "%"
      }`;
      const wrap = document.createElement("div");
      wrap.className = "trend-bar-wrap";
      const skip = document.createElement("div");
      skip.className = "trend-bar-skip";
      skip.style.setProperty("--h", "0%");
      const ok = document.createElement("div");
      ok.className = "trend-bar-ok";
      const v = d.value;
      ok.style.setProperty("--h", v == null ? "0%" : Math.max(2, Math.min(100, Number(v))) + "%");
      wrap.appendChild(skip);
      wrap.appendChild(ok);
      const val = document.createElement("div");
      val.className = "trend-val";
      val.textContent = v == null ? "—" : Math.round(Number(v)) + "%";
      const day = document.createElement("div");
      day.className = "trend-day";
      day.textContent = d.label || "";
      col.appendChild(tip);
      col.appendChild(wrap);
      col.appendChild(val);
      col.appendChild(day);
      host.appendChild(col);
    });
  }

  /* ---------- 词库作用域分布（按 count 降序取前 5） ---------- */
  function renderScopes(scopes) {
    const host = document.getElementById("scopeList");
    if (!host) return;
    host.innerHTML = "";
    const list = (scopes || []).slice().sort((a, b) => (b.count || 0) - (a.count || 0)).slice(0, 5);
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "trend-empty";
      empty.textContent = "暂无数据";
      host.appendChild(empty);
      return;
    }
    const max = Math.max(...list.map((s) => s.count || 0), 1);
    list.forEach((s) => {
      const row = document.createElement("div");
      row.className = "scope-row";
      const main = document.createElement("div");
      main.className = "scope-main";
      const name = document.createElement("div");
      name.className = "scope-name";
      name.textContent = (SCOPE_LABEL[s.scope_type] || s.scope_type || "未分类") + (s.scope_id ? " · " + s.scope_id : "");
      const sub = document.createElement("div");
      sub.className = "scope-sub";
      sub.textContent = SCOPE_SUB[s.scope_type] || "作用域";
      main.appendChild(name);
      main.appendChild(sub);
      const bar = document.createElement("div");
      bar.className = "scope-bar";
      const i = document.createElement("i");
      i.style.width = Math.max(3, Math.round(((s.count || 0) / max) * 100)) + "%";
      bar.appendChild(i);
      const cnt = document.createElement("span");
      cnt.className = "scope-count";
      cnt.textContent = (s.count || 0) + " 条";
      row.appendChild(main);
      row.appendChild(bar);
      row.appendChild(cnt);
      host.appendChild(row);
    });
  }

  /* ---------- 待审核词条（pending_items 缺失时容错为空） ---------- */
  function pendingSub(item) {
    if (item.status === "candidate") {
      return `猜测：${item.term} · 置信度 ${item.confidence == null ? "—" : Number(item.confidence).toFixed(2)}`;
    }
    if (item.status === "ambiguous") {
      const n = item.pending_sense_count == null ? 0 : item.pending_sense_count;
      return n > 0 ? `${n} 个冲突义项，需指定首选` : "多个冲突义项，需指定首选";
    }
    if (item.status === "provisional") return "单义项高置信";
    return "待人工审核";
  }
  function renderPending(items) {
    const host = document.getElementById("pendingList");
    if (!host) return;
    host.innerHTML = "";
    const list = items || [];
    if (!list.length) {
      const empty = document.createElement("div");
      empty.className = "trend-empty";
      empty.textContent = "暂无待审核";
      host.appendChild(empty);
      return;
    }
    list.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "list-row";
      const icon = document.createElement("div");
      icon.className = "list-icon " + ["i-pink", "i-blue", "i-violet", "i-green"][idx % 4];
      icon.innerHTML = HZ.icon(item.status === "ambiguous" ? "jargon" : "chat");
      const main = document.createElement("div");
      main.className = "list-main";
      const title = document.createElement("div");
      title.className = "list-title";
      title.textContent = item.term || "（未命名词条）";
      const sub = document.createElement("div");
      sub.className = "list-sub";
      sub.textContent = pendingSub(item);
      main.appendChild(title);
      main.appendChild(sub);
      const tag = document.createElement("span");
      tag.className = "tag tag-review";
      tag.textContent = STATUS_LABEL[item.status] || item.status || "unknown";
      row.appendChild(icon);
      row.appendChild(main);
      row.appendChild(tag);
      host.appendChild(row);
    });
  }

  /* ---------- 概览加载 ---------- */
  const errbar = document.getElementById("overviewError");
  const errbarText = document.getElementById("overviewErrorText");
  function showError(message) {
    if (errbarText) errbarText.textContent = message;
    if (errbar) errbar.style.display = "flex";
  }
  function hideError() {
    if (errbar) errbar.style.display = "none";
  }

  async function loadOverview() {
    try {
      const data = await api.get("overview");
      hideError();
      renderOverview(data || {});
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      showError(err.message);
      renderApiUnavailable(); // 清空 mock 内容 + 显示错误提示，防止假数据误导
    }
  }

  function renderOverview(d) {
    const cs = d.context_stats || {};
    const rate = d.protocol_success_rate;

    /* Hero */
    const desc = $("#heroDesc");
    if (desc) {
      desc.textContent =
        "近 7 天协议通过率 " +
        (rate == null ? "暂无数据" : Number(rate).toFixed(1) + "%") +
        `，词库有 ${d.pending == null ? 0 : d.pending} 条词条待审核，` +
        `上下文追踪已记录 ${cs.total_runs == null ? 0 : cs.total_runs} 次请求。随时可以在下方查看详情～`;
    }
    const tagRate = $("#heroTagRate");
    if (tagRate) tagRate.textContent = rate == null ? "协议通过率 暂无数据" : "协议通过率 " + Number(rate).toFixed(1) + "%";
    const tagPending = $("#heroTagPending");
    if (tagPending) tagPending.textContent = (d.pending == null ? 0 : d.pending) + " 条待审核";

    /* 统计卡（含角标） */
    const pendingTrend = $("#statTrendPending");
    if (pendingTrend) pendingTrend.textContent = (d.pending == null ? 0 : d.pending) + " 条待审";
    const samplesTrend = $("#statTrendSamples");
    if (samplesTrend) samplesTrend.textContent = (d.protocol_samples == null ? 0 : d.protocol_samples) + " 样本";
    const omittedTrend = $("#statTrendOmitted");
    if (omittedTrend) omittedTrend.textContent = (cs.omitted_runs == null ? 0 : cs.omitted_runs) + " 次省略";
    const blockedTrend = $("#statTrendBlocked");
    if (blockedTrend) blockedTrend.textContent = (d.blocked_week == null ? 0 : d.blocked_week) + " 次拦截";

    statEls.forEach((s) => {
      if (!s.el) return;
      const raw = s.key.split(".").reduce((o, k) => (o == null ? null : o[k]), d);
      if (s.key === "protocol_success_rate") {
        setStatValue(s, raw == null ? 0 : raw, 1);
      } else if (s.key === "context_stats.average_tokens") {
        setStatValue(s, raw == null ? 0 : Math.round(raw), 0);
      } else {
        setStatValue(s, raw == null ? 0 : raw, 0);
      }
    });
    triggerCounters();

    /* 回复占比环 */
    const dist = d.action_distribution || {};
    const reply = dist.Reply == null ? 0 : dist.Reply;
    const noReply = dist["No Reply"] == null ? 0 : dist["No Reply"];
    renderActionRing(reply, noReply);

    /* 7 天柱状图 */
    renderTrend(Array.isArray(d.protocol_trend) ? d.protocol_trend : []);

    /* 词库作用域 */
    renderScopes(Array.isArray(d.scopes) ? d.scopes : []);

    /* 待审核词条（字段缺失时容错为空态） */
    renderPending(Array.isArray(d.pending_items) ? d.pending_items : []);
  }

  /* 重试按钮 */
  const retryBtn = errbar && errbar.querySelector('[data-act="retry"]');
  if (retryBtn) retryBtn.addEventListener("click", loadOverview);

  /* 初始化 */
  loadOverview();
})();
