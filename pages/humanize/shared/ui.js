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
      items: [{ id: "dashboard", name: "仪表盘", icon: "dashboard", href: "dashboard.html" }],
    },
    {
      label: "能力",
      items: [
        { id: "memory", name: "长期记忆", icon: "memory", href: "memory.html", badge: "128" },
        { id: "jargon", name: "黑话词库", icon: "jargon", href: "jargon.html", badge: "42" },
        { id: "examples", name: "回复样例", icon: "chat", href: "examples.html" },
        { id: "context", name: "上下文追踪", icon: "file", href: "context.html" },
      ],
    },
    {
      label: "管理",
      items: [
        { id: "prompts", name: "提示词模板", icon: "edit", href: "prompts.html" },
        { id: "settings", name: "设置", icon: "settings", href: "settings.html" },
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
        <img class="brand-avatar" src="assets/lovie_avatar.png" alt="洛薇" />
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
          `<button class="btn btn-${a.variant || "ghost"}${a.size ? " btn-" + a.size : ""}">${
            a.icon ? HZ.icon(a.icon) : ""
          }${a.label}</button>`
      )
      .join("");
    host.innerHTML = `
      <div>
        <div class="page-title">${opts.title}</div>
        <div class="page-sub">${opts.sub || ""}</div>
      </div>
      <div class="topbar-actions">${search}${actions}</div>`;
  }

  global.HZ = global.HZ || {};
  Object.assign(global.HZ, {
    initReveal,
    initCounters,
    initBars,
    initRanges,
    renderSidebar,
    renderTopbar,
    NAV_GROUPS,
  });
})(window);
