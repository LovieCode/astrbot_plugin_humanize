/**
 * View: Memory — 长期记忆页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
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

  /* ---------- 数据源 Tab：长期记忆 / 会话提交 ---------- */
  document.querySelectorAll(".source-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".source-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const isMem = tab.dataset.source === "memories";
      document.getElementById("panelMemories").style.display = isMem ? "" : "none";
      document.getElementById("panelSessions").style.display = isMem ? "none" : "";
      document.getElementById("memoryFilters").style.display = isMem ? "" : "none";
    });
  });

  /* ---------- 筛选（预览交互） ---------- */
  document.querySelectorAll(".seg-item").forEach((seg) => {
    seg.addEventListener("click", () => {
      seg.parentElement.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });
  document.querySelectorAll(".pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      const group = pill.dataset.group;
      document.querySelectorAll(`.pill[data-group="${group}"]`).forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
    });
  });

  /* ---------- 详情抽屉 ---------- */
  const drawer = document.getElementById("drawer");
  const mask = document.getElementById("drawerMask");

  function openDrawer(card) {
    document.querySelectorAll(".mem-card").forEach((c) => c.classList.remove("selected"));
    if (card) card.classList.add("selected");
    drawer.classList.add("open");
    mask.classList.add("open");
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    document.querySelectorAll(".mem-card").forEach((c) => c.classList.remove("selected"));
  }

  document.querySelectorAll(".mem-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".icon-btn") || e.target.closest(".btn")) return;
      openDrawer(card);
    });
  });
  mask.addEventListener("click", closeDrawer);
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  /* ---------- 分页（预览交互） ---------- */
  document.querySelectorAll(".pg-btn[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pg-btn[data-page]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    });
  });
})();
