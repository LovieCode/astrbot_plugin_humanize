/**
 * 单页应用控制器（构建产物）。
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js, shared/views/*.js
 * 职责：
 *   - 侧边栏渲染一次（所有视图共用）
 *   - 视图切换：显示对应 section + 首次激活时调用 HZ.views.<name>.init()
 *   - 防重复初始化（每个视图只 init 一次）
 *   - 导航点击用事件委托（视图 init 可能重渲染侧边栏，委托不依赖绑定时机）
 */
(function () {
  const VIEWS = ["dashboard", "memory", "jargon", "examples", "context", "prompts", "policy", "settings"];
  const sections = {};
  VIEWS.forEach((name) => {
    const el = document.getElementById("view-" + name);
    if (el) sections[name] = el;
  });

  function show(name) {
    if (!sections[name]) return;
    if (window.HZ && HZ.topbars && HZ.topbars[name]) {
      try {
        HZ.renderTopbar(HZ.topbars[name]);
      } catch (e) {
        console.error("Humanize topbar render failed:", name, e);
      }
    }
    VIEWS.forEach((n) => {
      const el = sections[n];
      if (!el) return;
      el.classList.toggle("active", n === name);
      if (n === name && !el.dataset.inited && window.HZ && HZ.views && HZ.views[name]) {
        el.dataset.inited = "1";
        try {
          HZ.views[name].init();
        } catch (e) {
          console.error("Humanize view init failed:", name, e);
        }
      }
    });
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.nav === name);
    });
    // 视图已初始化后再切换时才刷新数据（首次由 init 自己加载）
    if (sections[name] && sections[name].dataset.inited) {
      refreshView(name);
    }
  }

  // 当前激活视图 + 视图切换/窗口聚焦时自动刷新
  let activeView = "dashboard";
  function refreshView(name) {
    const section = sections[name];
    if (!section || !section.dataset.inited) return; // 未初始化（首次加载由 init 负责）
    if (!window.HZ || !HZ.views || !HZ.views[name]) return;
    const topbar = HZ.topbars && HZ.topbars[name];
    if (topbar && typeof topbar.onRefresh === "function") {
      try {
        topbar.onRefresh();
      } catch (e) {
        console.error("Humanize refresh failed:", name, e);
      }
    }
  }
  function setActive(name) {
    activeView = name;
  }
  // 窗口/标签页重新获得焦点时刷新当前视图
  window.addEventListener("focus", () => refreshView(activeView));
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshView(activeView);
  });

  if (window.HZ && HZ.renderSidebar) {
    HZ.renderSidebar("dashboard");
  }

  /* 事件委托：侧边栏导航点击（视图 init 重渲染侧边栏也不丢绑定） */
  document.addEventListener("click", (e) => {
    const item = e.target && e.target.closest ? e.target.closest(".nav-item") : null;
    if (!item) return;
    const name = item.dataset.nav;
    if (name && sections[name]) {
      e.preventDefault();
      setActive(name);
      show(name);
    }
  });

  show("dashboard");
})();
