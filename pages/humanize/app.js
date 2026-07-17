(function initializeHumanizeApp(global) {
  "use strict";

  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;
  const Api = global.HumanizeApi;

  if (!Core || !Ui || !Api) {
    console.error("[HumanizeApp] 缺少依赖（HumanizeCore/HumanizeUi/HumanizeApi），启动中止。");
    return;
  }

  /**
   * 8 个导航项（明确不含 Control：persona/state/behavior/expression）。
   * 与 PLAN.md 保留范围一致；与另一个 agent 裁剪 Control 后端协同。
   */
  const NAV_ITEMS = [
    { key: "overview", label: "运行总览", icon: "house", section: "总览" },
    { key: "jargons", label: "黑话词库", icon: "book-open", section: "词库与样例" },
    { key: "memory", label: "长期记忆", icon: "brain", section: "词库与样例" },
    { key: "examples", label: "回复样例", icon: "messages-square", section: "词库与样例" },
    { key: "context", label: "上下文追踪", icon: "scan-search", section: "协议与调试" },
    { key: "protocol", label: "协议监控", icon: "chart-no-axes-combined", section: "协议与调试" },
    { key: "prompts", label: "提示词模板", icon: "file-text", section: "配置" },
    { key: "settings", label: "设置", icon: "settings", section: "配置" },
  ];

  /** 路由元信息：topbar 标题与副标题。 */
  const ROUTE_TITLES = {
    overview: { title: "运行总览", subtitle: "插件运行状态、Provider 观测与上下文概览" },
    jargons: { title: "黑话词库", subtitle: "管理词条、含义、别名与合并" },
    memory: { title: "长期记忆", subtitle: "OpenViking 记忆条目、召回调试与后台任务" },
    examples: { title: "回复样例", subtitle: "1-3 轮对话样例与召回测试" },
    context: { title: "上下文追踪", subtitle: "请求级上下文构建与插入段追踪" },
    protocol: { title: "协议监控", subtitle: "回复协议合规日志与 7 天趋势" },
    prompts: { title: "提示词模板", subtitle: "5 个核心模板的编辑与恢复" },
    settings: { title: "设置", subtitle: "插件公开配置与 Provider 观测" },
  };

  let currentView = null; // { key, instance }
  let viewEpoch = 0;
  let booting = false;

  /**
   * Resolve the current route key from location.hash.
   * @returns {string} Route key, defaults to "overview".
   */
  function getRouteFromHash() {
    const hash = (global.location.hash || "").replace(/^#\/?/, "");
    const [key] = hash.split("/");
    return key || "overview";
  }

  /**
   * Render sidebar nav items from NAV_ITEMS.
   * Section headers separate groups ("总览" / "词库与样例" / "协议与调试" / "配置").
   */
  function renderSidebar() {
    const nav = document.getElementById("sidebar-nav");
    if (!nav) return;
    nav.replaceChildren();
    let lastSection = null;
    NAV_ITEMS.forEach((item) => {
      if (item.section !== lastSection) {
        const sec = document.createElement("div");
        sec.className = "sidebar-nav-section";
        sec.textContent = item.section;
        nav.append(sec);
        lastSection = item.section;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "nav-item";
      btn.dataset.key = item.key;
      const ic = Core.icon(item.icon);
      ic.classList.add("nav-item-icon");
      btn.append(ic);
      const lab = document.createElement("span");
      lab.className = "nav-item-label";
      lab.textContent = item.label;
      btn.append(lab);
      btn.addEventListener("click", () => navigate(item.key));
      nav.append(btn);
    });
    Core.refreshIcons();
  }

  /**
   * Mark the active nav item via aria-current.
   * @param {string} key
   */
  function setActiveNav(key) {
    document.querySelectorAll(".nav-item").forEach((el) => {
      if (el.dataset.key === key) {
        el.setAttribute("aria-current", "page");
      } else {
        el.removeAttribute("aria-current");
      }
    });
  }

  /**
   * Reset topbar title/subtitle and clear actions slot.
   * Views can later call ctx.setTopbarActions() to inject their own actions.
   * @param {string} key
   */
  function setTopbar(key) {
    const info = ROUTE_TITLES[key] || { title: key, subtitle: "" };
    const title = document.getElementById("topbar-title");
    const sub = document.getElementById("topbar-subtitle");
    const actions = document.getElementById("topbar-actions");
    if (title) title.textContent = info.title;
    if (sub) sub.textContent = info.subtitle;
    if (actions) actions.replaceChildren();
  }

  /**
   * Build a per-view context object passed to mount().
   * @param {string} viewKey
   * @param {number} epoch
   * @returns {object}
   */
  function makeContext(viewKey, epoch) {
    return {
      viewKey,
      epoch,
      /** True if another navigation has superseded this view. */
      isStale: () => epoch !== viewEpoch,
      api: Api,
      ui: Ui,
      core: Core,
      /** Navigate to another view. */
      navigate,
      /** Shorthand for Ui.toast. */
      toast: Ui.toast,
      /** Replace topbar actions slot with given nodes. */
      setTopbarActions(nodes) {
        const actions = document.getElementById("topbar-actions");
        if (!actions) return;
        actions.replaceChildren();
        const list = Array.isArray(nodes) ? nodes : [nodes];
        list.forEach((n) => {
          if (n instanceof HTMLElement) actions.append(n);
        });
      },
      /** Override topbar subtitle (e.g. show record count). */
      setTopbarSubtitle(text) {
        const sub = document.getElementById("topbar-subtitle");
        if (sub) sub.textContent = String(text ?? "");
      },
      /** Update sidebar footer status indicator. */
      setStatus(state, text) {
        const dot = document.getElementById("sidebar-status-dot");
        const txt = document.getElementById("sidebar-status-text");
        if (dot) dot.dataset.state = state || "ok";
        if (txt) txt.textContent = text || "就绪";
      },
    };
  }

  /**
   * Navigate to a view by key. Unmounts current view, mounts target view.
   * Honors epoch to drop stale async mounts.
   * @param {string} key
   */
  async function navigate(key) {
    if (!NAV_ITEMS.some((item) => item.key === key)) {
      key = "overview";
    }
    if (currentView && currentView.key === key) {
      if (global.location.hash !== `#/${key}`) {
        global.location.hash = `#/${key}`;
      }
      return;
    }

    viewEpoch += 1;
    const myEpoch = viewEpoch;

    // Unmount current view
    if (currentView && currentView.instance) {
      try {
        await Promise.resolve(currentView.instance.unmount?.());
      } catch (err) {
        console.warn(`[HumanizeApp] unmount error for "${currentView.key}":`, err);
      }
    }
    currentView = null;

    setActiveNav(key);
    setTopbar(key);
    if (global.location.hash !== `#/${key}`) {
      global.location.hash = `#/${key}`;
    }

    const root = document.getElementById("view-root");
    if (!root) return;
    root.replaceChildren();
    const loading = Ui.createLoading("正在加载…");
    loading.style.padding = "var(--sp-8)";
    loading.style.justifyContent = "center";
    root.append(loading);

    const viewFactory = global.HumanizeViews?.[key];
    if (!viewFactory || typeof viewFactory.mount !== "function") {
      root.replaceChildren();
      root.append(Ui.createEmptyState({
        title: "视图未就绪",
        message: `视图 "${key}" 尚未加载或注册。请检查 pages/humanize/views/${key}.js。`,
      }));
      return;
    }

    try {
      const ctx = makeContext(key, myEpoch);
      const instance = await Promise.resolve(viewFactory.mount(root, ctx));
      // Drop mount result if a newer navigation has occurred.
      if (myEpoch !== viewEpoch) {
        await Promise.resolve(instance?.unmount?.());
        return;
      }
      currentView = { key, instance };
    } catch (err) {
      if (myEpoch !== viewEpoch) return;
      console.error(`[HumanizeApp] view "${key}" mount failed:`, err);
      root.replaceChildren();
      root.append(Ui.createAlert({
        variant: "danger",
        title: "加载失败",
        message: (err && err.message) || String(err),
      }));
      Ui.toastError((err && err.message) || `视图 "${key}" 加载失败`);
    }
  }

  /**
   * Boot the app: render sidebar, wait for bridge, navigate to initial route.
   */
  async function boot() {
    if (booting) return;
    booting = true;

    renderSidebar();

    const app = document.getElementById("app");
    if (app) app.dataset.viewLoading = "false";

    // Wait for AstrBot bridge readiness with timeout.
    try {
      await Api.ready();
      const txt = document.getElementById("sidebar-status-text");
      if (txt) txt.textContent = "已连接";
    } catch (err) {
      const dot = document.getElementById("sidebar-status-dot");
      const txt = document.getElementById("sidebar-status-text");
      if (dot) dot.dataset.state = "error";
      if (txt) txt.textContent = "未连接";
      Ui.toastError((err && err.message) || "AstrBot 桥接未就绪");
    }

    // Load plugin version for sidebar footer (non-blocking).
    try {
      const overview = await Api.getOverview();
      const versionEl = document.getElementById("sidebar-version");
      if (versionEl && overview && overview.version) {
        versionEl.textContent = `v${overview.version}`;
      }
    } catch (err) {
      // version load failure is non-fatal
    }

    // Initial route from hash (or default to overview).
    const initial = getRouteFromHash();
    await navigate(initial);

    // React to back/forward navigation.
    global.addEventListener("hashchange", () => {
      const key = getRouteFromHash();
      navigate(key);
    });
  }

  global.HumanizeApp = Object.freeze({
    navigate,
    boot,
    /** @returns {string|null} current view key */
    get currentKey() { return currentView ? currentView.key : null; },
    /** @returns {object[]} nav items (read-only) */
    get navItems() { return NAV_ITEMS.slice(); },
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);