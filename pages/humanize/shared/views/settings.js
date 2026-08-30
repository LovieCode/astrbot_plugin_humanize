/**
 * View: Settings — 设置页交互（真实接口版）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET settings / POST settings / GET chat-providers / GET memory-providers
 * 降级：api.js 未加载时停留在静态预览（Tab 切换、开关、分段、滑杆等预览交互仍可用）；
 *       POST settings 未落地（404）时提示「保存接口尚未就绪」。
 * 安全：所有动态文本一律通过 textContent 写入。
 */
HZ.views["settings"] = { init: function () {

  HZ.topbars["settings"] = {
    title: "设置",
    sub: "插件配置 · 修改后保存生效",
    search: "",
    actions: [{ label: "保存全部", icon: "check_simple", variant: "primary" }],
  };
HZ.renderTopbar(HZ.topbars["settings"]);
  HZ.initReveal();

  const $ = (sel) => document.querySelector(sel);

  /* ---------- 预览交互（api.js 缺失时依然可用） ---------- */
  document.querySelectorAll(".switch").forEach((sw) => {
    sw.addEventListener("click", () => sw.classList.toggle("on"));
  });

  document.querySelectorAll(".st-seg-item").forEach((seg) => {
    seg.addEventListener("click", () => {
      seg.parentElement.querySelectorAll(".st-seg-item").forEach((s) => s.classList.remove("active"));
      seg.classList.add("active");
    });
  });

  /* 滑杆：value → 百分比填充 + 0.xx 数值展示 */
  function updateSlider(sl) {
    const range = sl.querySelector('input[type="range"]');
    const val = sl.querySelector(".st-slider-val");
    if (!range) return;
    const pct = ((Number(range.value) - Number(range.min)) / (Number(range.max) - Number(range.min))) * 100;
    range.style.setProperty("--fill", pct + "%");
    if (val) val.textContent = (Number(range.value) / 100).toFixed(2);
  }
  document.querySelectorAll(".st-slider").forEach((sl) => {
    const range = sl.querySelector('input[type="range"]');
    if (!range) return;
    range.addEventListener("input", () => updateSlider(sl));
    updateSlider(sl);
  });

  /* 顶部标签分页切换（预览交互）：一次只显示一组（一个 tab 可对应多张卡） */
  function applyTab(targetId) {
    document.querySelectorAll(".st-groups-col > .card").forEach((card) => {
      card.style.display = card.id === targetId || card.dataset.tab === targetId ? "" : "none";
    });
  }
  document.querySelectorAll(".st-nav-item").forEach((nav) => {
    nav.addEventListener("click", () => {
      document.querySelectorAll(".st-nav-item").forEach((n) => n.classList.remove("active"));
      nav.classList.add("active");
      applyTab(nav.dataset.target);
    });
  });
  applyTab("st-general");

  /* 共享 API 层缺失：清空设置组/服务商 mock 内容，显示明确错误提示（幂等）；Tab 切换等纯 UI 交互保留 */
  function renderApiUnavailable() {
    const groups = $(".st-groups-col");
    if (groups) groups.innerHTML = "";
    const host = groups || document.querySelector(".main");
    if (!host || host.querySelector(".errbar[data-api-unavailable]")) return;
    const bar = document.createElement("div");
    bar.className = "errbar";
    bar.dataset.apiUnavailable = "1";
    bar.innerHTML =
      '<span class="errbar-icon">' +
      (window.HZ && HZ.icon ? HZ.icon("alert", 15) : "") +
      '</span><span class="errbar-text">共享 API 层未加载，无法显示真实数据</span>';
    host.parentNode.insertBefore(bar, host);
  }

  if (!window.HZ || !HZ.api) {
    console.error("共享 API 层（shared/api.js）未加载，无法获取真实数据");
    renderApiUnavailable();
    return;
  }
  const api = HZ.api;
  const toast = HZ.toast || ((msg) => console.log("[toast]", msg));

  /* ---------- 状态 ---------- */
  let initial = {}; // 最近一次加载/保存后的配置快照

  /* ---------- 小工具 ---------- */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function withIcon(node, name) {
    node.insertAdjacentHTML("afterbegin", HZ.icon(name));
  }

  /** 按初始值类型把控件原始值转成后端期望类型（bool/int/float/str/list）。 */
  function coerce(key, raw) {
    const base = initial[key];
    if (typeof base === "boolean") return raw === true || raw === "true" || raw === 1 || raw === "1";
    if (Array.isArray(base)) return Array.isArray(raw) ? raw : [];
    if (typeof base === "number") {
      const n = Number(raw);
      if (!Number.isFinite(n)) return base;
      return Number.isInteger(base) ? Math.round(n) : n;
    }
    return String(raw == null ? "" : raw);
  }

  /** 收集 6 组所有带 data-key 控件的当前值。 */
  function collectValues() {
    const values = {};
    document.querySelectorAll("[data-key]").forEach((node) => {
      const key = node.dataset.key;
      if (key === "memory_identity_secret_env") return; // 只读展示，不参与保存
      let raw;
      if (node.classList.contains("switch")) {
        raw = node.classList.contains("on");
      } else if (node.classList.contains("st-slider")) {
        const range = node.querySelector('input[type="range"]');
        raw = range ? Number(range.value) / 100 : 0;
      } else if (node.classList.contains("st-seg")) {
        const active = node.querySelector(".st-seg-item.active");
        raw = active ? active.dataset.value : "";
      } else if (node.tagName === "SELECT") {
        raw = node.value;
      } else if (node.tagName === "TEXTAREA") {
        raw = node.value.split("\n").map((s) => s.trim()).filter(Boolean);
      } else if (node.tagName === "INPUT") {
        raw = node.value;
      } else {
        return;
      }
      values[key] = coerce(key, raw);
    });
    return values;
  }

  /** 与初始值 diff，仅返回变化的 key。 */
  function changedValues(values) {
    const changed = {};
    Object.keys(values).forEach((k) => {
      if (JSON.stringify(values[k]) !== JSON.stringify(initial[k])) changed[k] = values[k];
    });
    return changed;
  }

  /* ---------- 回填 ---------- */
  function fillAll(data) {
    document.querySelectorAll(".switch[data-key]").forEach((sw) => {
      sw.classList.toggle("on", !!data[sw.dataset.key]);
    });
    document.querySelectorAll("input.st-input[data-key]").forEach((inp) => {
      const v = data[inp.dataset.key];
      inp.value = v == null ? "" : String(v);
    });
    document.querySelectorAll("textarea[data-key]").forEach((ta) => {
      const v = data[ta.dataset.key];
      ta.value = Array.isArray(v) ? v.join("\n") : v == null ? "" : String(v);
    });
    document.querySelectorAll(".st-slider[data-key]").forEach((sl) => {
      const range = sl.querySelector('input[type="range"]');
      if (!range) return;
      const v = Number(data[sl.dataset.key]);
      range.value = String(Math.round((Number.isFinite(v) ? v : 0) * 100));
      updateSlider(sl);
    });
    document.querySelectorAll(".st-seg[data-key]").forEach((seg) => {
      const cur = String(data[seg.dataset.key] == null ? "" : data[seg.dataset.key]);
      seg.querySelectorAll(".st-seg-item").forEach((s) => {
        s.classList.toggle("active", s.dataset.value === cur);
      });
    });
    const env = data.memory_identity_secret_env;
    const secretCode = $("#stSecretEnv code");
    if (secretCode && env) secretCode.textContent = String(env);
  }

  /** 用 memory-providers 的 chat/embedding/rerank 列表填充服务商下拉并选中当前值。 */
  function fillProviderSelects(memData) {
    const groups = [
      ["memory_extraction_provider_id", "chat", "未配置（提取不可用）"],
      ["memory_embedding_provider_id", "embedding", "未配置（纯关键词召回）"],
      ["memory_rerank_provider_id", "rerank", "未配置"],
      ["image_transcription_provider_id", "chat", "未配置（[图片] 占位）"],
    ];
    groups.forEach(([key, group, emptyLabel]) => {
      const sel = $('select[data-key="' + key + '"]');
      if (!sel) return;
      sel.innerHTML = "";
      const none = el("option", null, emptyLabel);
      none.value = "";
      sel.appendChild(none);
      const current = initial[key];
      const list = (memData && memData[group]) || [];
      let matched = false;
      list.forEach((p) => {
        const op = el("option", null, p.model ? p.model + " · " + p.id : p.id);
        op.value = p.id;
        sel.appendChild(op);
        if (p.id === current) matched = true;
      });
      if (current && !matched) {
        const op = el("option", null, "当前：" + current);
        op.value = String(current);
        sel.appendChild(op);
      }
      sel.value = current == null ? "" : String(current);
    });
  }

  /* ---------- 保存 ---------- */
  async function saveAll() {
    const values = collectValues();
    const changed = changedValues(values);
    if (!Object.keys(changed).length) {
      toast("没有需要保存的修改", { type: "info" });
      return;
    }
    try {
      await api.post("settings", { values: changed });
      initial = values; // 保存成功即更新本地快照
      toast("已保存，重启后生效", { type: "success" });
    } catch (e) {
      const err = api.errorOf(e);
      if (err.status === 404) {
        toast("保存接口尚未就绪", { type: "error" });
      } else {
        toast(err.message, { type: "error" });
      }
    }
  }

  /* ---------- 加载失败重试条 ---------- */
  function showRetry(msg) {
    let bar = $("#stRetry");
    if (!bar) {
      bar = el("div", "st-retry");
      bar.id = "stRetry";
      const text = el("span", "st-retry-text");
      const btn = el("button", "btn btn-sm btn-primary", "重试");
      btn.addEventListener("click", loadSettings);
      bar.appendChild(text);
      bar.appendChild(btn);
      const col = $(".st-groups-col");
      if (col) col.insertBefore(bar, col.firstChild);
    }
    const text = bar.querySelector(".st-retry-text");
    if (text) text.textContent = "设置加载失败：" + msg;
  }
  function hideRetry() {
    const bar = $("#stRetry");
    if (bar) bar.remove();
  }

  /* ---------- 服务商渲染 ---------- */
  const CAP_LABEL = { implicit: "隐式缓存", explicit: "显式", unsupported: "不支持", unknown: "未知" };
  const CAP_CLASS = { implicit: "tag-ok", explicit: "tag-scope", unsupported: "tag-cap-none", unknown: "tag-review" };

  function capTag(cap) {
    const tag = el("span", "tag " + (CAP_CLASS[cap] || "tag-cap-none"));
    tag.appendChild(el("span", "tag-dot"));
    tag.appendChild(document.createTextNode(CAP_LABEL[cap] || cap || "unknown"));
    return tag;
  }

  function guideEl(state, error, fallbackText) {
    const g = el("div", "pv-guide");
    withIcon(g, "alert");
    let text = fallbackText || "";
    if (!text) {
      if (state === "not_initialized") text = "服务商目录尚未初始化，请确认插件依赖已加载后重试";
      else if (state === "error") text = "服务商信息获取失败" + (error ? "：" + error : "");
      else text = "暂无数据";
    }
    g.appendChild(el("span", null, text));
    return g;
  }

  function renderChatProviders(data) {
    const grid = $("#pvChatGrid");
    if (!grid) return;
    grid.innerHTML = "";
    if (!data || data.state !== "ready") {
      grid.appendChild(guideEl(data && data.state, data && data.error));
      return;
    }
    const list = data.providers || [];
    if (!list.length) {
      grid.appendChild(guideEl("ready", null, "未发现已加载的对话服务商"));
      return;
    }
    const table = el("table", "tbl");
    const thead = el("thead");
    const headTr = el("tr");
    ["服务商 ID", "适配器", "模型", "缓存能力"].forEach((h) => headTr.appendChild(el("th", null, h)));
    thead.appendChild(headTr);
    table.appendChild(thead);
    const tbody = el("tbody");
    list.forEach((p) => {
      const tr = el("tr");
      tr.appendChild(el("td", "mono", p.id || "-"));
      tr.appendChild(el("td", null, p.adapter || "-"));
      const modelTd = el("td");
      modelTd.appendChild(document.createTextNode(p.model || "-"));
      if (p.model_revision) {
        modelTd.appendChild(el("div", "pv-rev", "rev " + p.model_revision));
      }
      tr.appendChild(modelTd);
      const capTd = el("td");
      capTd.appendChild(capTag(p.capability));
      tr.appendChild(capTd);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    grid.appendChild(table);
  }

  function renderMemoryProviders(data) {
    if (!data || data.state !== "ready") {
      ["pvMemChat", "pvMemEmbedding", "pvMemRerank"].forEach((id) => {
        const box = $("#" + id);
        if (!box) return;
        box.innerHTML = "";
        box.appendChild(guideEl(data && data.state, data && data.error));
      });
      return;
    }
    fillMemGroup("#pvMemChat", data.chat || []);
    fillMemGroup("#pvMemEmbedding", data.embedding || []);
    fillMemGroup("#pvMemRerank", data.rerank || []);
  }

  function fillMemGroup(sel, list) {
    const box = $(sel);
    if (!box) return;
    box.innerHTML = "";
    if (!list.length) {
      box.appendChild(guideEl("ready", null, "未配置"));
      return;
    }
    list.forEach((p) => {
      const row = el("div", "pv-row");
      const main = el("div");
      main.appendChild(el("div", "pv-name", p.id || "-"));
      main.appendChild(el("div", "pv-model", [p.adapter, p.model].filter(Boolean).join(" · ") || "-"));
      row.appendChild(main);
      box.appendChild(row);
    });
  }

  async function loadProviders() {
    try {
      const [chatData, memData] = await Promise.all([
        api.get("chat-providers"),
        api.get("memory-providers"),
      ]);
      renderChatProviders(chatData);
      renderMemoryProviders(memData);
      fillProviderSelects(memData);
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      renderChatProviders({ state: "error", providers: [], error: err.message });
      renderMemoryProviders({ state: "error", chat: [], embedding: [], rerank: [], error: err.message });
    }
  }

  /* ---------- 加载 ---------- */
  async function loadSettings() {
    try {
      const data = await api.get("settings");
      initial = data || {};
      fillAll(initial);
      hideRetry();
      loadProviders();
    } catch (e) {
      const err = api.errorOf(e);
      toast(err.message, { type: "error" });
      showRetry(err.message);
      renderApiUnavailable();
    }
  }

  /* ---------- 事件绑定 ---------- */
  /* 保存按钮（委托到 document，topbar 重建也不丢绑定） */
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn || !document.querySelector("#topbar").contains(btn)) return;
    if (btn.textContent.includes("保存")) saveAll();
  });

  /* ---------- 启动 ---------- */
  loadSettings();

} };

