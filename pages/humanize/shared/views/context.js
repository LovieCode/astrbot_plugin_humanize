/**
 * View: Context — 上下文追踪页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  HZ.renderSidebar("context");
  HZ.renderTopbar({
    title: "上下文追踪",
    sub: "每次 LLM 请求的区块组装与 token 预算全记录",
    search: "搜索请求 ID、消息内容…",
    actions: [],
  });
  HZ.initReveal();

  /* 运行列表切换（预览交互） */
  document.querySelectorAll(".cx-run").forEach((run) => {
    run.addEventListener("click", () => {
      document.querySelectorAll(".cx-run").forEach((r) => r.classList.remove("active"));
      run.classList.add("active");
    });
  });

  /* 筛选（预览交互） */
  document.querySelectorAll(".seg-item").forEach((seg) => {
    seg.addEventListener("click", () => {
      seg.parentElement.querySelectorAll(".seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });
})();
