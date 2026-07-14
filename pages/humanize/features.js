(function initializeHumanizeFeatures(global, document) {
  "use strict";

  const api = global.HumanizeApi;
  const VIEW_META = {
    persona: {
      title: "人格设定",
      description: "维护稳定身份、性格特征与表达边界。",
      icon: "user-round",
      saveLabel: "保存人格",
    },
    state: {
      title: "动态状态",
      description: "调整当前情绪、精力和关注焦点，状态会随对话继续变化。",
      icon: "activity",
      saveLabel: "更新状态",
    },
    behavior: {
      title: "行为决策",
      description: "配置回复、追问、主动发言与话题收束策略。",
      icon: "git-branch",
      saveLabel: "保存决策",
    },
    expression: {
      title: "Expression",
      description: "配置表达画像与集成模式，不改变普通文本输出格式。",
      icon: "message-circle-more",
      saveLabel: "保存表达",
    },
    control: {
      title: "运行控制",
      description: "查看近期变更，并按模块恢复默认状态。",
      icon: "sliders-horizontal",
    },
  };

  const DEFAULTS = {
    persona: { name: "", identity: "", traits: [], values: [], boundaries: [] },
    state: { mood: 0.5, energy: 0.5, interest: 0.5, stress: 0, focus: "", updated_at: "" },
    behavior: {
      enabled: true,
      allow_no_reply: true,
      allow_follow_up: true,
      allow_proactive: false,
      allow_end_topic: true,
      reply_threshold: 0.35,
      follow_up_threshold: 0.65,
      proactive_threshold: 0.85,
      end_topic_threshold: 0.8,
      cooldown_minutes: 30,
    },
    expression: {
      enabled: false,
      provider: "builtin",
      mode: "off",
      profile: "",
      integration_status: "unknown",
      last_checked_at: "",
      last_error: "",
    },
  };

  const state = {
    active: "persona",
    data: null,
    audit: [],
    loaded: false,
    loading: false,
    pending: false,
    dirty: false,
    savedAt: "",
    error: "",
  };

  let root = null;
  let notify = function noop() {};

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function numberValue(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function clampUnit(value, fallback) {
    return Math.max(0, Math.min(1, numberValue(value, fallback)));
  }

  function stringList(value) {
    if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
    return String(value || "").split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
  }

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function normalizeFeatures(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const normalized = {};
    Object.keys(DEFAULTS).forEach((section) => {
      normalized[section] = source[section] && typeof source[section] === "object"
        ? { ...copy(DEFAULTS[section]), ...copy(source[section]) }
        : null;
    });
    state.audit = Array.isArray(source.audit) ? copy(source.audit) : [];
    return normalized;
  }

  function refreshIcons() {
    if (global.lucide && typeof global.lucide.createIcons === "function") {
      global.lucide.createIcons();
    }
  }

  function statusLabel() {
    if (state.pending) return '<span class="feature-status pending"><i data-lucide="loader-circle"></i>保存中</span>';
    if (state.dirty) return '<span class="feature-status dirty"><i data-lucide="circle-dot"></i>有未保存更改</span>';
    if (state.savedAt) return `<span class="feature-status saved"><i data-lucide="check"></i>已保存 ${escapeHtml(state.savedAt)}</span>`;
    return '<span class="feature-status"><i data-lucide="cloud-check"></i>已同步</span>';
  }

  function updateStatus() {
    const status = root && root.querySelector("[data-feature-status]");
    if (status) status.innerHTML = statusLabel();
    const saveButton = root && root.querySelector("[data-feature-save]");
    if (saveButton) saveButton.disabled = state.pending || !state.dirty;
    refreshIcons();
  }

  function shell(content) {
    const meta = VIEW_META[state.active];
    const saveButton = state.active === "control" ? "" : `
      <button class="primary-button feature-save" type="button" data-feature-save disabled>
        <i data-lucide="save"></i><span>${meta.saveLabel}</span>
      </button>`;
    root.innerHTML = `
      <header class="feature-header">
        <div class="feature-heading">
          <span class="feature-heading-icon"><i data-lucide="${meta.icon}"></i></span>
          <div><h2>${meta.title}</h2><p>${meta.description}</p></div>
        </div>
        <div class="feature-header-actions"><span data-feature-status>${statusLabel()}</span>${saveButton}</div>
      </header>
      <div class="feature-content">${content}</div>`;
  }

  function renderLoading() {
    shell(`
      <div class="feature-screen-state loading" role="status">
        <i data-lucide="loader-circle"></i>
        <strong>正在读取拟人化配置</strong>
        <span>同步人格、状态、行为与表达配置…</span>
      </div>`);
    refreshIcons();
  }

  function renderError() {
    shell(`
      <div class="feature-screen-state error" role="alert">
        <i data-lucide="circle-alert"></i>
        <strong>配置加载失败</strong>
        <span>${escapeHtml(state.error || "无法连接插件服务")}</span>
        <button class="secondary-button" type="button" data-feature-retry>
          <i data-lucide="refresh-cw"></i><span>重新加载</span>
        </button>
      </div>`);
    root.querySelector("[data-feature-retry]").addEventListener("click", load);
    refreshIcons();
  }

  function renderEmpty() {
    const meta = VIEW_META[state.active];
    shell(`
      <div class="feature-screen-state empty">
        <i data-lucide="inbox"></i>
        <strong>还没有${meta.title}配置</strong>
        <span>创建一份默认配置后即可开始调整。</span>
        <button class="secondary-button" type="button" data-feature-create>
          <i data-lucide="plus"></i><span>创建默认配置</span>
        </button>
      </div>`);
    root.querySelector("[data-feature-create]").addEventListener("click", () => {
      state.data[state.active] = copy(DEFAULTS[state.active]);
      state.dirty = true;
      render();
    });
    refreshIcons();
  }

  function textField(id, label, value, options) {
    const settings = options || {};
    const hint = settings.hint ? `<small>${escapeHtml(settings.hint)}</small>` : "";
    const input = settings.multiline
      ? `<textarea id="${id}" name="${id}" rows="${settings.rows || 3}" placeholder="${escapeHtml(settings.placeholder || "")}">${escapeHtml(value)}</textarea>`
      : `<input id="${id}" name="${id}" type="text" value="${escapeHtml(value)}" placeholder="${escapeHtml(settings.placeholder || "")}">`;
    return `<label class="feature-field"><span>${label}</span>${input}${hint}</label>`;
  }

  function toggleField(name, label, description, checked) {
    return `
      <label class="feature-toggle-row">
        <span><strong>${label}</strong><small>${description}</small></span>
        <input type="checkbox" name="${name}" ${checked ? "checked" : ""}>
        <span class="toggle-track" aria-hidden="true"><i></i></span>
      </label>`;
  }

  function rangeField(name, label, value, hint) {
    const percent = Math.round(clampUnit(value, 0.5) * 100);
    return `
      <label class="feature-range">
        <span><strong>${label}</strong><small>${hint}</small></span>
        <span class="range-control">
          <input type="range" name="${name}" min="0" max="1" step="0.01" value="${clampUnit(value, 0.5)}">
          <output>${percent}%</output>
        </span>
      </label>`;
  }

  function renderPersona(value) {
    shell(`
      <form class="feature-form" data-feature-form="persona">
        <section class="feature-section">
          <div class="feature-section-heading"><div><h3>核心身份</h3><p>保持跨对话稳定，不依赖关系记忆。</p></div><i data-lucide="badge-check"></i></div>
          <div class="feature-form-grid">
            ${textField("name", "显示名称", value.name, { placeholder: "例如：眠汐" })}
            ${textField("identity", "身份定位", value.identity, { placeholder: "一句话说明角色身份" })}
          </div>
        </section>
        <section class="feature-section">
          <div class="feature-section-heading"><div><h3>性格与边界</h3><p>每行一项，也可使用逗号分隔。</p></div><i data-lucide="shield-check"></i></div>
          <div class="feature-form-grid three-columns">
            ${textField("traits", "性格特征", stringList(value.traits).join("\n"), { multiline: true, placeholder: "冷静\n直接\n有边界感" })}
            ${textField("values", "价值偏好", stringList(value.values).join("\n"), { multiline: true, placeholder: "诚实\n可靠" })}
            ${textField("boundaries", "行为边界", stringList(value.boundaries).join("\n"), { multiline: true, placeholder: "不冒充真人" })}
          </div>
        </section>
      </form>`);
  }

  function renderState(value) {
    shell(`
      <form class="feature-form" data-feature-form="state">
        <section class="feature-section state-section">
          <div class="feature-section-heading">
            <div><h3>当前状态</h3><p>${value.updated_at ? `最近更新 ${escapeHtml(value.updated_at)}` : "尚未记录更新时间"}</p></div>
            <span class="live-pill"><i></i>动态</span>
          </div>
          <div class="state-grid">
            ${rangeField("mood", "情绪", value.mood, "低落到愉悦")}
            ${rangeField("energy", "精力", value.energy, "疲惫到充沛")}
            ${rangeField("interest", "兴趣", value.interest, "平淡到投入")}
            ${rangeField("stress", "压力", value.stress, "放松到紧张")}
          </div>
          ${textField("focus", "关注焦点", value.focus, { placeholder: "当前最在意的对话主题" })}
        </section>
      </form>`);
  }

  function renderBehavior(value) {
    shell(`
      <form class="feature-form" data-feature-form="behavior">
        <section class="feature-section">
          <div class="feature-section-heading">
            <div><h3>策略状态</h3><p>配置会写入 Humanize 数据库；运行时决策执行器尚未接入。</p></div>
            <span class="integration-pill unknown"><i data-lucide="construction"></i>运行时待接入</span>
          </div>
          <div class="feature-toggle-list single">
            ${toggleField("enabled", "启用行为决策", "由当前场景决定是否以及如何回应。", value.enabled)}
          </div>
          <div class="feature-section-heading"><div><h3>行为许可</h3><p>分别控制决策器可以选择的行为，不会互相覆盖。</p></div><i data-lucide="list-checks"></i></div>
          <div class="feature-toggle-list">
            ${toggleField("allow_no_reply", "允许不回复", "日常对话没有回应价值时可以保持安静。", value.allow_no_reply)}
            ${toggleField("allow_follow_up", "允许追问", "信息不足或话题值得继续时提出问题。", value.allow_follow_up)}
            ${toggleField("allow_proactive", "允许主动发言", "达到高置信度后可主动加入对话。", value.allow_proactive)}
            ${toggleField("allow_end_topic", "允许结束话题", "识别自然收束点，不强行延长对话。", value.allow_end_topic)}
          </div>
        </section>
        <section class="feature-section">
          <div class="feature-section-heading"><div><h3>决策阈值</h3><p>数值越高，运行时触发对应行为会越谨慎。</p></div><i data-lucide="gauge"></i></div>
          <div class="decision-grid">
            ${rangeField("reply_threshold", "回复", value.reply_threshold, "回复当前消息")}
            ${rangeField("follow_up_threshold", "追问", value.follow_up_threshold, "继续确认细节")}
            ${rangeField("proactive_threshold", "主动发言", value.proactive_threshold, "未被点名时加入")}
            ${rangeField("end_topic_threshold", "结束话题", value.end_topic_threshold, "自然停止延伸")}
          </div>
          <label class="feature-field compact-field"><span>主动发言冷却</span><span class="number-control"><input name="cooldown_minutes" type="number" min="0" max="10080" step="1" value="${Math.max(0, numberValue(value.cooldown_minutes, 30))}"><b>分钟</b></span></label>
        </section>
      </form>`);
  }

  function renderExpression(value) {
    const status = String(value.integration_status || "unknown").toLowerCase();
    const statusLabelText = status === "ready" || status === "connected" ? "连接正常" : status === "error" ? "连接异常" : "未检查";
    shell(`
      <form class="feature-form" data-feature-form="expression">
        <section class="feature-section">
          <div class="feature-section-heading">
            <div><h3>表达集成</h3><p>输出仍为普通文本，仅在生成前应用表达画像。</p></div>
            <span class="integration-pill ${escapeHtml(status)}"><i data-lucide="${status === "error" ? "circle-alert" : "plug-zap"}"></i>${statusLabelText}</span>
          </div>
          <div class="feature-toggle-list single">
            ${toggleField("enabled", "启用 Expression", "关闭后保留 Provider 与画像设置。", value.enabled)}
          </div>
          <div data-expression-dependent>
            <div class="feature-form-grid">
              ${textField("provider", "Provider", value.provider, { placeholder: "builtin" })}
              <fieldset class="feature-field expression-mode">
                <legend>运行模式</legend>
                <div class="segmented-control">
                  <label><input type="radio" name="mode" value="off" ${value.mode === "off" ? "checked" : ""}><span>关闭</span></label>
                  <label><input type="radio" name="mode" value="observe" ${value.mode === "observe" ? "checked" : ""}><span>观察</span></label>
                  <label><input type="radio" name="mode" value="inject" ${value.mode === "inject" ? "checked" : ""}><span>注入</span></label>
                </div>
              </fieldset>
            </div>
            ${textField("profile", "表达画像", value.profile, { multiline: true, rows: 6, placeholder: "描述日常语气、信息密度与特殊任务下的表达方式。", hint: "日常建议不等于硬限制；代码、长任务等场景允许完整输出。" })}
          </div>
          <dl class="integration-facts">
            <div><dt>最近检查</dt><dd>${escapeHtml(value.last_checked_at || "--")}</dd></div>
            <div><dt>诊断</dt><dd>${escapeHtml(value.last_error || "无异常")}</dd></div>
          </dl>
        </section>
      </form>`);
  }

  function renderControl() {
    const rows = state.audit.length
      ? state.audit.slice(0, 8).map((entry) => `
          <tr><td>${escapeHtml(entry.time || entry.created_at || "--")}</td><td>${escapeHtml(entry.section || "--")}</td><td>${escapeHtml(entry.action || "--")}</td><td>${escapeHtml(entry.detail || entry.reason || "--")}</td></tr>`).join("")
      : '<tr><td colspan="4" class="control-empty">暂无控制记录</td></tr>';
    shell(`
      <div class="control-layout">
        <section class="feature-section reset-section">
          <div class="feature-section-heading"><div><h3>恢复默认</h3><p>只重置选择的模块，不影响黑话词库；关系记忆尚未实现。</p></div><i data-lucide="rotate-ccw"></i></div>
          <form class="reset-form" data-reset-form>
            <label class="feature-field"><span>重置范围</span><select name="section">
              <option value="persona">人格设定</option><option value="state">动态状态</option>
              <option value="behavior">行为决策</option><option value="expression">Expression</option>
              <option value="all">全部拟人化配置</option>
            </select></label>
            ${textField("reason", "操作备注", "", { placeholder: "可选，便于后续审计" })}
            <button class="danger-button" type="submit" data-reset-submit><i data-lucide="rotate-ccw"></i><span>确认重置</span></button>
          </form>
        </section>
        <section class="feature-section audit-section">
          <div class="feature-section-heading"><div><h3>近期变更</h3><p>仅展示非关系类拟人化配置记录。</p></div><i data-lucide="history"></i></div>
          <div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>时间</th><th>模块</th><th>动作</th><th>详情</th></tr></thead><tbody>${rows}</tbody></table></div>
        </section>
      </div>`);
  }

  function updateRangeOutputs() {
    root.querySelectorAll('.feature-range input[type="range"]').forEach((input) => {
      const output = input.closest(".range-control").querySelector("output");
      output.textContent = `${Math.round(clampUnit(input.value, 0) * 100)}%`;
    });
  }

  function updateDependentFields() {
    const behaviorForm = root.querySelector('[data-feature-form="behavior"]');
    if (behaviorForm) {
      const proactiveEnabled = behaviorForm.elements.allow_proactive.checked;
      const proactiveControls = [
        behaviorForm.elements.proactive_threshold.closest(".feature-range"),
        behaviorForm.elements.cooldown_minutes.closest(".feature-field"),
      ];
      proactiveControls.forEach((control) => {
        control.classList.toggle("is-disabled", !proactiveEnabled);
      });
    }
    const expressionForm = root.querySelector('[data-feature-form="expression"]');
    if (expressionForm) {
      const enabled = expressionForm.elements.enabled.checked;
      const section = expressionForm.querySelector("[data-expression-dependent]");
      section.classList.toggle("is-disabled", !enabled);
      section.querySelectorAll("input, textarea").forEach((input) => { input.disabled = !enabled; });
    }
  }

  function bindForm() {
    const form = root.querySelector("[data-feature-form]");
    if (!form) return;
    form.addEventListener("input", (event) => {
      state.dirty = true;
      state.savedAt = "";
      if (event.target.type === "range") updateRangeOutputs();
      if (event.target.name === "enabled") updateDependentFields();
      updateStatus();
    });
    form.addEventListener("change", () => {
      state.dirty = true;
      state.savedAt = "";
      updateDependentFields();
      updateStatus();
    });
    root.querySelector("[data-feature-save]").addEventListener("click", save);
    updateDependentFields();
  }

  function collectPayload() {
    const form = root.querySelector("[data-feature-form]");
    const data = new FormData(form);
    if (state.active === "persona") {
      return {
        name: String(data.get("name") || "").trim(),
        identity: String(data.get("identity") || "").trim(),
        traits: stringList(data.get("traits")),
        values: stringList(data.get("values")),
        boundaries: stringList(data.get("boundaries")),
      };
    }
    if (state.active === "state") {
      return {
        mood: clampUnit(data.get("mood"), 0.5),
        energy: clampUnit(data.get("energy"), 0.5),
        interest: clampUnit(data.get("interest"), 0.5),
        stress: clampUnit(data.get("stress"), 0),
        focus: String(data.get("focus") || "").trim(),
      };
    }
    if (state.active === "behavior") {
      const elements = form.elements;
      return {
        enabled: elements.enabled.checked,
        allow_no_reply: elements.allow_no_reply.checked,
        allow_follow_up: elements.allow_follow_up.checked,
        allow_proactive: elements.allow_proactive.checked,
        allow_end_topic: elements.allow_end_topic.checked,
        reply_threshold: clampUnit(data.get("reply_threshold"), state.data.behavior.reply_threshold),
        follow_up_threshold: clampUnit(data.get("follow_up_threshold"), state.data.behavior.follow_up_threshold),
        proactive_threshold: clampUnit(data.get("proactive_threshold"), state.data.behavior.proactive_threshold),
        end_topic_threshold: clampUnit(data.get("end_topic_threshold"), state.data.behavior.end_topic_threshold),
        cooldown_minutes: Math.max(0, Math.min(10080, Math.round(numberValue(data.get("cooldown_minutes"), 30)))),
      };
    }
    return {
      enabled: form.elements.enabled.checked,
      provider: String(data.get("provider") || "").trim(),
      mode: String(data.get("mode") || "off"),
      profile: String(data.get("profile") || "").trim(),
    };
  }

  async function save() {
    if (state.pending || !state.dirty) return;
    const methods = {
      persona: api.savePersona,
      state: api.saveState,
      behavior: api.saveBehavior,
      expression: api.saveExpression,
    };
    const payload = collectPayload();
    state.pending = true;
    root.querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = true; });
    updateStatus();
    try {
      const result = await methods[state.active](payload);
      state.data[state.active] = { ...copy(DEFAULTS[state.active]), ...(result || {}) };
      state.dirty = false;
      state.savedAt = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      notify(`${VIEW_META[state.active].title}已保存`, "success");
      render();
    } catch (error) {
      notify(error.message || "保存失败", "error");
      root.querySelectorAll("input, textarea, select, button").forEach((control) => { control.disabled = false; });
    } finally {
      state.pending = false;
      updateDependentFields();
      updateStatus();
    }
  }

  async function reset(event) {
    event.preventDefault();
    if (state.pending) return;
    const form = event.currentTarget;
    const section = String(new FormData(form).get("section") || "all");
    const reason = String(new FormData(form).get("reason") || "").trim();
    state.pending = true;
    form.querySelectorAll("input, select, button").forEach((control) => { control.disabled = true; });
    updateStatus();
    try {
      const result = await api.resetControl({ section, reason });
      const sections = result && result.sections && typeof result.sections === "object" ? result.sections : {};
      Object.keys(DEFAULTS).forEach((key) => {
        if (sections[key]) state.data[key] = { ...copy(DEFAULTS[key]), ...copy(sections[key]) };
        else if (section === "all" || section === key) state.data[key] = copy(DEFAULTS[key]);
      });
      notify(section === "all" ? "拟人化配置已全部重置" : `${VIEW_META[section].title}已重置`, "success");
      await load();
    } catch (error) {
      notify(error.message || "重置失败", "error");
      form.querySelectorAll("input, select, button").forEach((control) => { control.disabled = false; });
    } finally {
      state.pending = false;
      updateStatus();
    }
  }

  function render() {
    if (!root || root.hidden) return;
    if (state.loading) return renderLoading();
    if (state.error) return renderError();
    if (state.active !== "control" && (!state.data || !state.data[state.active])) return renderEmpty();
    const renderers = { persona: renderPersona, state: renderState, behavior: renderBehavior, expression: renderExpression };
    if (state.active === "control") {
      renderControl();
      root.querySelector("[data-reset-form]").addEventListener("submit", reset);
    } else {
      renderers[state.active](state.data[state.active]);
      bindForm();
    }
    updateStatus();
    updateRangeOutputs();
    refreshIcons();
  }

  async function load() {
    state.loading = true;
    state.error = "";
    render();
    try {
      state.data = normalizeFeatures(await api.getFeatures());
      state.loaded = true;
      state.dirty = false;
    } catch (error) {
      state.error = error.message || "拟人化配置加载失败";
    } finally {
      state.loading = false;
      render();
    }
  }

  function open(view) {
    if (!VIEW_META[view]) return false;
    state.active = view;
    state.dirty = false;
    state.savedAt = "";
    state.error = "";
    root.hidden = false;
    if (!state.loaded) load();
    else render();
    return true;
  }

  global.HumanizeFeatures = Object.freeze({
    mount(target, options) {
      root = target;
      notify = options && typeof options.notify === "function" ? options.notify : notify;
    },
    open,
  });
})(window, document);
