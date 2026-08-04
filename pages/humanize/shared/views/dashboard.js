/**
 * View: Dashboard — 仪表盘独有交互
 * 依赖：shared/icons.js, shared/ui.js
 */
(function () {
  /* 侧边栏 + 顶栏 */
  HZ.renderSidebar("dashboard");
  HZ.renderTopbar({
    title: "仪表盘",
    sub: "2026年3月30日 星期一 · 今天也要元气满满哦",
    search: "搜索记忆、黑话、样例…",
    actions: [
      { label: "导出", icon: "export", variant: "ghost" },
      { label: "新建记忆", icon: "plus", variant: "primary" },
    ],
  });

  /* 通用行为 */
  HZ.initReveal();
  HZ.initCounters();
  HZ.initBars();
  HZ.initRanges();

  /* 回复动作占比环 */
  (function () {
    const ring = document.getElementById("actionRing");
    const num = document.getElementById("actionNum");
    if (!ring || !num) return;
    const value = 85; /* Reply 占比 85% */
    const C = 2 * Math.PI * 58;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          io.unobserve(e.target);
          const start = performance.now();
          const dur = 1400;
          (function tick(now) {
            const p = Math.min((now - start) / dur, 1);
            const eased = 1 - Math.pow(1 - p, 3);
            ring.style.strokeDashoffset = C * (1 - (value / 100) * eased);
            num.textContent = Math.round(value * eased) + "%";
            if (p < 1) requestAnimationFrame(tick);
          })(start);
        });
      },
      { threshold: 0.4 }
    );
    io.observe(ring);
  })();

  /* 协议 Tab 切换 */
  document.querySelectorAll(".p-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".p-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const isReply = tab.dataset.tab === "reply";
      document.getElementById("protocolReply").style.display = isReply ? "" : "none";
      document.getElementById("protocolNoreply").style.display = isReply ? "none" : "";
    });
  });
})();
