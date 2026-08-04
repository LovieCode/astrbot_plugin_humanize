/**
 * View: Prompts — 提示词模板页独有交互（静态预览，mock 数据）
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  HZ.renderSidebar("prompts");
  HZ.renderTopbar({
    title: "提示词模板",
    sub: "5 个全局模板 · 修改立即生效并记录审计",
    search: "",
    actions: [{ label: "全部重置", icon: "refresh", variant: "ghost" }],
  });
  HZ.initReveal();

  /* 模板切换（预览交互） */
  document.querySelectorAll(".pt-item").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".pt-item").forEach((i) => i.classList.remove("active"));
      item.classList.add("active");
    });
  });

  /* 编辑脏标记 + 字数统计（预览交互） */
  const textarea = document.getElementById("ptTextarea");
  const dirty = document.getElementById("ptDirty");
  const counter = document.getElementById("ptCharCount");
  function refreshCount() {
    counter.textContent = textarea.value.length + " 字";
  }
  textarea.addEventListener("input", () => {
    dirty.classList.add("on");
    refreshCount();
  });
  refreshCount();

  /* 变量芯片点击复制（预览交互） */
  document.querySelectorAll(".pt-var-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const original = chip.textContent;
      chip.textContent = "已复制";
      setTimeout(() => { chip.textContent = original; }, 900);
    });
  });
})();
