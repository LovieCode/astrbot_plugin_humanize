/**
 * View: Settings — 设置页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  HZ.renderSidebar("settings");
  HZ.renderTopbar({
    title: "设置",
    sub: "插件配置 · 修改后保存生效",
    search: "",
    actions: [{ label: "保存全部", icon: "check_simple", variant: "primary" }],
  });
  HZ.initReveal();

  /* 开关（预览交互） */
  document.querySelectorAll(".switch").forEach((sw) => {
    sw.addEventListener("click", () => sw.classList.toggle("on"));
  });

  /* 分段控件（预览交互） */
  document.querySelectorAll(".st-seg-item").forEach((seg) => {
    seg.addEventListener("click", () => {
      seg.parentElement.querySelectorAll(".st-seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });

  /* 滑杆数值（预览交互） */
  document.querySelectorAll(".st-slider input[type=range]").forEach((range) => {
    const val = range.parentElement.querySelector(".st-slider-val");
    range.addEventListener("input", () => { val.textContent = range.value; });
  });

  /* 顶部标签分页切换（预览交互）：一次只显示一组 */
  document.querySelectorAll(".st-nav-item").forEach((nav) => {
    nav.addEventListener("click", () => {
      document.querySelectorAll(".st-nav-item").forEach((n) => n.classList.remove("active"));
      nav.classList.add("active");
      const targetId = nav.dataset.target;
      document.querySelectorAll(".st-groups-col > .card").forEach((card) => {
        card.style.display = card.id === targetId ? "" : "none";
      });
    });
  });
  /* 初始只显示常规组 */
  document.querySelectorAll(".st-groups-col > .card").forEach((card) => {
    card.style.display = card.id === "st-general" ? "" : "none";
  });
})();
