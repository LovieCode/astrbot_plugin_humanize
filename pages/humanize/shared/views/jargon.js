/**
 * View: Jargon — 黑话词库页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  HZ.renderSidebar("jargon");
  HZ.renderTopbar({
    title: "黑话词库",
    sub: "LLM 词条一律不可信 · 验证、限长并保留证据后才可注入",
    search: "搜索词条、别名、释义…",
    actions: [
      { label: "导出", icon: "export", variant: "ghost" },
      { label: "新建词条", icon: "plus", variant: "primary" },
    ],
  });
  HZ.initReveal();

  /* 筛选（预览交互） */
  document.querySelectorAll(".seg-item").forEach((seg) => {
    seg.addEventListener("click", () => {
      seg.parentElement.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });

  /* 详情抽屉 */
  const drawer = document.getElementById("drawer");
  const mask = document.getElementById("drawerMask");

  function openDrawer(card) {
    document.querySelectorAll(".jg-card").forEach((c) => c.classList.remove("selected"));
    if (card) card.classList.add("selected");
    drawer.classList.add("open");
    mask.classList.add("open");
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    document.querySelectorAll(".jg-card").forEach((c) => c.classList.remove("selected"));
  }

  document.querySelectorAll(".jg-card").forEach((card) => {
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

  /* 分页（预览交互） */
  document.querySelectorAll(".pg-btn[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".pg-btn[data-page]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
    });
  });
})();
