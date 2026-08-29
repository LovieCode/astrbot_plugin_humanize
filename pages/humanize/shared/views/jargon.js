/**
 * View: Jargon — 黑话词库页交互（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET jargons / GET jargon-detail / GET jargon-export / POST jargon-action
 * 降级：api.js 未加载时清空 mock 内容并显示明确错误提示。
 * 安全：所有持久化内容（term/meaning/source_text/proposed_meaning/reason 等）
 *       一律通过 textContent 写入，禁止拼入 innerHTML。
 */
HZ.views["jargon"] = { init: function () {

  HZ.topbars["jargon"] = {
    title: "黑话词库",
    sub: "LLM 词条一律不可信 · 验证、限长并保留证据后才可注入",
    search: "搜索词条、别名、释义…",
    actions: [
      { label: "导出", icon: "export", variant: "ghost" },
      { label: "新建词条", icon: "plus", variant: "primary" },
    ],
    onRefresh: loadList,
  };
HZ.renderTopbar(HZ.topbars["jargon"]);
  HZ.initReveal();

  /** api.js 缺失时的明确降级：清空 mock 数据容器并插入错误提示条。 */
  function renderApiUnavailable() {
    ["jgList", "jgPager", "drawerBody"].forEach((id) => {
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
    const anchor = document.querySelector(".jg-main") || document.querySelector(".main");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(bar, anchor);
  }

  if (!window.HZ || !HZ.api) {
    console.error("共享 API 层（shared/api.js）未加载，无法获取真实数据");
    renderApiUnavailable();
    return;
  }
  const api = HZ.api;

  /* ---------- 共享 UI（api.js 落地前的本地兜底，避免直接抛错） ---------- */
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));
  const confirmDlg = HZ.confirm || ((o) => o.onConfirm && o.onConfirm());
  const initEmpty = HZ.initEmpty;
  const initErrbar = HZ.initErrbar;
  const fmtTime = (iso) => (api.time ? api.time(iso) : String(iso || ""));
  const fmtAgo = (iso) => (api.ago ? api.ago(iso) : String(iso || ""));

  /* ---------- 常量 ---------- */
  const PAGE_SIZE = 10;
  const SCOPE_LABEL = {
    global: "全局",
    group: "群聊",
    private_user: "私聊",
    group_member: "群成员",
  };
  const STATUS_LABEL = {
    verified: "已验证",
    provisional: "暂定",
    candidate: "候选",
    ambiguous: "歧义",
    rejected: "已拒绝",
    disabled: "已停用",
  };
  const STATUS_CLASS = {
    verified: "tag-verified",
    provisional: "tag-provisional",
    candidate: "tag-candidate2",
    ambiguous: "tag-ambiguous",
    rejected: "tag-rejected2",
    disabled: "tag-disabled",
  };

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const listEl = $("#jgList");
  const pagerEl = $("#jgPager");
  const drawer = $("#jg-drawer");
  const mask = $("#jg-drawerMask");
  const drawerBody = $("#jg-drawerBody");
  /* 限定在抽屉内查找 foot：源码单页唯一，构建合并后多视图都有 .drawer-foot，必须约束作用域 */
  const footEl = $(".drawer-foot", drawer);

  /* ---------- 状态 ---------- */
  let current = { page: 1, scope: "", status: "", search: "" };
  let detail = null; // 当前抽屉词条详情（data.entry）
  let detailData = null; // 当前抽屉完整详情（含 senses/aliases/evidence 等）
  let busy = false;
  let reloadPending = false; // busy 期间有新的列表刷新请求，结束后补跑一次
  let detailSeq = 0; // 详情请求序号：渲染前比对，旧响应丢弃

  /* ---------- DOM 小工具 ---------- */
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  /** 把 [data-icon] 占位替换为共享图标（与 jargon.html 内联脚本同逻辑）。 */
  function injectIcons(root) {
    root.querySelectorAll("[data-icon]").forEach((n) => {
      const svg = HZ.icon(n.dataset.icon);
      const afterbegin =
        (n.tagName === "BUTTON" && n.textContent.trim()) ||
        n.classList.contains("m") ||
        n.classList.contains("d-label") ||
        n.classList.contains("jg-alias") ||
        n.classList.contains("match-flag") ||
        n.classList.contains("preferred-star");
      if (afterbegin) n.insertAdjacentHTML("afterbegin", svg);
      else n.innerHTML = svg;
    });
  }

  /* ---------- 状态 / 作用域标签 ---------- */
  /** disabled 词条显示「已停用」，否则按 status。返回 DOM 元素。 */
  function statusTagEl(status, enabled) {
    const key = !enabled ? "disabled" : status;
    const tag = el("span", "tag " + (STATUS_CLASS[key] || "tag-candidate2"));
    const dot = el("span", "tag-dot");
    tag.appendChild(dot);
    tag.appendChild(document.createTextNode(STATUS_LABEL[key] || key || "未知"));
    return tag;
  }

  function scopeText(item) {
    const label = SCOPE_LABEL[item.scope_type] || item.scope_type || "";
    return item.scope_id ? `${label} · ${item.scope_id}` : label || "未分类";
  }

  /** 置信度：多义项时展示首选义项置信度（与后端列表口径一致）。 */
  function confidenceOf(item) {
    return item.preferred_sense && item.preferred_sense.confidence != null
      ? item.preferred_sense.confidence
      : item.confidence;
  }

  /* ---------- 列表 ---------- */
  async function loadList() {
    if (busy) {
      /* 请求往返期间筛选/搜索/翻页可能已变化：记下来收尾后用最新状态补跑，
         而不是直接丢弃（否则筛选控件与列表内容脱节且不自恢复）。 */
      reloadPending = true;
      return;
    }
    busy = true;
    try {
      const data = await api.get("jargons", {
        ...api.pageParams({ page: current.page, pageSize: PAGE_SIZE }),
        search: current.search || undefined,
        status: current.status || undefined,
        scope_type: current.scope || undefined,
      });
      renderList(data);
      renderPager(data);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      if (initErrbar) initErrbar({ message: err.message });
      renderApiUnavailable(); // 清空 mock 内容 + 显示错误提示，防止假数据误导
    } finally {
      busy = false;
      if (reloadPending) {
        reloadPending = false;
        void loadList();
      }
    }
  }

  function renderList(data) {
    const items = data.items || [];
    listEl.innerHTML = "";
    if (!items.length) {
      if (initEmpty) {
        listEl.appendChild(initEmpty({ text: "没有符合条件的词条" }));
      } else {
        const empty = el("div", "jg-empty", "没有符合条件的词条");
        listEl.appendChild(empty);
      }
      return;
    }
    items.forEach((item) => listEl.appendChild(cardEl(item)));
  }

  function cardEl(item) {
    const card = el("div", "jg-card");
    card.dataset.id = item.id;
    if (!item.enabled) card.style.opacity = "0.6";

    /* 顶部行：词条 + 别名 + 状态 + 作用域 + 操作 */
    const top = el("div", "jg-top");
    top.appendChild(el("span", "jg-term", item.term));
    if (item.alias_count) {
      const aliases = el("span", "jg-aliases");
      const list = item.aliases || [];
      if (list.length) {
        list.forEach((a) => {
          const chip = el("span", "jg-alias", a);
          chip.setAttribute("data-icon", "link");
          aliases.appendChild(chip);
        });
      } else {
        const chip = el("span", "jg-alias", `+${item.alias_count} 别名`);
        chip.setAttribute("data-icon", "link");
        aliases.appendChild(chip);
      }
      top.appendChild(aliases);
    }
    top.appendChild(statusTagEl(item.status, item.enabled));
    top.appendChild(el("span", "jg-scope", scopeText(item)));

    const actions = el("div", "jg-actions");
    if (!item.enabled) actions.style.opacity = "1";
    const editBtn = el("button", "icon-btn");
    editBtn.dataset.act = "edit";
    editBtn.dataset.icon = "edit";
    editBtn.title = "编辑";
    const toggleBtn = el("button", "icon-btn");
    toggleBtn.dataset.act = "toggle";
    toggleBtn.dataset.icon = item.enabled ? "zap_off" : "refresh";
    toggleBtn.title = item.enabled ? "停用" : "重新启用";
    actions.appendChild(editBtn);
    actions.appendChild(toggleBtn);
    top.appendChild(actions);
    card.appendChild(top);

    /* 释义（textContent，禁 innerHTML） */
    const meaning = el("div", "jg-meaning");
    meaning.textContent = (item.preferred_sense && item.preferred_sense.meaning) || item.meaning || "";
    if (item.sense_count > 1) {
      meaning.appendChild(el("span", "sense-more", `+${item.sense_count - 1} 个义项`));
    }
    card.appendChild(meaning);

    /* 元信息行 */
    const meta = el("div", "jg-meta");
    const conf = confidenceOf(item);
    if (item.verified_sense_count) {
      const m = el("span", "m", `${item.verified_sense_count} 条证据`);
      m.setAttribute("data-icon", "quote");
      meta.appendChild(m);
    }
    if (item.occurrence_count) {
      const m = el("span", "m", `出现 ${item.occurrence_count} 次`);
      m.setAttribute("data-icon", "eye");
      meta.appendChild(m);
    }
    if (conf != null) {
      const m = el("span", "m", `conf ${conf}`);
      m.setAttribute("data-icon", "spark");
      meta.appendChild(m);
    }
    if (item.last_seen_at) {
      const m = el("span", "m", `最近 ${fmtAgo(item.last_seen_at)}`);
      m.setAttribute("data-icon", "clock");
      meta.appendChild(m);
    }
    if (item.has_conflict) {
      const m = el("span", "m", "有冲突义项");
      m.style.color = "var(--amber-text)";
      m.setAttribute("data-icon", "alert");
      meta.appendChild(m);
    }
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

  /* ---------- 侧栏统计：接口无统计端点，仅取总数 + 待审数（各一次 pageSize=1 请求） ---------- */
  async function loadSideStats() {
    try {
      const total = await api.get("jargons", api.pageParams({ page: 1, pageSize: 1 }));
      const pending = await api.get("jargons", { ...api.pageParams({ page: 1, pageSize: 1 }), status: "pending" });
      const elTotal = $("#statTotal");
      const elPending = $("#statPending");
      if (elTotal) elTotal.textContent = String(total.total || 0);
      if (elPending) elPending.textContent = String(pending.total || 0);
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  /* ---------- 抽屉 ---------- */
  function openDrawer(id) {
    $$(".jg-card").forEach((c) => c.classList.toggle("selected", c.dataset.id === String(id)));
    drawer.classList.add("open");
    mask.classList.add("open");
    loadDetail(id);
  }

  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    $$(".jg-card").forEach((c) => c.classList.remove("selected"));
  }

  async function loadDetail(id) {
    /* last-write-wins：快速切换卡片时，只有最新一次请求允许渲染，
       旧响应（可能更慢返回）直接丢弃，避免抽屉显示错词条的详情。 */
    const seq = ++detailSeq;
    try {
      const data = await api.get("jargon-detail", { id });
      if (seq !== detailSeq) return;
      detail = data.entry || {};
      detailData = data;
      renderDetail(data);
    } catch (e) {
      if (seq !== detailSeq) return;
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      drawerBody.innerHTML = "";
      drawerBody.appendChild(el("div", "jg-empty", "详情加载失败"));
      drawerBody.appendChild(el("div", "jg-empty-sub", err.message));
    }
  }

  /* ---------- 详情渲染辅助 ---------- */
  function chip(cls, text) {
    const t = el("span", "tag tag-lg " + cls, text);
    return t;
  }
  function flag(on, text) {
    const f = el("span", "match-flag" + (on ? " on" : ""), text);
    f.setAttribute("data-icon", "check_simple");
    return f;
  }
  function section(iconName, label, content) {
    const box = el("div");
    const labelEl = el("div", "d-label", label);
    labelEl.setAttribute("data-icon", iconName);
    box.appendChild(labelEl);
    box.appendChild(content);
    return box;
  }
  function subSpan(text, cls) {
    const s = el("span", cls || null, text);
    return s;
  }
  /** 日志条目（证据 / 推断 / 注入通用结构）。 */
  function logItem(mainText, subs) {
    const item = el("div", "log-item");
    const main = el("div", "log-main", mainText);
    const sub = el("div", "log-sub");
    subs.forEach((s) => sub.appendChild(s));
    main.appendChild(sub);
    item.appendChild(main);
    return item;
  }

  function renderDetail(data) {
    const entry = data.entry || {};
    drawerBody.innerHTML = "";

    /* 词条元信息 */
    const chips = el("div", "d-chips");
    chips.appendChild(chip("tag-pink", "词条 · " + (entry.term || "")));
    if (entry.normalized_term) chips.appendChild(chip("tag-scope", "规范化 · " + entry.normalized_term));
    if (entry.scope_type) chips.appendChild(chip("tag-scope", "作用域 · " + (SCOPE_LABEL[entry.scope_type] || entry.scope_type)));
    if (entry.scope_id != null) chips.appendChild(chip("tag-alias", "id · " + entry.scope_id));
    drawerBody.appendChild(section("jargon", "词条", chips));

    /* 匹配配置 */
    const flags = el("div");
    flags.appendChild(flag(!!entry.enabled, "enabled"));
    flags.appendChild(flag(false, "match_mode · " + (entry.match_mode || "-")));
    flags.appendChild(flag(false, "case_sensitive · " + (entry.case_sensitive ? "on" : "off")));
    drawerBody.appendChild(section("settings", "匹配配置", flags));

    /* 义项 */
    const senses = data.senses || [];
    const sensesBox = el("div");
    senses.forEach((s) => sensesBox.appendChild(renderSense(s)));
    drawerBody.appendChild(section("file", `义项（${senses.length} 个）`, sensesBox));

    /* 别名 */
    const aliases = data.aliases || [];
    if (aliases.length) {
      const box = el("div", "d-chips");
      aliases.forEach((a) => {
        const t = el("span", "jg-alias", a.alias);
        t.setAttribute("data-icon", "link");
        box.appendChild(t);
      });
      drawerBody.appendChild(section("link", `别名（${aliases.length} 个）`, box));
    }

    /* 证据 */
    const evidence = data.evidence || [];
    if (evidence.length) {
      const box = el("div");
      evidence.forEach((ev) => {
        const subs = [subSpan("msg #" + (ev.message_id || "-")), subSpan("sender " + (ev.sender_id || "-"))];
        if (ev.observed_at) subs.push(subSpan(fmtTime(ev.observed_at)));
        subs.push(subSpan(ev.valid === false ? "invalid" : "valid", ev.valid === false ? "log-rejected" : "log-accepted"));
        box.appendChild(logItem(`「${ev.source_text || ""}」`, subs));
      });
      drawerBody.appendChild(section("quote", `证据（${evidence.length} 条）`, box));
    }

    /* 推断日志 */
    const inferences = data.inferences || [];
    if (inferences.length) {
      const box = el("div");
      inferences.forEach((inf) => {
        const subs = [subSpan("理由：" + (inf.reason || "-"))];
        subs.push(subSpan(inf.accepted ? "采纳" : "拒绝", inf.accepted ? "log-accepted" : "log-rejected"));
        if (inf.created_at) subs.push(subSpan(fmtTime(inf.created_at)));
        box.appendChild(logItem(`提议释义「${inf.proposed_meaning || ""}」 · conf ${inf.confidence ?? "-"}`, subs));
      });
      drawerBody.appendChild(section("spark", "推断日志", box));
    }

    /* 注入日志 */
    const injections = data.injections || [];
    if (injections.length) {
      const box = el("div");
      injections.forEach((inj) => {
        const subs = [subSpan("scope " + (inj.scope_id || "-")), subSpan("理由：" + (inj.reason || "-"))];
        if (inj.created_at) subs.push(subSpan(fmtTime(inj.created_at)));
        box.appendChild(logItem(`request #${(inj.request_id || "-").slice(0, 12)} · ${inj.selected ? "selected" : "skipped"}`, subs));
      });
      drawerBody.appendChild(section("history", "注入日志", box));
    }

    /* 抽屉头部 / 底部按钮 */
    $("#jg-drawerTitle").textContent = entry.term || "";
    const st = $("#jg-drawerStatus");
    st.innerHTML = "";
    st.appendChild(statusTagEl(entry.status, entry.enabled));
    renderFoot(entry);
    injectIcons(drawerBody);
  }

  /* 义项卡片 */
  function renderSense(s) {
    const item = el("div", "sense-item" + (s.is_preferred ? " preferred" : ""));
    item.dataset.senseId = s.id;

    const top = el("div", "sense-top");
    if (s.is_preferred) {
      const star = el("span", "preferred-star", "首选义项");
      star.setAttribute("data-icon", "star");
      top.appendChild(star);
    }
    top.appendChild(el("span", "tag " + (STATUS_CLASS[s.status] || "tag-candidate2"), s.status || "-"));
    const byline = el("span", null, `v${s.version ?? "-"} · by ${s.created_by || "-"}`);
    byline.style.cssText = "margin-left:auto;font-size:11px;color:var(--text-2)";
    top.appendChild(byline);
    item.appendChild(top);

    item.appendChild(el("div", "sense-meaning", s.meaning || ""));
    if (s.reason) item.appendChild(el("div", "sense-reason", s.reason));

    const meta = el("div", "sense-meta");
    meta.appendChild(subSpan(`conf ${s.confidence ?? "-"}`));
    meta.appendChild(subSpan(`${s.evidence_count ?? 0} 条证据`));
    if (s.created_at) meta.appendChild(subSpan(fmtTime(s.created_at)));
    item.appendChild(meta);

    /* 操作按钮 */
    const actions = el("div", "sense-actions");
    [
      ["confirm", "确认", "btn-tonal"],
      ["set_preferred", "设为首选", "btn-ghost"],
      ["edit", "编辑", "btn-ghost"],
      ["merge", "合并", "btn-ghost"],
      ["reject", "拒绝", "btn-ghost"],
      ["delete", "删除", "btn-ghost jg-danger"],
    ].forEach(([action, label, variant]) => {
      const b = el("button", "btn btn-sm " + variant, label);
      b.dataset.senseAction = action;
      actions.appendChild(b);
    });
    item.appendChild(actions);
    return item;
  }

  function renderFoot(entry) {
    footEl.querySelectorAll("[data-action]").forEach((b) => {
      const act = b.dataset.action;
      if (act === "toggle_enable") {
        const enabled = !!entry.enabled;
        const oldSvg = b.querySelector("svg");
        if (oldSvg) oldSvg.remove();
        b.dataset.icon = enabled ? "zap_off" : "refresh";
        b.textContent = enabled ? "停用" : "重新启用";
        b.insertAdjacentHTML("afterbegin", HZ.icon(b.dataset.icon));
      }
      if (act === "reject_entry") b.style.display = entry.status === "rejected" ? "none" : "";
    });
  }

  /* ---------- 弹窗（本地实现，语义对齐 HZ.confirm 的回调风格） ---------- */
  const modal = el("div", "jg-modal");
  modal.style.display = "none";
  document.body.appendChild(modal);

  let modalResolve = null;

  function closeModal(result) {
    modal.style.display = "none";
    /* 不在此清空 DOM：调用方在 await openFormModal 返回后仍要读表单输入值。
       openModal 每次重新写入 innerHTML，旧内容自然被覆盖，无需显式清空。 */
    if (modalResolve) {
      const r = modalResolve;
      modalResolve = null;
      r(result === true);
    }
    /* modal 关闭后恢复抽屉遮罩（抽屉本身可能仍开着） */
    if (drawer && drawer.classList.contains("open")) {
      mask.classList.add("open");
    }
  }

  function openModal(html) {
    modal.innerHTML = html;
    modal.style.display = "flex";
    /* modal 弹出时隐藏抽屉遮罩，避免遮罩拦截 modal 的点击 */
    const openMask = document.querySelector("#jg-drawerMask.open");
    if (openMask) openMask.classList.remove("open");
    injectIcons(modal);
    return new Promise((resolve) => {
      modalResolve = resolve;
    });
  }

  modal.addEventListener("click", (e) => {
    if (e.target === modal) return closeModal(false);
    if (e.target.closest("[data-m-close]")) return closeModal(false);
    if (e.target.closest("[data-m-ok]")) return closeModal(true);
  });

  function modalShell(title, bodyHtml, okText) {
    return `<div class="jg-modal-card">
      <div class="jg-modal-head"><span class="drawer-title"></span><button class="drawer-close" data-m-close>✕</button></div>
      <div class="jg-modal-body"></div>
      <div class="jg-modal-foot">
        <button class="btn btn-ghost" data-m-close>取消</button>
        <button class="btn btn-primary" data-m-ok></button>
      </div>
    </div>`;
  }

  function openFormModal(title, fieldsHtml, okText) {
    const html = modalShell(title, "", okText);
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    $(".jg-modal-head .drawer-title", tmp).textContent = title;
    $(".jg-modal-body", tmp).innerHTML = fieldsHtml;
    $("[data-m-ok]", tmp).textContent = okText || "确定";
    return openModal(tmp.innerHTML);
  }

  function inputRow(label, value, id, opts) {
    const type = (opts && opts.type) || "text";
    const placeholder = (opts && opts.placeholder) || "";
    const input = `<input class="jg-input" id="${id}" type="${type}" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value == null ? "" : value)}" />`;
    return `<label class="jg-field"><span class="jg-field-label">${escapeHtml(label)}</span>${input}</label>`;
  }

  function val(id) {
    const node = document.getElementById(id);
    return node ? node.value.trim() : "";
  }

  /* ---------- 动作执行 ---------- */
  async function postAction(payload) {
    const data = await api.post("jargon-action", payload);
    if (data && data.deleted) {
      toast("已删除", { type: "success" });
      closeDrawer();
      loadList();
      loadSideStats();
      return;
    }
    toast("操作成功", { type: "success" });
    const detail2 = data && data.detail ? data.detail : null;
    if (detail2 && (detail2.id || (detail2.entry && detail2.entry.id))) {
      loadDetail(detail2.id || detail2.entry.id);
    } else {
      loadList();
    }
    loadSideStats();
  }

  async function runAction(action, extra) {
    const entry = detail || {};
    await postAction({ id: entry.id, action, ...(extra || {}) });
  }

  /* ---------- 义项操作 ---------- */
  async function onSenseAction(action, senseId) {
    if (!senseId) return;
    try {
      if (action === "confirm") {
        /* 可勾「设为首选」（后端要求 verified 才能设首选，确认本身会把义项置为 verified） */
        const ok = await openFormModal("确认义项",
          `<div class="jg-modal-hint">确认后将义项标记为 verified。是否同时设为首选？</div>` +
          `<label class="jg-check"><input type="checkbox" id="mPreferred" checked /><span>同时设为首选</span></label>`,
          "确认");
        if (!ok) return;
        const pref = document.getElementById("mPreferred");
        await runAction("confirm_sense", { sense_id: senseId, preferred: pref && pref.checked ? true : undefined });
      } else if (action === "reject") {
        confirmDlg({
          title: "拒绝义项",
          text: "拒绝后该义项将不再参与注入，确定拒绝吗？",
          danger: true,
          onConfirm: () => runAction("reject_sense", { sense_id: senseId }),
        });
      } else if (action === "set_preferred") {
        const senses = (detailData && detailData.senses) || [];
        const sense = senses.find((s) => s.id === senseId);
        if (sense && sense.status !== "verified") {
          toast("只有 verified 义项才能设为首选，请先确认该义项", { type: "error" });
          return;
        }
        await runAction("set_preferred", { sense_id: senseId });
      } else if (action === "edit") {
        const senses = (detailData && detailData.senses) || [];
        const sense = senses.find((s) => s.id === senseId);
        if (!sense) return;
        const ok = await openFormModal("编辑义项",
          inputRow("释义", sense.meaning, "mMeaning", { placeholder: "义项释义" }) +
          inputRow("置信度", sense.confidence, "mConfidence", { type: "number", placeholder: "0~1" }),
          "保存");
        if (!ok) return;
        const meaning = val("mMeaning");
        const confidence = parseFloat(val("mConfidence"));
        if (!meaning) {
          toast("释义不能为空", { type: "error" });
          return;
        }
        await runAction("update_sense", {
          sense_id: senseId,
          meaning,
          confidence: Number.isFinite(confidence) ? confidence : undefined,
        });
      } else if (action === "merge") {
        const senses = (detailData && detailData.senses) || [];
        const others = senses.filter((s) => s.id !== senseId);
        if (!others.length) {
          toast("没有其他义项可合并", { type: "error" });
          return;
        }
        const options = others
          .map((s) => `<option value="${s.id}">${escapeHtml((s.meaning || "").slice(0, 24))}（${escapeHtml(s.status || "")}）</option>`)
          .join("");
        const ok = await openFormModal("合并义项",
          `<div class="jg-field"><span class="jg-field-label">并入目标义项</span><select class="jg-input" id="mTarget">${options}</select></div>` +
          `<div class="jg-modal-hint">合并后当前义项将被删除，其证据与别名转移到目标义项。</div>`,
          "合并");
        if (!ok) return;
        const target = document.getElementById("mTarget");
        const targetId = target ? target.value : "";
        if (!targetId) {
          toast("请选择目标义项", { type: "error" });
          return;
        }
        confirmDlg({
          title: "确认合并义项",
          text: "将当前义项合并到目标义项？此操作不可撤销。",
          danger: true,
          onConfirm: () => runAction("merge_sense", { source_sense_id: senseId, target_sense_id: targetId }),
        });
      } else if (action === "delete") {
        confirmDlg({
          title: "删除义项",
          text: "删除后不可恢复，确定删除该义项吗？",
          danger: true,
          onConfirm: () => runAction("delete_sense", { sense_id: senseId }),
        });
      }
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  /* ---------- 词条级操作 ---------- */
  async function onEntryAction(action) {
    if (!detail) return;
    const entry = detail;
    try {
      if (action === "edit_entry") {
        const ok = await openFormModal("编辑词条",
          inputRow("词条", entry.term, "mTerm", { placeholder: "词条" }) +
          `<label class="jg-field"><span class="jg-field-label">匹配方式</span><select class="jg-input" id="mMatchMode">
            <option value="smart" ${entry.match_mode === "smart" ? "selected" : ""}>smart 智能</option>
            <option value="contains" ${entry.match_mode === "contains" ? "selected" : ""}>contains 包含</option>
            <option value="exact" ${entry.match_mode === "exact" ? "selected" : ""}>exact 精确</option>
          </select></label>` +
          `<label class="jg-check"><input type="checkbox" id="mEnabled" ${entry.enabled ? "checked" : ""} /><span>启用（参与注入）</span></label>` +
          `<label class="jg-check"><input type="checkbox" id="mCase" ${entry.case_sensitive ? "checked" : ""} /><span>区分大小写</span></label>`,
          "保存");
        if (!ok) return;
        const term = val("mTerm");
        if (!term) {
          toast("词条不能为空", { type: "error" });
          return;
        }
        await runAction("update_entry", {
          term,
          enabled: document.getElementById("mEnabled").checked ? 1 : 0,
          match_mode: document.getElementById("mMatchMode").value,
          case_sensitive: document.getElementById("mCase").checked ? 1 : 0,
        });
      } else if (action === "create_sense") {
        const ok = await openFormModal("新增义项",
          inputRow("释义", "", "mMeaning", { placeholder: "义项释义" }) +
          inputRow("置信度", "0.8", "mConfidence", { type: "number", placeholder: "0~1" }) +
          `<label class="jg-field"><span class="jg-field-label">状态</span><select class="jg-input" id="mStatus">
            <option value="candidate">候选</option>
            <option value="provisional">provisional 临时</option>
            <option value="verified">已验证</option>
          </select></label>`,
          "新增");
        if (!ok) return;
        const meaning = val("mMeaning");
        const confidence = parseFloat(val("mConfidence"));
        if (!meaning) {
          toast("释义不能为空", { type: "error" });
          return;
        }
        await runAction("create_sense", {
          meaning,
          confidence: Number.isFinite(confidence) ? confidence : undefined,
          status: document.getElementById("mStatus").value,
        });
      } else if (action === "manage_aliases") {
        const currentAliases = ((detailData && detailData.aliases) || []).map((a) => a.alias).join("，");
        const ok = await openFormModal("管理别名",
          inputRow("别名（逗号分隔，整表替换）", currentAliases, "mAliases", { placeholder: "别名1,别名2" }),
          "保存");
        if (!ok) return;
        const aliases = val("mAliases")
          .split(/[,，]/)
          .map((s) => s.trim())
          .filter(Boolean);
        await runAction("replace_aliases", { aliases });
      } else if (action === "toggle_enable") {
        await runAction("update_entry", { enabled: entry.enabled ? 0 : 1 });
      } else if (action === "reject_entry") {
        confirmDlg({
          title: "拒绝词条",
          text: "拒绝后该词条将不再参与注入，确定拒绝吗？",
          danger: true,
          onConfirm: () => runAction("reject"),
        });
      } else if (action === "delete_entry") {
        confirmDlg({
          title: "删除词条",
          text: "删除后词条及全部义项、别名、证据将不可恢复，确定删除吗？",
          danger: true,
          onConfirm: () => runAction("delete"),
        });
      }
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  /* ---------- 新建词条 ---------- */
  async function createEntry() {
    try {
      const ok = await openFormModal("新建词条",
        inputRow("词条", "", "mTerm", { placeholder: "如 yyds" }) +
        `<label class="jg-field"><span class="jg-field-label">作用域类型</span><select class="jg-input" id="mScopeType">
          ${Object.entries(SCOPE_LABEL).map(([k, v]) => `<option value="${k}">${k} ${v}</option>`).join("")}
        </select></label>` +
        inputRow("作用域 ID", "", "mScopeId", { placeholder: "群号或用户标识" }) +
        inputRow("释义", "", "mMeaning", { placeholder: "释义（1-1000 字符）" }) +
        inputRow("置信度", "0.9", "mConfidence", { type: "number", placeholder: "0~1" }) +
        inputRow("别名（逗号分隔，可选）", "", "mAliases", { placeholder: "别名1,别名2" }),
        "创建");
      if (!ok) return;
      const term = val("mTerm");
      const scopeType = document.getElementById("mScopeType").value;
      const scopeId = val("mScopeId");
      const meaning = val("mMeaning");
      if (!term) {
        toast("词条不能为空", { type: "error" });
        return;
      }
      if (!scopeId) {
        toast("作用域 ID 不能为空", { type: "error" });
        return;
      }
      if (!meaning) {
        toast("释义不能为空", { type: "error" });
        return;
      }
      const confidence = parseFloat(val("mConfidence"));
      const aliases = val("mAliases")
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
      await api.post("jargon-action", {
        action: "create_entry",
        term,
        scope_type: scopeType,
        scope_id: scopeId,
        meaning,
        confidence: Number.isFinite(confidence) ? confidence : undefined,
        aliases: aliases.length ? aliases : undefined,
      });
      toast("已创建", { type: "success" });
      loadList();
      loadSideStats();
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  /* ---------- 导出：拉取筛选后的全量 JSON，客户端生成 Blob 下载 ---------- */
  async function exportJargons() {
    try {
      const data = await api.get("jargon-export", {
        search: current.search || undefined,
        scope_type: current.scope || undefined,
        status: current.status || undefined,
      });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "jargon_export.json";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast("导出成功", { type: "success" });
    } catch (e) {
      toast(api.errorOf(e).message, { type: "error" });
    }
  }

  /* ---------- 事件绑定 ---------- */

  /* 状态 / 作用域 seg（事件委托） */
  $(".jg-filter").addEventListener("click", (e) => {
    const seg = e.target.closest(".seg-item");
    if (!seg) return;
    const isStatus = seg.parentElement.classList.contains("status-seg");
    seg.parentElement.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
    seg.classList.add("active");
    if (isStatus) {
      current.status = seg.dataset.status || "";
    } else {
      current.scope = seg.dataset.scope || "";
    }
    current.page = 1;
    loadList();
  });

  /* 搜索框（防抖 350ms）：委托到 document，topbar 每次切换重建也不丢绑定 */
  document.addEventListener("input", (e) => {
    if (!e.target || e.target !== document.querySelector("#topbar .input-box input")) return;
    const box = e.target;
    clearTimeout(box._hzDebounce);
    box._hzDebounce = setTimeout(() => {
      current.search = box.value.trim();
      current.page = 1;
      loadList();
    }, 350);
  });

  /* 顶栏按钮：导出 / 新建词条（委托到 document，topbar 重建也不丢绑定） */
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn || !document.querySelector("#topbar").contains(btn)) return;
    if (btn.textContent.includes("导出")) exportJargons();
    if (btn.textContent.includes("新建词条")) createEntry();
  });

  /* 列表：卡片点击 / 卡片内快捷操作 */
  listEl.addEventListener("click", async (e) => {
    const card = e.target.closest(".jg-card");
    if (!card) return;
    const id = Number(card.dataset.id);
    const actBtn = e.target.closest("[data-act]");
    if (actBtn) {
      if (actBtn.dataset.act === "edit") {
        openDrawer(id);
      } else if (actBtn.dataset.act === "toggle") {
        try {
          const enabled = actBtn.title === "停用" ? 0 : 1;
          await api.post("jargon-action", { id, action: "update_entry", enabled });
          toast("操作成功", { type: "success" });
          loadList();
          loadSideStats();
        } catch (err) {
          toast(api.errorOf(err).message, { type: "error" });
        }
      }
      return;
    }
    openDrawer(id);
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
  $("#jg-drawerClose").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  /* 抽屉义项操作（事件委托） */
  drawerBody.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-sense-action]");
    if (!btn) return;
    const senseItem = btn.closest(".sense-item");
    onSenseAction(btn.dataset.senseAction, Number(senseItem.dataset.senseId));
  });

  /* 抽屉底部词条操作（事件委托） */
  footEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-action]");
    if (!btn || btn.style.display === "none") return;
    onEntryAction(btn.dataset.action);
  });

  /* ---------- 启动 ---------- */
  loadList();
  loadSideStats();

} };

