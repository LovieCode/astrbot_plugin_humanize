/**
 * 共享 UI 行为：滚动显现、数字滚动、滑杆填充、侧边栏渲染、顶栏渲染。
 * 依赖：icons.js（HZ.icon）
 */
(function (global) {
  /* ---------- 滚动显现 ---------- */
  function initReveal() {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("visible");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.08 }
    );
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
  }

  /* ---------- 数字滚动 ---------- */
  function animateCount(el) {
    const target = parseFloat(el.dataset.count);
    const decimals = parseInt(el.dataset.decimal || "0", 10);
    const suffix = el.dataset.suffix || "";
    const dur = 1300;
    const start = performance.now();
    (function tick(now) {
      const p = Math.min((now - start) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals) + suffix;
      if (p < 1) requestAnimationFrame(tick);
    })(start);
  }

  function initCounters(scope) {
    const root = scope || document;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.querySelectorAll("[data-count]").forEach(animateCount);
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.3 }
    );
    root.querySelectorAll("[data-count-parent]").forEach((el) => io.observe(el));
  }

  /* ---------- 进度条宽度动画 ---------- */
  function initBars() {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          e.target.querySelectorAll(".bar i").forEach((bar) => {
            bar.style.width = bar.dataset.w + "%";
          });
          io.unobserve(e.target);
        });
      },
      { threshold: 0.3 }
    );
    document.querySelectorAll("[data-bars]").forEach((el) => io.observe(el));
  }

  /* ---------- 滑杆 ---------- */
  function initRanges() {
    document.querySelectorAll(".range").forEach((range) => {
      const out = range.parentElement.querySelector(".range-val");
      function update() {
        const pct = ((range.value - range.min) / (range.max - range.min)) * 100;
        range.style.setProperty("--fill", pct + "%");
        if (out) out.textContent = range.value;
      }
      range.addEventListener("input", update);
      update();
    });
  }

  /* ---------- 侧边栏 ---------- */
  const NAV_GROUPS = [
    {
      label: "总览",
      items: [{ id: "dashboard", name: "仪表盘", icon: "dashboard", href: "/plugin-page/astrbot_plugin_humanize/dashboard" }],
    },
    {
      label: "能力",
      items: [
        { id: "memory", name: "长期记忆", icon: "memory", href: "/plugin-page/astrbot_plugin_humanize/memory" },
        { id: "jargon", name: "黑话词库", icon: "jargon", href: "/plugin-page/astrbot_plugin_humanize/jargon" },
        { id: "examples", name: "回复样例", icon: "chat", href: "/plugin-page/astrbot_plugin_humanize/examples" },
        { id: "context", name: "上下文追踪", icon: "file", href: "/plugin-page/astrbot_plugin_humanize/context" },
      ],
    },
    {
      label: "管理",
      items: [
        { id: "prompts", name: "提示词模板", icon: "edit", href: "/plugin-page/astrbot_plugin_humanize/prompts" },
        { id: "settings", name: "设置", icon: "settings", href: "/plugin-page/astrbot_plugin_humanize/settings" },
      ],
    },
  ];

  /**
   * 渲染侧边栏。
   * @param {string} activeId 当前页面导航 id
   */
  function renderSidebar(activeId) {
    const host = document.getElementById("sidebar");
    if (!host) return;
    const avatarSrc = (() => {
      const path = String(window.location.pathname || "");
      const match = path.match(/^\/api\/plugin\/page\/content\/([^/]+)\/([^/]+)\//);
      if (match) {
        const params = new URLSearchParams(window.location.search);
        const token = params.get("asset_token") || "";
        const theme = params.get("theme") || "";
        const query = new URLSearchParams();
        if (token) query.set("asset_token", token);
        if (theme) query.set("theme", theme);
        const qs = query.toString();
        return "/api/plugin/page/content/" + match[1] + "/" + match[2] + "/assets/lovie_avatar.png" + (qs ? "?" + qs : "");
      }
      return "assets/lovie_avatar.png";
    })();
    const nav = NAV_GROUPS.map((group) => {
      const items = group.items
        .map((item) => {
          const badge = item.badge ? `<span class="nav-badge">${item.badge}</span>` : "";
          const active = item.id === activeId ? " active" : "";
          return `<a class="nav-item${active}" href="${item.href}" data-nav="${item.id}">${HZ.icon(item.icon)}${item.name}${badge}</a>`;
        })
        .join("");
      return `<div class="nav-label">${group.label}</div>${items}`;
    }).join("");

    host.innerHTML = `
      <div class="brand">
        <img class="brand-avatar" src="${avatarSrc}" alt="洛薇" />
        <div>
          <div class="brand-name">洛薇 Lovie</div>
          <div class="brand-sub">Humanize v0.2.0</div>
        </div>
      </div>
      <nav class="side-nav">${nav}</nav>
      <div class="sidebar-footer">
        <div class="status-card">
          <span class="status-dot"></span>
          <div>
            <div class="status-text">运行正常</div>
            <div class="status-sub">已陪伴 36 天</div>
          </div>
        </div>
      </div>`;
  }

  /* ---------- 轻提示 ---------- */
  /**
   * 右下角轻提示，自动消失。
   * @param {string} message 提示文本
   * @param {{type?: "success"|"error"|"info"}} [opts] 类型，默认 info
   */
  function toast(message, opts) {
    const type = (opts && opts.type) || "info";
    let host = document.querySelector(".toast-host");
    if (!host) {
      host = document.createElement("div");
      host.className = "toast-host";
      document.body.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `toast toast-${type}`;
    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.innerHTML = HZ.icon(type === "success" ? "check" : type === "error" ? "alert" : "info", 15);
    const text = document.createElement("span");
    text.textContent = String(message || "");
    el.appendChild(icon);
    el.appendChild(text);
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.remove(), 300);
    }, 2600);
  }

  /* ---------- 确认框 ---------- */
  /**
   * 基于 .modal 的确认框。
   * @param {{title?: string, text: string, danger?: boolean, confirmText?: string, cancelText?: string, onConfirm: () => void}} opts
   */
  function confirm(opts) {
    const o = opts || {};
    const mask = document.createElement("div");
    mask.className = "modal-mask";
    mask.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <div class="modal-title">${o.title || "确认操作"}</div>
        <div class="modal-text"></div>
        <div class="modal-actions">
          <button class="btn btn-ghost" data-act="cancel">${o.cancelText || "取消"}</button>
          <button class="btn ${o.danger ? "btn-danger" : "btn-primary"}" data-act="ok">${o.confirmText || "确定"}</button>
        </div>
      </div>`;
    mask.querySelector(".modal-text").textContent = String(o.text || "");
    const close = (act) => {
      mask.classList.remove("show");
      setTimeout(() => mask.remove(), 220);
      if (act === "ok" && typeof o.onConfirm === "function") o.onConfirm();
    };
    mask.addEventListener("click", (e) => {
      if (e.target === mask || e.target.closest("[data-act]")) {
        close(e.target.closest("[data-act]") ? e.target.closest("[data-act]").dataset.act : "cancel");
      }
    });
    document.body.appendChild(mask);
    requestAnimationFrame(() => mask.classList.add("show"));
  }

  /* ---------- 空态 / 错误条工厂 ---------- */
  /**
   * 生成空态元素（含可选重试按钮）。
   * @param {{text?: string, icon?: string, retry?: () => void, retryText?: string}} [opts]
   * @returns {HTMLElement} .empty 元素，可直接 appendChild
   */
  function initEmpty(opts) {
    const o = opts || {};
    const el = document.createElement("div");
    el.className = "empty";
    const icon = document.createElement("div");
    icon.className = "empty-icon";
    icon.innerHTML = HZ.icon(o.icon || "mood", 30);
    const text = document.createElement("div");
    text.className = "empty-text";
    text.textContent = String(o.text || "暂无数据");
    el.appendChild(icon);
    el.appendChild(text);
    if (typeof o.retry === "function") {
      const btn = document.createElement("button");
      btn.className = "btn btn-ghost btn-sm empty-retry";
      btn.textContent = o.retryText || "重试";
      btn.addEventListener("click", () => o.retry());
      el.appendChild(btn);
    }
    return el;
  }

  /**
   * 生成错误条元素（含重试按钮）。
   * @param {{message?: string, retry?: () => void}} [opts]
   * @returns {HTMLElement} .errbar 元素，可直接 appendChild
   */
  function initErrbar(opts) {
    const o = opts || {};
    const el = document.createElement("div");
    el.className = "errbar";
    const icon = document.createElement("span");
    icon.className = "errbar-icon";
    icon.innerHTML = HZ.icon("alert", 15);
    const text = document.createElement("span");
    text.className = "errbar-text";
    text.textContent = String(o.message || "加载失败");
    el.appendChild(icon);
    el.appendChild(text);
    if (typeof o.retry === "function") {
      const btn = document.createElement("button");
      btn.className = "errbar-retry";
      btn.textContent = "重试";
      btn.addEventListener("click", () => o.retry());
      el.appendChild(btn);
    }
    return el;
  }

  /* ---------- 顶栏 ---------- */
  /**
   * 渲染页面顶栏。
   * @param {object} opts { title, sub, search: bool|placeholder, actions: [{label, icon, variant}] }
   */
  function renderTopbar(opts) {
    const host = document.getElementById("topbar");
    if (!host) return;
    const search = opts.search
      ? `<div class="input-box" style="width:230px">${HZ.icon("search")}<input type="text" placeholder="${
          typeof opts.search === "string" ? opts.search : "搜索…"
        }" /></div>`
      : "";
    const actions = (opts.actions || [])
      .map(
        (a) =>
          `<button class="btn btn-${a.variant || "ghost"}${a.size ? " btn-" + a.size : ""}" data-topbar-action="${
            a.key || ""
          }">${a.icon ? HZ.icon(a.icon) : ""}${a.label}</button>`
      )
      .join("");
    // 刷新按钮：所有数据页通用，点击触发 onRefresh
    const refreshBtn = opts.onRefresh
      ? `<button class="btn btn-ghost" data-topbar-action="refresh" title="刷新">${HZ.icon("refresh")}</button>`
      : "";
    host.innerHTML = `
      <div>
        <div class="page-title">${opts.title}</div>
        <div class="page-sub">${opts.sub || ""}</div>
      </div>
      <div class="topbar-actions">${search}${actions}${refreshBtn}</div>`;
    // 刷新按钮事件（委托，topbar 重建也不丢）
    if (opts.onRefresh) {
      host.querySelector('[data-topbar-action="refresh"]')?.addEventListener("click", () => opts.onRefresh());
    }
  }

  global.HZ = global.HZ || {};
  global.HZ.topbars = global.HZ.topbars || {};
  Object.assign(global.HZ, {
    initReveal,
    initCounters,
    initBars,
    initRanges,
    renderSidebar,
    renderTopbar,
    NAV_GROUPS,
    toast,
    confirm,
    initEmpty,
    initErrbar,
  });
})(window);
