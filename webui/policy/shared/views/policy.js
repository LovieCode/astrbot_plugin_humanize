/**
 * View: Policy — 群聊策略页交互（全局默认 + 按群覆盖 + 触发关键词）
 * 依赖：shared/icons.js, shared/ui.js, shared/api.js
 * 接口：GET policy / POST policy-set / POST policy-clear / POST policy-keywords
 * 安全：会话标识、群名与关键词一律通过 textContent/value 写入，禁止拼进 innerHTML。
 */
(function () {
  const MODE_LABELS = {
    silent: "完全沉默",
    no_proactive: "只回复@",
    admin: "管理员可触发",
    mention: "任何人可触发",
    full: "完全主动",
  };
  const MODE_DESCS = {
    silent: "彻底闭麦：@ 也不回复，图片不处理、消息不旁观、所有触发门全关。",
    no_proactive: "被 @ 或唤醒时正常回复；引用、关键词、闲聊窗等一切主动路径全部关闭。",
    admin: "在上一档基础上：群管理员引用机器人或命中关键词时也会主动回，普通成员触发无效。",
    mention: "在上一档基础上：任何人引用机器人或命中关键词都会主动回，但仍不开闲聊窗。",
    full: "在上一档基础上再开闲聊窗：群里的普通闲聊也会按窗口节奏触发回复。",
  };
  const MODES = Object.keys(MODE_LABELS);

  HZ.renderSidebar("policy");
  HZ.topbars["policy"] = {
    title: "群聊策略",
    sub: "全局默认 + 按群覆盖 · 保存立即生效",
    search: "",
    actions: [{ label: "刷新", icon: "refresh", variant: "ghost" }],
    onRefresh: loadPolicy,
  };
  HZ.renderTopbar(HZ.topbars["policy"]);
  HZ.initReveal();

  const $ = (sel) => document.querySelector(sel);
  const globalModesEl = $("#plGlobalModes");
  const globalDescEl = $("#plGlobalDesc");
  const globalProbEl = $("#plGlobalProb");
  const saveGlobalBtn = $("#plSaveGlobal");
  const scopeInput = $("#plNewScope");
  const scopeOptions = $("#plScopeOptions");
  const modeSelect = $("#plNewMode");
  const addBtn = $("#plAddBtn");
  const groupsEl = $("#plGroups");
  const emptyHost = $("#plEmptyHost");
  const sessionsEl = $("#plSessions");
  const sessionCountEl = $("#plSessionCount");
  const keywordsEl = $("#plKeywords");
  const keywordInput = $("#plNewKeyword");
  const keywordAddBtn = $("#plKwAddBtn");
  const sumModeEl = $("#plSumMode");
  const sumGroupsEl = $("#plSumGroups");
  const sumKeywordsEl = $("#plSumKeywords");

  let globalMode = "mention";
  let groups = [];
  let knownSessions = [];
  let keywords = [];

  function selectedMode(host) {
    const active = host.querySelector(".pl-mode.active");
    return active ? active.dataset.value : "";
  }

  /* 期望发言概率输入解析：空串 = 清除(null)；1-100 整数有效；其余报错。 */
  function parseProbInput(input) {
    const raw = (input.value || "").trim();
    if (!raw) return { ok: true, value: null };
    const value = Number(raw);
    if (!Number.isInteger(value) || value < 1 || value > 100) {
      return { ok: false };
    }
    return { ok: true, value };
  }

  function renderGlobalModes() {
    globalModesEl.querySelectorAll(".pl-mode").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.value === globalMode);
    });
    globalDescEl.textContent = MODE_DESCS[globalMode] || "";
  }

  function renderSummary() {
    if (sumModeEl) sumModeEl.textContent = MODE_LABELS[globalMode] || "—";
    if (sumGroupsEl) sumGroupsEl.textContent = String(groups.length);
    if (sumKeywordsEl) sumKeywordsEl.textContent = String(keywords.length);
  }

  function renderSessions() {
    sessionsEl.textContent = "";
    if (sessionCountEl) {
      sessionCountEl.textContent = knownSessions.length
        ? "共 " + knownSessions.length + " 个"
        : "暂无";
    }
    if (!knownSessions.length) {
      const empty = document.createElement("div");
      empty.className = "pl-note";
      empty.textContent = "还没有见到任何群；机器人收到群消息后会自动记录群名。";
      sessionsEl.appendChild(empty);
      return;
    }
    knownSessions.forEach((session) => {
      const row = document.createElement("div");
      row.className = "pl-session";
      const name = document.createElement("span");
      name.className = "pl-session-name";
      name.textContent = session.display_name || "（未命名群）";
      const id = document.createElement("span");
      id.className = "pl-session-id";
      id.textContent = session.scope_id;
      row.appendChild(name);
      row.appendChild(id);
      sessionsEl.appendChild(row);
    });
  }

  function renderDatalist() {
    scopeOptions.textContent = "";
    knownSessions.forEach((session) => {
      const option = document.createElement("option");
      option.value = session.scope_id;
      option.label = session.display_name || "";
      scopeOptions.appendChild(option);
    });
  }

  function buildModeSelect(current) {
    const select = document.createElement("select");
    select.className = "pl-mode-select";
    MODES.forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = MODE_LABELS[mode];
      if (mode === current) option.selected = true;
      select.appendChild(option);
    });
    return select;
  }

  function renderGroups() {
    groupsEl.textContent = "";
    emptyHost.textContent = "";
    if (!groups.length) {
      emptyHost.appendChild(
        HZ.initEmpty({
          text: "还没有按群覆盖的设置，所有群都套用全局默认。",
          icon: "users",
        }),
      );
      return;
    }
    groups.forEach((group) => {
      const row = document.createElement("div");
      row.className = "pl-group";
      row.dataset.scope = group.scope_id;
      row.dataset.mode = group.mode;

      const dot = document.createElement("span");
      dot.className = "pl-group-dot";
      dot.title = MODE_LABELS[group.mode] || group.mode;

      const info = document.createElement("div");
      info.className = "pl-group-info";
      const name = document.createElement("div");
      name.className = "pl-group-name";
      name.textContent = group.display_name || "（未知会话）";
      const id = document.createElement("div");
      id.className = "pl-group-id";
      id.textContent = group.scope_id;
      info.appendChild(name);
      info.appendChild(id);

      const controls = document.createElement("div");
      controls.className = "pl-group-controls";

      const select = buildModeSelect(group.mode);
      select.addEventListener("change", () => {
        // 颜色小圆点跟着当前档位走，便于一眼扫出哪些群放得开
        row.dataset.mode = select.value;
        dot.title = MODE_LABELS[select.value] || select.value;
        updateProbDisabled();
        saveOverride(group.scope_id, select.value);
      });

      const probField = document.createElement("div");
      probField.className = "pl-prob-field";
      const probLabel = document.createElement("span");
      probLabel.className = "pl-prob-tag";
      probLabel.textContent = "期望发言";
      const probInput = document.createElement("input");
      probInput.className = "pl-input pl-prob-input";
      probInput.type = "number";
      probInput.min = "1";
      probInput.max = "100";
      probInput.step = "1";
      probInput.placeholder = "如 30";
      probInput.title = "期望发言概率（1-100，软性期望；留空回退全局默认）";
      probInput.setAttribute("aria-label", "期望发言概率");
      if (group.speak_probability !== null && group.speak_probability !== undefined) {
        probInput.value = String(group.speak_probability);
      }
      probInput.addEventListener("change", () => {
        saveProbability(group.scope_id, select.value, probInput);
      });
      const probUnit = document.createElement("span");
      probUnit.className = "pl-prob-unit";
      probUnit.textContent = "%";
      probField.appendChild(probLabel);
      probField.appendChild(probInput);
      probField.appendChild(probUnit);
      function updateProbDisabled() {
        const disabled = select.value === "silent";
        probField.classList.toggle("is-muted", disabled);
        probInput.disabled = disabled;
        probInput.title = disabled
          ? "完全沉默模式下发言概率不适用"
          : "期望发言概率（1-100，软性期望；留空回退全局默认）";
      }
      updateProbDisabled();

      const removeBtn = document.createElement("button");
      removeBtn.className = "btn btn-ghost pl-danger";
      removeBtn.type = "button";
      removeBtn.textContent = "移除";
      removeBtn.addEventListener("click", () => removeOverride(group.scope_id));

      controls.appendChild(select);
      controls.appendChild(probField);
      controls.appendChild(removeBtn);

      row.appendChild(dot);
      row.appendChild(info);
      row.appendChild(controls);
      groupsEl.appendChild(row);
    });
  }

  function renderKeywords() {
    keywordsEl.textContent = "";
    if (!keywords.length) {
      const empty = document.createElement("div");
      empty.className = "pl-kw-empty";
      empty.textContent = "还没有关键词；机器人只会在被 @、被引用或闲聊窗到期时开口。";
      keywordsEl.appendChild(empty);
      return;
    }
    keywords.forEach((keyword, index) => {
      const tag = document.createElement("span");
      tag.className = "pl-kw";
      const text = document.createElement("span");
      text.className = "pl-kw-text";
      text.textContent = keyword;
      text.title = keyword;
      const remove = document.createElement("button");
      remove.className = "pl-kw-remove";
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "删除关键词 " + keyword);
      remove.addEventListener("click", () => {
        keywords.splice(index, 1);
        saveKeywords();
      });
      tag.appendChild(text);
      tag.appendChild(remove);
      keywordsEl.appendChild(tag);
    });
  }

  function renderAll() {
    renderGlobalModes();
    renderSummary();
    renderGroups();
    renderSessions();
    renderDatalist();
    renderKeywords();
  }

  async function loadPolicy() {
    try {
      const data = await HZ.api.get("policy");
      globalMode = MODES.includes(data && data.global_mode)
        ? data.global_mode
        : "mention";
      groups = Array.isArray(data && data.groups) ? data.groups : [];
      knownSessions =
        Array.isArray(data && data.known_sessions) ? data.known_sessions : [];
      keywords = Array.isArray(data && data.proactive_keywords)
        ? data.proactive_keywords.map((k) => String(k)).filter(Boolean)
        : [];
      renderAll();
      if (globalProbEl) {
        const probability = data && data.global_speak_probability;
        globalProbEl.value =
          probability === null || probability === undefined
            ? ""
            : String(probability);
      }
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("读取策略失败：" + err.message, { type: "error" });
    }
  }

  async function saveGlobal() {
    if (globalProbEl) {
      const parsed = parseProbInput(globalProbEl);
      if (!parsed.ok) {
        HZ.toast("期望发言概率需要是 1-100 的整数，或留空", { type: "error" });
        return;
      }
      try {
        await HZ.api.post("policy-set", {
          scope_id: "global",
          mode: globalMode,
          speak_probability: parsed.value,
        });
        HZ.toast("全局默认已保存：" + MODE_LABELS[globalMode], { type: "success" });
        await loadPolicy();
      } catch (e) {
        const err = HZ.api.errorOf(e);
        HZ.toast("保存失败：" + err.message, { type: "error" });
      }
      return;
    }
    try {
      await HZ.api.post("policy-set", { scope_id: "global", mode: globalMode });
      HZ.toast("全局默认已保存：" + MODE_LABELS[globalMode], { type: "success" });
      await loadPolicy();
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("保存失败：" + err.message, { type: "error" });
    }
  }

  async function saveProbability(scope, mode, input) {
    const parsed = parseProbInput(input);
    if (!parsed.ok) {
      HZ.toast("期望发言概率需要是 1-100 的整数，或留空", { type: "error" });
      await loadPolicy();
      return;
    }
    try {
      await HZ.api.post("policy-set", {
        scope_id: scope,
        mode,
        speak_probability: parsed.value,
      });
      HZ.toast(
        "期望发言概率已保存：" + scope + " → " + (parsed.value === null ? "未设置" : parsed.value + "%"),
        { type: "success" },
      );
      await loadPolicy();
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("保存失败：" + err.message, { type: "error" });
      await loadPolicy();
    }
  }

  async function addOverride() {
    const scope = (scopeInput.value || "").trim();
    if (!scope) {
      HZ.toast("请填写群号或会话标识", { type: "error" });
      return;
    }
    try {
      await HZ.api.post("policy-set", {
        scope_id: scope,
        mode: modeSelect.value,
      });
      HZ.toast("已设置：" + MODE_LABELS[modeSelect.value], { type: "success" });
      scopeInput.value = "";
      await loadPolicy();
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("设置失败：" + err.message, { type: "error" });
    }
  }

  async function saveOverride(scope, mode) {
    try {
      await HZ.api.post("policy-set", { scope_id: scope, mode });
      HZ.toast("已更新：" + scope + " → " + MODE_LABELS[mode], {
        type: "success",
      });
      await loadPolicy();
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("更新失败：" + err.message, { type: "error" });
      await loadPolicy();
    }
  }

  async function saveKeywords() {
    renderKeywords();
    try {
      await HZ.api.post("policy-keywords", { proactive_keywords: keywords });
      HZ.toast("关键词已保存", { type: "success" });
    } catch (e) {
      const err = HZ.api.errorOf(e);
      HZ.toast("关键词保存失败：" + err.message, { type: "error" });
      await loadPolicy();
    }
  }

  function addKeyword() {
    const raw = (keywordInput.value || "").replace(/[,，]/g, " ");
    const parts = raw
      .split(/\s+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!parts.length) return;
    const next = keywords.slice();
    parts.forEach((part) => {
      if (!next.includes(part)) next.push(part);
    });
    if (next.length === keywords.length) {
      HZ.toast("关键词已存在", { type: "info" });
      return;
    }
    keywords = next;
    keywordInput.value = "";
    saveKeywords();
  }

  function removeOverride(scope) {
    HZ.confirm({
      title: "移除群聊覆盖",
      text: "移除后该群回退到全局默认模式：" + MODE_LABELS[globalMode],
      danger: true,
      onConfirm: async () => {
        try {
          await HZ.api.post("policy-clear", { scope_id: scope });
          HZ.toast("已移除覆盖：" + scope, { type: "success" });
          await loadPolicy();
        } catch (e) {
          const err = HZ.api.errorOf(e);
          HZ.toast("移除失败：" + err.message, { type: "error" });
        }
      },
    });
  }

  globalModesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".pl-mode");
    if (!btn || !MODES.includes(btn.dataset.value)) return;
    globalMode = btn.dataset.value;
    renderGlobalModes();
  });
  if (saveGlobalBtn) saveGlobalBtn.addEventListener("click", saveGlobal);
  if (addBtn) addBtn.addEventListener("click", addOverride);
  if (scopeInput) {
    scopeInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") addOverride();
    });
  }
  if (keywordAddBtn) keywordAddBtn.addEventListener("click", addKeyword);
  if (keywordInput) {
    keywordInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") addKeyword();
    });
  }
  /* 顶栏「刷新」（委托到 document，topbar 重建也不丢绑定） */
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("button") : null;
    if (!btn || !document.querySelector("#topbar").contains(btn)) return;
    if (btn.textContent.includes("刷新")) loadPolicy();
  });

  loadPolicy();
})();
