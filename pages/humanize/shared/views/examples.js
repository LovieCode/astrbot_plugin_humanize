/**
 * View: Examples — 回复样例页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  HZ.renderSidebar("examples");
  HZ.renderTopbar({
    title: "回复样例",
    sub: "审核通过后作为 few-shot 表达参考 · 绝不直接返回旧样例回复",
    search: "搜索标题、话题、关键词…",
    actions: [{ label: "新建样例", icon: "plus", variant: "primary" }],
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
    document.querySelectorAll(".ex-card").forEach((c) => c.classList.remove("selected"));
    if (card) card.classList.add("selected");
    drawer.classList.add("open");
    mask.classList.add("open");
  }
  function closeDrawer() {
    drawer.classList.remove("open");
    mask.classList.remove("open");
    document.querySelectorAll(".ex-card").forEach((c) => c.classList.remove("selected"));
  }

  document.querySelectorAll(".ex-card").forEach((card) => {
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
