(function initializeHumanizePage(global, document) {
  "use strict";

  const api = global.HumanizeApi;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const STATUS_META = {
    candidate: { label: "待确认", className: "status-candidate" },
    confirmed: { label: "已确认", className: "status-confirmed" },
    ambiguous: { label: "有歧义", className: "status-ambiguous" },
    rejected: { label: "已拒绝", className: "status-rejected" },
    disabled: { label: "已禁用", className: "status-disabled" },
  };
  const DYNAMIC_TITLES = {
    overview: "运行总览",
    context: "上下文追踪",
    memory: "长期记忆",
    examples: "回复样例",
    protocol: "协议监控",
    settings: "设置",
  };
  const state = {
    settings: {},
    overview: {},
    jargons: [],
    total: 0,
    page: 1,
    pageSize: 10,
    search: "",
    status: "",
    scopeType: "",
    scopeId: "",
    selectedId: null,
    selectedDetail: null,
    protocolLogs: [],
    promptTemplates: [],
    promptTemplatePendingKey: "",
    actionPending: false,
    listRequestId: 0,
    detailRequestId: 0,
    contextDetailRequestId: 0,
    activeView: "jargons",
  };
  const refs = {};
  let searchTimer = null;

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const node = document.createElement("i");
    node.dataset.lucide = name;
    return node;
  }

  function refreshIcons() {
    if (global.lucide && typeof global.lucide.createIcons === "function") {
      global.lucide.createIcons();
    }
  }

  function numberValue(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function booleanValue(value, fallback) {
    if (typeof value === "boolean") return value;
    if (value === 1 || value === "1" || value === "true") return true;
    if (value === 0 || value === "0" || value === "false") return false;
    return fallback;
  }

  function normalizeConfidence(value) {
    const numeric = numberValue(value, 0);
    return Math.max(0, Math.min(100, numeric <= 1 ? numeric * 100 : numeric));
  }

  function formatConfidence(value) {
    return `${Math.round(normalizeConfidence(value))}%`;
  }

  function formatRate(value) {
    if (value === null || value === undefined || value === "") return "--";
    return `${numberValue(value, 0).toFixed(1)}%`;
  }

  function normalizeStatus(value, enabled) {
    if (enabled === false) return "disabled";
    const status = String(value || "candidate").toLowerCase();
    if (status === "verified" || status === "approved") return "confirmed";
    if (status === "pending" || status === "provisional") return "candidate";
    return STATUS_META[status] ? status : "candidate";
  }

  function formatScopeLabel(scopeType, scopeId) {
    const id = String(scopeId || "");
    if (!id) return "未知作用域";
    const type = String(scopeType || "").toLowerCase();
    const prefix = type === "group" ? "群聊" : type === "private" ? "私聊" : "作用域";
    return `${prefix} · ${id}`;
  }

  function makeScopeKey(scopeType, scopeId) {
    const type = encodeURIComponent(String(scopeType || ""));
    const id = encodeURIComponent(String(scopeId || ""));
    return type && id ? `${type}/${id}` : "";
  }

  function parseScopeKey(value) {
    const key = String(value || "");
    const separator = key.indexOf("/");
    if (separator < 0) return { type: "", id: "" };
    try {
      return { type: decodeURIComponent(key.slice(0, separator)), id: decodeURIComponent(key.slice(separator + 1)) };
    } catch {
      return { type: "", id: "" };
    }
  }

  function normalizeSense(value, index) {
    const source = value && typeof value === "object" ? value : {};
    return {
      id: source.id ?? source.sense_id ?? null,
      meaning: String(source.meaning || source.guess || source.inferred_meaning || ""),
      status: normalizeStatus(source.status || source.state, true),
      rawStatus: String(source.status || source.state || "candidate"),
      confidence: normalizeConfidence(source.confidence),
      preferred: booleanValue(source.is_preferred ?? source.preferred, false),
      evidenceCount: numberValue(source.evidence_count, 0),
      reason: String(source.reason || source.sense_reason || ""),
      version: numberValue(source.version, 0),
      legacy: source.id === undefined && source.sense_id === undefined,
      index,
    };
  }

  function normalizeJargon(payload) {
    const envelope = payload && typeof payload === "object" ? payload : {};
    const source = envelope.entry && typeof envelope.entry === "object" ? envelope.entry : envelope;
    const enabled = booleanValue(source.enabled, source.status !== "rejected");
    const senses = Array.isArray(envelope.senses)
      ? envelope.senses.map(normalizeSense)
      : Array.isArray(source.senses) ? source.senses.map(normalizeSense) : [];
    const preferred = source.preferred_sense && typeof source.preferred_sense === "object"
      ? normalizeSense(source.preferred_sense, -1)
      : senses.find((item) => item.preferred) || senses.find((item) => item.status === "confirmed") || senses[0];
    const directMeaning = String(source.meaning || source.guess || source.inferred_meaning || "");
    const aliasSource = Array.isArray(envelope.aliases) ? envelope.aliases : Array.isArray(source.aliases) ? source.aliases : [];
    const aliases = aliasSource.map((item) => ({
      id: item && typeof item === "object" ? item.id : null,
      alias: String(item && typeof item === "object" ? item.alias || item.term || "" : item),
    })).filter((item) => item.alias);
    return {
      id: source.id ?? source.entry_id,
      term: String(source.term || source.content || source.word || ""),
      status: normalizeStatus(source.status || source.state, enabled),
      rawStatus: String(source.status || source.state || "candidate"),
      enabled,
      hasConflict: booleanValue(source.has_conflict, source.status === "ambiguous"),
      matchMode: String(source.match_mode || "smart"),
      caseSensitive: booleanValue(source.case_sensitive, false),
      meaning: preferred && preferred.meaning ? preferred.meaning : directMeaning || "含义待确认",
      preferredSense: preferred || null,
      senses,
      senseCount: numberValue(source.sense_count, senses.length || (directMeaning ? 1 : 0)),
      pendingSenseCount: numberValue(source.pending_sense_count, senses.filter((item) => item.status === "candidate").length),
      aliases,
      aliasCount: numberValue(source.alias_count, aliases.length),
      scopeType: String(source.scope_type || ""),
      scopeId: String(source.scope_id || source.chat_id || ""),
      scopeLabel: String(source.scope_label || source.scope_name || formatScopeLabel(source.scope_type, source.scope_id || source.chat_id)),
      confidence: normalizeConfidence(source.confidence ?? (preferred && preferred.confidence)),
      occurrences: numberValue(source.occurrence_count ?? source.count, 0),
      firstSeen: String(source.first_seen || source.first_seen_at || source.created_at || "--"),
      lastSeen: String(source.last_seen || source.last_seen_at || source.updated_at || "--"),
      evidence: Array.isArray(envelope.evidence) ? envelope.evidence : Array.isArray(source.evidence) ? source.evidence : [],
      inferences: Array.isArray(envelope.inferences) ? envelope.inferences : [],
      injections: Array.isArray(envelope.injections) ? envelope.injections : [],
    };
  }

  function normalizeDetail(payload) {
    const detail = normalizeJargon(payload);
    if (!detail.senses.length && detail.meaning && detail.meaning !== "含义待确认") {
      detail.senses = [normalizeSense({
        meaning: detail.meaning,
        status: detail.rawStatus,
        confidence: detail.confidence / 100,
        is_preferred: true,
      }, 0)];
      detail.senseCount = 1;
      detail.preferredSense = detail.senses[0];
    }
    return detail;
  }

  function normalizeCollection(payload) {
    if (Array.isArray(payload)) return { items: payload, total: payload.length };
    const source = payload && typeof payload === "object" ? payload : {};
    const items = Array.isArray(source.items) ? source.items : Array.isArray(source.runs) ? source.runs : [];
    return { items, total: numberValue(source.total, items.length) };
  }

  function normalizePromptTemplates(payload) {
    const source = payload && typeof payload === "object" ? payload : {};
    const collection = Array.isArray(payload)
      ? payload
      : Array.isArray(source.items)
        ? source.items
        : Array.isArray(source.templates)
          ? source.templates
          : source.templates && typeof source.templates === "object"
            ? Object.entries(source.templates).map(([key, value]) => value && typeof value === "object" ? { key, ...value } : { key, content: value })
            : source.prompt_templates && typeof source.prompt_templates === "object"
              ? Object.entries(source.prompt_templates).map(([key, value]) => value && typeof value === "object" ? { key, ...value } : { key, content: value })
              : source.item && typeof source.item === "object"
                ? [source.item]
                : source.template && typeof source.template === "object"
                  ? [source.template]
                  : source.key
                    ? [source]
                    : [];
    return collection.map((item, index) => {
      const template = item && typeof item === "object" ? item : { content: item };
      const key = String(template.key || template.id || template.name || `template-${index + 1}`);
      const content = template.content ?? template.template ?? template.prompt ?? template.text ?? "";
      return {
        key,
        name: String(template.name || template.title || template.label || key),
        description: String(template.description || template.hint || ""),
        content: typeof content === "string" ? content : serializeTraceContent(content),
        variables: Array.isArray(template.variables) ? template.variables.map(String) : [],
        editable: booleanValue(template.editable, true),
        source: String(template.source || template.origin || ""),
        updatedAt: String(template.updated_at || template.updatedAt || ""),
      };
    });
  }

  function showToast(message, type) {
    const toast = element("div", `toast ${type || "info"}`);
    toast.append(icon(type === "success" ? "check-circle-2" : type === "error" ? "circle-alert" : "info"));
    toast.append(element("span", "", message));
    refs.toastRegion.append(toast);
    refreshIcons();
    global.setTimeout(() => toast.remove(), 3200);
  }

  function setStateNode(node, message) {
    node.hidden = !message;
    node.textContent = message || "";
  }

  function createStatusBadge(status) {
    const meta = STATUS_META[normalizeStatus(status, status !== "disabled")];
    return element("span", `status-badge ${meta.className}`, meta.label);
  }

  function renderMetrics() {
    const overview = state.overview || {};
    refs.metricLearned.textContent = String(overview.learned_count ?? overview.learned ?? "--");
    refs.metricPending.textContent = String(overview.pending_count ?? overview.pending ?? "--");
    refs.metricProtocol.textContent = formatRate(overview.protocol_success_rate ?? overview.success_rate);
    refs.metricBlocked.textContent = String(overview.blocked_week ?? overview.weekly_blocked ?? "--");
    refs.chartSuccessRate.textContent = formatRate(overview.protocol_success_rate ?? overview.success_rate);
    renderProtocolChart(Array.isArray(overview.protocol_trend) ? overview.protocol_trend : []);
  }

  function renderProtocolChart(trend) {
    const points = trend.slice(-7).map((item) => ({
      label: String(item.label || item.date || "--"),
      value: item.value === null || item.value === undefined ? null : Math.max(0, Math.min(100, numberValue(item.value ?? item.rate, 0))),
    }));
    refs.protocolChart.replaceChildren();
    refs.chartLabels.replaceChildren();
    points.forEach((point) => refs.chartLabels.append(element("span", "", point.label)));
    const sampled = points.map((point, index) => ({ ...point, index })).filter((point) => point.value !== null);
    if (!sampled.length) {
      const empty = document.createElementNS(SVG_NS, "text");
      empty.setAttribute("x", "180");
      empty.setAttribute("y", "54");
      empty.setAttribute("text-anchor", "middle");
      empty.setAttribute("fill", "#9d9499");
      empty.setAttribute("font-size", "12");
      empty.textContent = "暂无协议样本";
      refs.protocolChart.append(empty);
      return;
    }
    const polyline = document.createElementNS(SVG_NS, "polyline");
    polyline.setAttribute("points", sampled.map((point) => {
      const x = (point.index / Math.max(1, points.length - 1)) * 352 + 4;
      const y = 94 - (point.value / 100) * 84;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" "));
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", "#709b72");
    polyline.setAttribute("stroke-width", "1.5");
    if (sampled.length > 1) refs.protocolChart.append(polyline);
    sampled.forEach((point) => {
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", String((point.index / Math.max(1, points.length - 1)) * 352 + 4));
      circle.setAttribute("cy", String(94 - (point.value / 100) * 84));
      circle.setAttribute("r", "2.6");
      circle.setAttribute("fill", "#709b72");
      refs.protocolChart.append(circle);
    });
  }

  function renderScopes() {
    const configured = Array.isArray(state.settings.scopes) ? state.settings.scopes : Array.isArray(state.overview.scopes) ? state.overview.scopes : [];
    const scopes = configured.map((scope) => ({
      type: String(scope.type || scope.scope_type || ""),
      id: String(scope.id || scope.scope_id || ""),
      label: String(scope.label || scope.name || formatScopeLabel(scope.type || scope.scope_type, scope.id || scope.scope_id)),
    })).filter((scope) => scope.type && scope.id);
    refs.scopePicker.replaceChildren(element("option", "", "全部作用域"));
    refs.scopePicker.firstChild.value = "";
    refs.scopeFilter.replaceChildren(element("option", "", "全部作用域"));
    refs.scopeFilter.firstChild.value = "";
    scopes.forEach((scope) => {
      const value = makeScopeKey(scope.type, scope.id);
      const top = element("option", "", scope.label);
      top.value = value;
      refs.scopePicker.append(top);
      const filter = element("option", "", scope.label);
      filter.value = value;
      refs.scopeFilter.append(filter);
    });
    refs.scopePicker.value = makeScopeKey(state.scopeType, state.scopeId);
    refs.scopeFilter.value = refs.scopePicker.value;
  }

  function renderSettingsSummary() {
    const settings = state.settings || {};
    const enabled = settings.default_rule_enabled !== false;
    refs.ruleEnabled.textContent = enabled ? "已启用" : "未启用";
    document.querySelector(".rule-dot").classList.toggle("disabled", !enabled);
    refs.ruleInjectionMode.textContent = settings.protocol_injection_mode === "both" ? "用户消息 + System" : "用户消息（临时）";
    refs.ruleAdministrator.textContent = String(settings.administrator_name || settings.admin_name || "管理员");
    refs.ruleMessageLimit.textContent = `${numberValue(settings.max_message_chars, 10)} 字以内`;
    refs.learningToggle.checked = (settings.learning_enabled ?? settings.jargon_enabled) !== false;
  }

  function renderJargonRows() {
    refs.jargonRows.replaceChildren();
    state.jargons.forEach((item) => {
      const row = element("tr", "jargon-row");
      row.dataset.id = String(item.id);
      row.tabIndex = 0;
      row.classList.toggle("selected", String(item.id) === String(state.selectedId));
      const select = () => selectJargon(item.id);
      row.addEventListener("click", select);
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          select();
        }
      });
      const statusCell = element("td");
      statusCell.append(createStatusBadge(item.status));
      if (item.hasConflict) statusCell.append(element("span", "row-conflict-dot", "冲突"));
      const termCell = element("td");
      termCell.append(element("strong", "", item.term));
      if (item.aliasCount) termCell.append(element("small", "row-secondary", `${item.aliasCount} 个别名`));
      const meaning = item.senseCount > 1 ? `${item.meaning}  +${item.senseCount - 1}` : item.meaning;
      [statusCell, termCell, element("td", "", meaning), element("td", "", item.scopeLabel), element("td", "", formatConfidence(item.confidence)), element("td", "", item.occurrences), element("td", "", item.lastSeen)].forEach((cell) => row.append(cell));
      const actionCell = element("td");
      const button = element("button", "row-action");
      button.type = "button";
      button.title = "查看词条详情";
      button.append(icon("chevron-right"));
      button.addEventListener("click", (event) => { event.stopPropagation(); select(); });
      actionCell.append(button);
      row.append(actionCell);
      refs.jargonRows.append(row);
    });
    refreshIcons();
  }

  function renderPagination() {
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    state.page = Math.min(state.page, totalPages);
    refs.paginationSummary.textContent = `共 ${state.total} 条`;
    refs.pagination.replaceChildren();
    const add = (label, page, disabled, active) => {
      const button = element("button", `page-button${active ? " active" : ""}`, label);
      button.type = "button";
      button.disabled = disabled;
      button.addEventListener("click", () => { state.page = page; loadJargons(); });
      refs.pagination.append(button);
    };
    add("‹", Math.max(1, state.page - 1), state.page <= 1, false);
    Array.from(new Set([1, state.page - 1, state.page, state.page + 1, totalPages])).filter((page) => page >= 1 && page <= totalPages).sort((a, b) => a - b).forEach((page) => add(String(page), page, false, page === state.page));
    add("›", Math.min(totalPages, state.page + 1), state.page >= totalPages, false);
  }

  async function loadJargons(options) {
    const requestId = ++state.listRequestId;
    setStateNode(refs.tableState, "正在读取真实词条…");
    try {
      const payload = await api.getJargons({ search: state.search, status: state.status, scope_type: state.scopeType, scope_id: state.scopeId, page: state.page, page_size: state.pageSize });
      if (requestId !== state.listRequestId) return;
      const collection = normalizeCollection(payload);
      state.jargons = collection.items.map(normalizeJargon);
      state.total = collection.total;
      renderJargonRows();
      renderPagination();
      setStateNode(refs.tableState, state.jargons.length ? "" : "没有符合条件的词条");
      const selectedVisible = state.jargons.some((item) => String(item.id) === String(state.selectedId));
      if (!selectedVisible && state.jargons.length) selectJargon(state.jargons[0].id);
      else if (!state.jargons.length) { state.selectedId = null; state.selectedDetail = null; renderDetail(); }
      else if (options && options.refreshDetail && state.selectedId) await loadJargonDetail(state.selectedId);
    } catch (error) {
      if (requestId !== state.listRequestId) return;
      state.jargons = [];
      state.total = 0;
      renderJargonRows();
      renderPagination();
      setStateNode(refs.tableState, error.message || "词条加载失败");
      showToast(error.message || "词条加载失败", "error");
    }
  }

  function selectJargon(id) {
    state.selectedId = String(id);
    renderJargonRows();
    loadJargonDetail(id);
  }

  function senseLabel(sense, index) {
    const text = sense.meaning || `含义 ${index + 1}`;
    return text.length > 24 ? `${text.slice(0, 24)}…` : text;
  }

  function renderSenseOptions(detail) {
    refs.evidenceSenseFilter.replaceChildren(element("option", "", "全部含义"));
    refs.evidenceSenseFilter.firstChild.value = "";
    refs.mergeSourceSelect.replaceChildren();
    refs.mergeTargetSelect.replaceChildren();
    detail.senses.forEach((sense, index) => {
      const value = sense.id === null ? `legacy-${index}` : String(sense.id);
      [refs.evidenceSenseFilter, refs.mergeSourceSelect, refs.mergeTargetSelect].forEach((select) => {
        const option = element("option", "", senseLabel(sense, index));
        option.value = value;
        select.append(option);
      });
    });
    refs.mergeSenses.hidden = detail.senses.length < 2 || detail.senses.some((sense) => sense.id === null);
    if (detail.senses.length > 1) refs.mergeTargetSelect.selectedIndex = 1;
  }

  function senseActionButton(label, iconName, action, sense, className) {
    const button = element("button", className || "text-button");
    button.type = "button";
    button.append(icon(iconName), element("span", "", label));
    button.disabled = state.actionPending;
    button.addEventListener("click", () => performSenseAction(action, sense));
    return button;
  }

  function renderSenses(detail) {
    refs.senseList.replaceChildren();
    refs.senseSummary.textContent = `${detail.senses.length} 个含义${detail.hasConflict ? " · 存在待审核冲突" : ""}`;
    if (!detail.senses.length) {
      refs.senseList.append(element("div", "sense-empty", "尚无释义，可手动新增。"));
      renderSenseOptions(detail);
      return;
    }
    detail.senses.forEach((sense, index) => {
      const card = element("article", `sense-card${sense.preferred ? " preferred" : ""}`);
      card.dataset.senseId = sense.id === null ? "" : String(sense.id);
      const header = element("div", "sense-card-header");
      const badges = element("div", "sense-badges");
      badges.append(createStatusBadge(sense.status));
      if (sense.preferred) badges.append(element("span", "preferred-badge", "首选"));
      header.append(badges, element("span", "sense-confidence", formatConfidence(sense.confidence)));
      const textarea = element("textarea", "sense-meaning-input");
      textarea.rows = 3;
      textarea.maxLength = 1000;
      textarea.value = sense.meaning;
      textarea.setAttribute("aria-label", `编辑含义 ${index + 1}`);
      const meta = element("div", "sense-meta", `${sense.evidenceCount} 条证据${sense.reason ? ` · ${sense.reason}` : ""}`);
      const actions = element("div", "sense-actions");
      const save = element("button", "text-button");
      save.type = "button";
      save.append(icon("save"), element("span", "", "保存"));
      save.addEventListener("click", () => performSenseAction("update_sense", sense, textarea.value.trim()));
      actions.append(save);
      if (sense.status !== "confirmed") actions.append(senseActionButton("确认", "check", "confirm_sense", sense));
      if (!sense.preferred && sense.id !== null) actions.append(senseActionButton("设为首选", "star", "set_preferred", sense));
      if (sense.status !== "rejected") actions.append(senseActionButton("拒绝", "ban", "reject_sense", sense, "text-button danger-text"));
      const evidenceButton = element("button", "text-button");
      evidenceButton.type = "button";
      evidenceButton.append(icon("quote"), element("span", "", "看证据"));
      evidenceButton.addEventListener("click", () => {
        refs.evidenceSenseFilter.value = sense.id === null ? `legacy-${index}` : String(sense.id);
        renderEvidence(detail);
      });
      actions.append(evidenceButton);
      card.append(header, textarea, meta, actions);
      refs.senseList.append(card);
    });
    renderSenseOptions(detail);
    refreshIcons();
  }

  function renderEvidence(detail) {
    refs.evidenceList.replaceChildren();
    const selected = refs.evidenceSenseFilter.value;
    const selectedSense = selected.startsWith("legacy-") ? "" : selected;
    const evidence = detail.evidence.filter((item) => !selected || String(item.sense_id ?? "") === selectedSense);
    if (!evidence.length) {
      const empty = element("li", "evidence-item");
      empty.append(element("p", "evidence-text", selected ? "该含义暂无证据" : "暂无可展示的真实证据"));
      refs.evidenceList.append(empty);
      return;
    }
    evidence.forEach((entry) => {
      const item = element("li", `evidence-item${booleanValue(entry.valid, true) ? "" : " invalid"}`);
      const meta = element("div", "evidence-meta");
      meta.append(element("span", "", entry.observed_at || entry.created_at || entry.time || "--"), element("span", "", `用户 · ${entry.sender_name || entry.sender_id || entry.sender || "未知"}`));
      if (entry.sense_id !== null && entry.sense_id !== undefined) meta.append(element("span", "evidence-sense", `含义 #${entry.sense_id}`));
      item.append(meta, element("p", "evidence-text", entry.source_text || entry.text || entry.context || entry.excerpt || ""));
      refs.evidenceList.append(item);
    });
  }

  function renderDetail() {
    const detail = state.selectedDetail;
    refs.detailEmpty.hidden = Boolean(detail);
    refs.detailContent.hidden = !detail;
    if (!detail) return;
    refs.detailTerm.textContent = detail.term || "--";
    const statusMeta = STATUS_META[detail.status];
    refs.detailStatus.textContent = statusMeta.label;
    refs.detailStatus.className = `status-badge ${statusMeta.className}`;
    refs.detailConflict.hidden = !detail.hasConflict;
    refs.matchModeSelect.value = ["smart", "contains", "exact"].includes(detail.matchMode) ? detail.matchMode : "smart";
    refs.caseSensitiveToggle.checked = detail.caseSensitive;
    refs.entryEnabledToggle.checked = detail.enabled;
    refs.aliasesInput.value = detail.aliases.map((item) => item.alias).join("\n");
    refs.detailEnabledLabel.textContent = detail.enabled ? "允许匹配与注入" : "已停止注入";
    refs.detailConfidence.textContent = formatConfidence(detail.confidence);
    refs.confidenceFill.style.width = `${normalizeConfidence(detail.confidence)}%`;
    refs.detailScope.textContent = detail.scopeLabel;
    refs.detailOccurrences.textContent = String(detail.occurrences);
    refs.detailFirstSeen.textContent = detail.firstSeen;
    refs.detailLastSeen.textContent = detail.lastSeen;
    refs.saveEntryButton.disabled = state.actionPending;
    refs.deleteEntryButton.disabled = state.actionPending;
    renderSenses(detail);
    renderEvidence(detail);
  }

  async function loadJargonDetail(id) {
    const requestId = ++state.detailRequestId;
    refs.detailEmpty.hidden = false;
    refs.detailEmpty.querySelector("span").textContent = "正在读取真实详情…";
    refs.detailContent.hidden = true;
    try {
      const payload = await api.getJargonDetail(id);
      if (requestId !== state.detailRequestId || String(id) !== String(state.selectedId)) return;
      state.selectedDetail = normalizeDetail(payload);
      renderDetail();
    } catch (error) {
      if (requestId !== state.detailRequestId) return;
      state.selectedDetail = null;
      renderDetail();
      refs.detailEmpty.querySelector("span").textContent = error.message || "详情加载失败";
      showToast(error.message || "详情加载失败", "error");
    }
  }

  async function performJargonAction(action, extra) {
    if (!state.selectedDetail || state.actionPending) return;
    state.actionPending = true;
    renderDetail();
    try {
      const payload = { id: state.selectedDetail.id, action, ...(extra || {}) };
      await api.jargonAction(payload);
      showToast("词条已更新", "success");
      await loadJargons({ refreshDetail: true });
    } catch (error) {
      showToast(error.message || "操作失败", "error");
    } finally {
      state.actionPending = false;
      renderDetail();
    }
  }

  function performSenseAction(action, sense, meaning) {
    if (!meaning && action === "update_sense") {
      showToast("释义不能为空", "error");
      return;
    }
    if (sense.id === null) {
      const legacyActions = { update_sense: "update_meaning", confirm_sense: "confirm", reject_sense: "reject" };
      const legacy = legacyActions[action];
      if (!legacy) { showToast("旧版词条不支持该操作，请先完成数据库迁移", "error"); return; }
      performJargonAction(legacy, meaning ? { meaning } : {});
      return;
    }
    const extra = { sense_id: sense.id };
    if (meaning) extra.meaning = meaning;
    if (action === "confirm_sense") extra.preferred = state.selectedDetail.senses.length === 1;
    performJargonAction(action, extra);
  }

  async function saveEntrySettings() {
    if (!state.selectedDetail || state.actionPending) return;
    const aliases = Array.from(new Set(refs.aliasesInput.value.split(/\r?\n|,|，/).map((item) => item.trim()).filter(Boolean)));
    const enabled = refs.entryEnabledToggle.checked;
    const matchMode = refs.matchModeSelect.value;
    const caseSensitive = refs.caseSensitiveToggle.checked;
    state.actionPending = true;
    renderDetail();
    try {
      await api.jargonAction({ id: state.selectedDetail.id, action: "update_entry", enabled, match_mode: matchMode, case_sensitive: caseSensitive });
      await api.jargonAction({ id: state.selectedDetail.id, action: "replace_aliases", aliases });
      showToast("匹配设置与别名已保存", "success");
      await loadJargons({ refreshDetail: true });
    } catch (error) {
      showToast(error.message || "词条设置保存失败", "error");
    } finally {
      state.actionPending = false;
      renderDetail();
    }
  }

  async function createSense() {
    const meaning = refs.newSenseInput.value.trim();
    if (!meaning) { showToast("请输入新的含义", "error"); return; }
    await performJargonAction("create_sense", { meaning });
    refs.newSenseInput.value = "";
    refs.newSenseForm.hidden = true;
  }

  async function exportJargons() {
    try {
      const payload = await api.exportJargons({ search: state.search, status: state.status, scope_type: state.scopeType, scope_id: state.scopeId });
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "humanize-jargons.json";
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      showToast("真实词库已导出", "success");
    } catch (error) {
      showToast(error.message || "词库导出失败", "error");
    }
  }

  function renderProtocolRows(container, logs, limit) {
    container.replaceChildren();
    const visible = typeof limit === "number" ? logs.slice(0, limit) : logs;
    visible.forEach((entry) => {
      const row = element("tr");
      const succeeded = entry.success === true || entry.success === 1 || entry.status === "success";
      const statusCell = element("td");
      statusCell.append(element("span", `protocol-status${succeeded ? "" : " blocked"}`, succeeded ? "成功" : "拦截"));
      row.append(element("td", "", entry.created_at || entry.time || "--"), element("td", "", entry.action || entry.type || "--"), statusCell, element("td", "", entry.failure_detail || entry.failure_code || entry.detail || "--"));
      container.append(row);
    });
  }

  async function loadProtocolLogs() {
    setStateNode(refs.protocolState, "正在读取协议日志…");
    try {
      const payload = await api.getProtocolLogs({ page: 1, page_size: 100 });
      state.protocolLogs = normalizeCollection(payload).items;
      renderProtocolRows(refs.protocolRows, state.protocolLogs, 4);
      setStateNode(refs.protocolState, state.protocolLogs.length ? "" : "暂无协议日志");
    } catch (error) {
      state.protocolLogs = [];
      renderProtocolRows(refs.protocolRows, [], 4);
      setStateNode(refs.protocolState, error.message || "协议日志加载失败");
    }
  }

  function dynamicHeader(title, description, iconName) {
    const header = element("header", "dynamic-header");
    const mark = element("span", "dynamic-header-icon");
    mark.append(icon(iconName));
    const copy = element("div");
    copy.append(element("h2", "", title), element("p", "", description));
    header.append(mark, copy);
    return header;
  }

  function metricCard(label, value, hint) {
    const card = element("article", "dynamic-metric");
    card.append(element("span", "", label), element("strong", "", value), element("small", "", hint || ""));
    return card;
  }

  function serializeTraceContent(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  function detectTraceFormat(value, hint) {
    const normalizedHint = String(hint || "").trim().toLowerCase();
    if (normalizedHint.includes("json")) return "json";
    if (normalizedHint.includes("xml")) return "xml";
    if (normalizedHint.includes("markdown") || normalizedHint === "md") return "markdown";
    if (normalizedHint.includes("code") || normalizedHint.includes("python") || normalizedHint.includes("javascript") || normalizedHint.includes("typescript")) return "code";
    if (value && typeof value === "object") return "json";

    const text = serializeTraceContent(value).trim();
    if (!text) return "plain";
    if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
      try {
        JSON.parse(text);
        return "json";
      } catch {
        // Keep malformed structured output visible as plain text.
      }
    }
    if (/^<\?xml\b|^<[A-Za-z_][\w:.-]*(?:\s[^>]*)?\/?>(?:[\s\S]*<\/[^>]+>)?$/.test(text)) return "xml";
    if (/^```[\s\S]*```$/.test(text) || /(?:^|\n)(?: {4}|\t)\S/.test(text)) return "code";
    if (/(?:^|\n)#{1,6}\s|(?:^|\n)\s*[-*+]\s+|\[[^\]]+\]\([^)]+\)/.test(text)) return "markdown";
    return "plain";
  }

  function formatTraceContent(value, format) {
    const text = serializeTraceContent(value);
    if (format !== "json" || !text.trim()) return text;
    try {
      return JSON.stringify(typeof value === "string" ? JSON.parse(value) : value, null, 2);
    } catch {
      return text;
    }
  }

  async function copyTraceContent(text, label) {
    let fallbackTextarea = null;
    try {
      if (global.navigator && global.navigator.clipboard && typeof global.navigator.clipboard.writeText === "function") {
        await global.navigator.clipboard.writeText(text);
      } else {
        fallbackTextarea = element("textarea", "trace-copy-fallback");
        fallbackTextarea.value = text;
        fallbackTextarea.setAttribute("readonly", "");
        document.body.append(fallbackTextarea);
        fallbackTextarea.select();
        if (typeof document.execCommand !== "function" || !document.execCommand("copy")) throw new Error("copy unavailable");
      }
      showToast(`${label}已复制`, "success");
    } catch {
      showToast("复制失败，请手动选择文本", "error");
    } finally {
      if (fallbackTextarea) fallbackTextarea.remove();
    }
  }

  function traceTextViewer(title, value, options) {
    const settings = options || {};
    const format = detectTraceFormat(value, settings.format);
    const text = formatTraceContent(value, format);
    const viewer = element("section", `trace-text-viewer trace-format-${format}`);
    const header = element("header", "trace-viewer-head");
    const titleGroup = element("div", "trace-viewer-title");
    titleGroup.append(element("strong", "", title));
    const badges = element("span", "trace-viewer-badges");
    const formatLabels = { plain: "纯文本", markdown: "Markdown", code: "代码", json: "JSON", xml: "XML" };
    badges.append(element("span", "trace-format-badge", formatLabels[format] || "文本"));
    if (settings.preview) badges.append(element("span", "trace-preview-badge", "预览"));
    if (text) badges.append(element("span", "trace-char-count", `${text.length} 字符`));
    titleGroup.append(badges);
    const actions = element("div", "trace-viewer-actions");
    if (text && (settings.expandable || text.length > 1200)) {
      const expandButton = element("button", "trace-expand-button");
      expandButton.type = "button";
      expandButton.setAttribute("aria-expanded", "false");
      expandButton.setAttribute("aria-label", `展开${title}`);
      expandButton.append(icon("maximize-2"), element("span", "", "展开"));
      expandButton.addEventListener("click", () => {
        const expanded = viewer.classList.toggle("is-expanded");
        expandButton.setAttribute("aria-expanded", String(expanded));
        expandButton.setAttribute("aria-label", `${expanded ? "收起" : "展开"}${title}`);
        expandButton.replaceChildren(icon(expanded ? "minimize-2" : "maximize-2"), element("span", "", expanded ? "收起" : "展开"));
        refreshIcons();
      });
      actions.append(expandButton);
    }
    const copyButton = element("button", "trace-copy-button");
    copyButton.type = "button";
    copyButton.disabled = !text;
    copyButton.setAttribute("aria-label", `复制${title}`);
    copyButton.append(icon("copy"), element("span", "", "复制"));
    copyButton.addEventListener("click", () => copyTraceContent(text, title));
    actions.append(copyButton);
    header.append(titleGroup, actions);
    viewer.append(header);
    if (text) {
      const content = element("pre", "trace-content", text);
      content.tabIndex = 0;
      viewer.append(content);
    } else {
      viewer.append(element("div", "trace-content-empty", settings.emptyText || `暂无${title}`));
    }
    return viewer;
  }

  function firstTraceValue(source, names) {
    if (!source || typeof source !== "object") return undefined;
    for (const name of names) {
      if (source[name] !== undefined && source[name] !== null) return source[name];
    }
    return undefined;
  }

  function operationalNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatOperationalNumber(value) {
    const parsed = operationalNumber(value);
    return parsed === null ? "--" : Math.round(parsed).toLocaleString("zh-CN");
  }

  function operationalMetric(label, value, hint) {
    const metric = element("article", "runtime-status-metric");
    metric.append(element("span", "", label), element("strong", "", value), element("small", "", hint || ""));
    return metric;
  }

  function operationalSectionTitle(title, description, stateLabel, tone) {
    const header = element("header", "runtime-section-head");
    const copy = element("div");
    copy.append(element("h4", "", title), element("p", "", description));
    header.append(copy);
    if (stateLabel) header.append(element("span", `runtime-state-badge ${tone || ""}`.trim(), stateLabel));
    return header;
  }

  function buildProviderPromptCachePanel(payload, loadError, chatProviderPayload, chatProviderLoadError) {
    const source = payload && typeof payload === "object" ? payload : {};
    const observedProviders = Array.isArray(payload)
      ? payload.filter((item) => item && typeof item === "object")
      : Array.isArray(source.items)
        ? source.items.filter((item) => item && typeof item === "object")
        : [];
    const providers = observedProviders.map((item) => ({ ...item }));
    const declaredProviders = chatProviderPayload && Array.isArray(chatProviderPayload.providers)
      ? chatProviderPayload.providers.filter((item) => item && typeof item === "object")
      : [];
    const known = new Set(providers.map((item) => `${item.provider_id || ""}\u0000${item.model || ""}`));
    declaredProviders.forEach((provider) => {
      const key = `${provider.id || ""}\u0000${provider.model || ""}`;
      if (known.has(key)) return;
      providers.push({
        provider_id: provider.id || "",
        provider_type: provider.adapter || "",
        model: provider.model || "",
        capability: provider.capability || "unknown",
        usage_observability: "unknown",
        observed_samples: 0,
        cached_samples: 0,
        input_cached: 0,
        input_other: 0,
        output_tokens: 0,
      });
    });

    const panel = element("section", "dynamic-panel runtime-status-panel");
    const panelHead = element("header", "runtime-panel-head");
    const panelCopy = element("div");
    panelCopy.append(
      element("h3", "", "Provider Prompt Cache"),
      element("p", "", "只展示 Provider 返回的真实 cached-token 与能力观测，不在插件内复用模型输出。"),
    );
    panelHead.append(panelCopy, element("span", "runtime-panel-badge", "Provider 管理"));
    panel.append(panelHead);
    if (loadError && !providers.length) {
      panel.append(element("div", "runtime-status-notice error", loadError));
      return panel;
    }

    const totals = providers.reduce((result, provider) => {
      result.observed += operationalNumber(provider.observed_samples) || 0;
      result.cached += operationalNumber(provider.cached_samples) || 0;
      result.inputCached += operationalNumber(provider.input_cached) || 0;
      result.inputOther += operationalNumber(provider.input_other) || 0;
      result.output += operationalNumber(provider.output_tokens) || 0;
      return result;
    }, { observed: 0, cached: 0, inputCached: 0, inputOther: 0, output: 0 });
    const allUnsupported = providers.length > 0 && providers.every((provider) =>
      provider.capability === "unsupported" || provider.usage_observability === "unsupported");
    const hitRate = totals.observed > 0 ? `${(totals.cached * 100 / totals.observed).toFixed(1)}%` : "--";
    const prefixSection = element("section", "runtime-status-section");
    prefixSection.append(operationalSectionTitle(
      "真实命中观测",
      "usage 缺失保持 unknown；只有 Provider 明确报告的 cached token 才计入命中证据。",
      totals.observed > 0 ? "可观测" : allUnsupported ? "不支持" : "尚无样本",
      totals.observed > 0 ? "ready" : allUnsupported ? "warning" : "empty",
    ));
    const metrics = element("div", "runtime-status-metrics");
    metrics.append(
      operationalMetric("Provider / Model", formatOperationalNumber(providers.length), "已加载或已观测组合"),
      operationalMetric("Usage 样本", formatOperationalNumber(totals.observed), "Provider 返回真实 usage"),
      operationalMetric("Prefix 命中", formatOperationalNumber(totals.cached), `样本命中率 ${hitRate}`),
      operationalMetric("Cached Token", formatOperationalNumber(totals.inputCached), "Provider 明确报告"),
      operationalMetric("Other Input", formatOperationalNumber(totals.inputOther), "非 cached 输入 Token"),
      operationalMetric("Output Token", formatOperationalNumber(totals.output), "真实输出 Token"),
    );
    prefixSection.append(metrics);
    if (!totals.observed) {
      prefixSection.append(element(
        "div",
        "runtime-status-notice warning",
        allUnsupported ? "当前 Provider 明确不支持可观测的 prompt cache usage。" : "尚无可观测 usage，不能把 0 当成未命中。",
      ));
    }
    if (loadError) prefixSection.append(element("div", "runtime-status-notice warning", loadError));
    if (chatProviderLoadError) prefixSection.append(element("div", "runtime-status-notice warning", chatProviderLoadError));

    const providerGroup = element("div", "runtime-status-subsection");
    providerGroup.append(element("h5", "", "Provider Cache Capability"));
    const providerList = element("div", "runtime-purpose-list");
    if (!providers.length) providerList.append(element("div", "runtime-status-notice", "尚无 Provider 能力记录。"));
    providers.forEach((provider) => {
      const row = element("article", "runtime-detail-row");
      const rowHead = element("div", "runtime-detail-head");
      rowHead.append(
        element("code", "runtime-code-label", provider.provider_id || "unknown"),
        element("span", "runtime-capability-badge", provider.capability || "unknown"),
      );
      const stats = element("dl", "runtime-inline-stats");
      [
        ["Model", provider.model || "--"],
        ["Usage", provider.usage_observability || "unknown"],
        ["样本", formatOperationalNumber(provider.observed_samples)],
        ["命中", formatOperationalNumber(provider.cached_samples)],
        ["Cached", formatOperationalNumber(provider.input_cached)],
        ["Other", formatOperationalNumber(provider.input_other)],
        ["最近观测", provider.last_seen_at || "--"],
      ].forEach(([label, value]) => {
        const stat = element("div");
        stat.append(element("dt", "", label), element("dd", "", value));
        stats.append(stat);
      });
      row.append(rowHead, stats);
      providerList.append(row);
    });
    providerGroup.append(providerList);
    prefixSection.append(providerGroup);
    panel.append(prefixSection);
    return panel;
  }

  async function renderOverviewView() {
    refs.dynamicWorkspace.replaceChildren(dynamicHeader("运行总览", "协议、上下文与 Provider prompt-cache 的真实运行状态。", "layout-dashboard"));
    const loading = element("div", "dynamic-loading", "正在读取运行数据…");
    refs.dynamicWorkspace.append(loading);
    try {
      let providerCacheError = "";
      let chatProviderError = "";
      const [overview, contextStats, runsPayload, providerCache, chatProviders] = await Promise.all([
        api.getOverview(),
        api.getContextStats(),
        api.getContextRuns({ page: 1, page_size: 8 }),
        api.getProviderCacheCapabilities().catch((error) => {
          providerCacheError = error && error.message ? error.message : "Provider prompt-cache 状态读取失败";
          return null;
        }),
        api.getChatProviders().catch((error) => {
          chatProviderError = error && error.message ? error.message : "Chat Provider 状态读取失败";
          return null;
        }),
      ]);
      if (state.activeView !== "overview") return;
      refs.dynamicWorkspace.replaceChildren(dynamicHeader("运行总览", "协议、上下文与 Provider prompt-cache 的真实运行状态。", "layout-dashboard"));
      const omittedSections = Array.isArray(contextStats.sections)
        ? contextStats.sections.reduce((total, item) => total + numberValue(item.omitted, 0), 0)
        : contextStats.truncated_runs ?? contextStats.truncated ?? "--";
      const grid = element("section", "dynamic-metrics-grid");
      grid.append(
        metricCard("词条", overview.learned_count ?? overview.learned ?? "--", "当前有效词条"),
        metricCard("待处理", overview.pending_count ?? overview.pending ?? "--", "候选与冲突"),
        metricCard("协议成功率", formatRate(overview.protocol_success_rate), `${overview.protocol_samples ?? 0} 个样本`),
        metricCard("上下文运行", contextStats.runs ?? contextStats.total_runs ?? contextStats.total ?? "--", "可审计请求"),
        metricCard("平均 Token", contextStats.average_tokens ?? contextStats.avg_tokens ?? "--", "实际注入估算"),
        metricCard("省略段", omittedSections, "预算或无匹配"),
      );
      refs.dynamicWorkspace.append(grid);
      const statusStack = element("section", "runtime-status-stack");
      statusStack.append(buildProviderPromptCachePanel(providerCache, providerCacheError, chatProviders, chatProviderError));
      refs.dynamicWorkspace.append(statusStack);
      const section = element("section", "dynamic-panel");
      section.append(element("h3", "", "最近上下文运行"));
      appendContextRunTable(section, normalizeCollection(runsPayload).items, false);
      refs.dynamicWorkspace.append(section);
      refreshIcons();
    } catch (error) {
      loading.textContent = error.message || "运行总览加载失败";
      loading.classList.add("error");
    }
  }

  function contextRunId(run) {
    return run.request_id ?? run.id ?? run.run_id ?? "";
  }

  function appendContextRunTable(container, runs, interactive) {
    const wrap = element("div", "context-table-wrap");
    const table = element("table", "context-table");
    const head = element("thead");
    const header = element("tr");
    ["时间", "作用域", "请求", "Token", "裁剪"].forEach((label) => header.append(element("th", "", label)));
    head.append(header);
    const body = element("tbody");
    if (!runs.length) {
      const row = element("tr");
      const cell = element("td", "dynamic-empty", "暂无上下文运行记录");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
    } else {
      runs.forEach((run) => {
        const row = element("tr", interactive ? "clickable" : "");
        row.append(
          element("td", "", run.created_at || run.time || "--"),
          element("td", "", formatScopeLabel(run.scope_type, run.scope_id)),
          element("td", "", String(run.request_id || run.id || "--")),
          element("td", "", String(run.estimated_tokens ?? run.total_tokens ?? run.used_tokens ?? "--")),
          element("td", "", String(run.omitted_sections ?? run.truncated_count ?? (run.truncated ? 1 : 0))),
        );
        if (interactive) row.addEventListener("click", () => loadContextRunDetail(contextRunId(run)));
        body.append(row);
      });
    }
    table.append(head, body);
    wrap.append(table);
    container.append(wrap);
  }

  async function renderContextView() {
    refs.dynamicWorkspace.replaceChildren(dynamicHeader("上下文追踪", "查看模型调用瞬间的完整 ProviderRequest、LLMResponse 与插件注入明细。", "scan-search"));
    const layout = element("section", "context-layout");
    const list = element("div", "dynamic-panel context-run-list");
    list.append(element("div", "dynamic-loading", "正在读取上下文运行…"));
    const detail = element("aside", "dynamic-panel context-run-detail");
    detail.id = "contextRunDetail";
    detail.append(element("div", "dynamic-empty", "选择一条运行记录查看注入详情"));
    layout.append(list, detail);
    refs.dynamicWorkspace.append(layout);
    try {
      const payload = await api.getContextRuns({ page: 1, page_size: 50 });
      if (state.activeView !== "context") return;
      list.replaceChildren(element("h3", "", "最近运行"));
      const runs = normalizeCollection(payload).items;
      appendContextRunTable(list, runs, true);
      if (runs.length) loadContextRunDetail(contextRunId(runs[0]));
    } catch (error) {
      list.replaceChildren(element("div", "dynamic-loading error", error.message || "上下文运行加载失败"));
    }
    refreshIcons();
  }

  async function loadContextRunDetail(id) {
    const container = document.getElementById("contextRunDetail");
    if (!container || !id) return;
    const requestId = ++state.contextDetailRequestId;
    container.replaceChildren(element("div", "dynamic-loading", "正在读取注入详情…"));
    try {
      const payload = await api.getContextRun(id);
      if (requestId !== state.contextDetailRequestId || state.activeView !== "context" || !document.body.contains(container)) return;
      const run = payload && payload.run && typeof payload.run === "object" ? payload.run : payload || {};
      const sections = Array.isArray(payload && payload.sections) ? payload.sections : Array.isArray(run.sections) ? run.sections : Array.isArray(run.items) ? run.items : [];
      const requestSnapshot = firstTraceValue(payload, ["request_snapshot"]) ?? firstTraceValue(run, ["request_snapshot"]);
      const legacySnapshot = firstTraceValue(payload, ["context_injection", "snapshot", "context_snapshot"]) ?? firstTraceValue(run, ["context_injection", "snapshot", "context_snapshot"]);
      const responseSnapshot = firstTraceValue(payload, ["response_snapshot"]) ?? firstTraceValue(run, ["response_snapshot"]);
      const response = firstTraceValue(payload, ["response", "result"]) ?? firstTraceValue(run, ["response", "result"]);
      const responseSource = response && typeof response === "object" ? response : responseSnapshot && typeof responseSnapshot === "object" ? responseSnapshot : undefined;
      const action = firstTraceValue(payload, ["action", "response_action"]) ?? firstTraceValue(run, ["action", "response_action"]) ?? firstTraceValue(responseSource, ["action"]);
      const messages = firstTraceValue(payload, ["messages", "response_messages"]) ?? firstTraceValue(run, ["messages", "response_messages"]) ?? firstTraceValue(response, ["messages"]);
      const rawOutput = firstTraceValue(payload, ["raw_output", "response_raw_output"]) ?? firstTraceValue(run, ["raw_output", "response_raw_output"]) ?? firstTraceValue(response, ["raw_output", "completion_text"]);
      const errorValue = firstTraceValue(payload, ["error", "error_detail", "failure_detail", "error_code"]) ?? firstTraceValue(run, ["error", "error_detail", "failure_detail", "error_code"]) ?? firstTraceValue(responseSource, ["error", "error_detail", "failure_detail", "error_code"]);
      container.replaceChildren();
      const detailHead = element("header", "context-detail-head");
      const heading = element("div");
      heading.append(element("span", "context-detail-eyebrow", "REQUEST TRACE"), element("h3", "", `请求 ${run.request_id || run.id || id}`));
      const runState = element("span", `context-run-state${errorValue ? " error" : ""}`, errorValue ? "存在错误" : "已记录");
      detailHead.append(heading, runState);
      container.append(detailHead);
      const summary = element("dl", "context-run-summary");
      [["时间", run.created_at || "--"], ["作用域", formatScopeLabel(run.scope_type, run.scope_id)], ["消息 ID", run.message_id || "--"], ["发送者", run.sender_id || "--"], ["Token", run.estimated_tokens ?? run.total_tokens ?? run.used_tokens ?? "--"], ["协议模式", run.protocol_mode || "--"]].forEach(([label, value]) => {
        const row = element("div"); row.append(element("dt", "", label), element("dd", "", value)); summary.append(row);
      });
      container.append(summary);

      const snapshotGroup = element("section", "context-detail-group");
      snapshotGroup.append(element("h4", "", "模型请求快照"), element("p", "context-detail-help", "Provider 调用前捕获，包含 AstrBot 与其他插件已经写入的全部可序列化请求字段。"));
      const displayedRequestSnapshot = requestSnapshot ?? legacySnapshot;
      snapshotGroup.append(traceTextViewer(requestSnapshot !== undefined ? "request_snapshot · 完整 ProviderRequest" : "兼容记录 · 上下文快照", displayedRequestSnapshot, {
        format: displayedRequestSnapshot && typeof displayedRequestSnapshot === "object" ? "json" : firstTraceValue(run, ["snapshot_format", "content_type"]),
        expandable: true,
        emptyText: "该运行暂未保存完整 ProviderRequest 快照",
      }));
      container.append(snapshotGroup);

      const sectionGroup = element("section", "context-detail-group");
      const sectionTitle = element("div", "context-group-title");
      sectionTitle.append(element("h4", "", "插件注入明细"), element("span", "", `${sections.length} 段`));
      sectionGroup.append(sectionTitle);
      const timeline = element("ol", "context-section-list");
      if (!sections.length) timeline.append(element("li", "dynamic-empty", "该运行没有可展示的上下文段"));
      sections.forEach((section, index) => {
        const item = element("li", `context-section${section.included === false || section.selected === false ? " omitted" : ""}`);
        const head = element("div", "context-section-head");
        head.append(element("strong", "", `${numberValue(section.ordinal ?? section.order ?? section.position, index) + 1}. ${section.section_key || section.name || section.section || section.kind || "未命名段"}`), element("span", "", `${section.applied_tokens ?? section.estimated_tokens ?? section.used_tokens ?? section.token_count ?? 0} / ${section.budget_tokens ?? section.token_budget ?? "--"} Token`));
        const meta = element("div", "context-section-meta");
        const targetValue = Array.isArray(section.targets) ? section.targets.join(" · ") : section.target || "--";
        const sourceValue = Array.isArray(section.source_refs) ? section.source_refs.join(" · ") : section.source_refs || section.source || section.source_type || "--";
        [["状态", section.included === false || section.selected === false ? "未注入" : "已注入"], ["目标", targetValue], ["来源", sourceValue], ["原因", section.reason || section.selection_reason || (section.truncated ? "已按预算裁剪" : "已注入")]].forEach(([label, value]) => {
          const metaItem = element("span");
          metaItem.append(element("b", "", label), document.createTextNode(String(value)));
          meta.append(metaItem);
        });
        const fullContent = firstTraceValue(section, ["content", "snapshot", "full_content", "content_text"]);
        const previewContent = firstTraceValue(section, ["content_preview", "preview"]);
        const sectionContent = fullContent ?? previewContent;
        const previewOnly = fullContent === undefined && previewContent !== undefined;
        const truncated = booleanValue(section.preview_truncated ?? section.truncated, false);
        item.append(head, meta, traceTextViewer("段内容", sectionContent, {
          format: section.content_type || section.format || section.language,
          preview: previewOnly || truncated,
          emptyText: section.included === false || section.selected === false ? "该段未注入，且没有内容快照" : "该段没有可展示内容",
        }));
        timeline.append(item);
      });
      sectionGroup.append(timeline);
      container.append(sectionGroup);

      const responseGroup = element("section", "context-detail-group context-response-group");
      const responseTitle = element("div", "context-group-title");
      responseTitle.append(element("h4", "", "模型响应快照"));
      if (action) responseTitle.append(element("span", `response-action${String(action).toLowerCase().includes("no reply") ? " no-reply" : ""}`, `Action · ${action}`));
      responseGroup.append(responseTitle);
      if (errorValue) {
        const errorNotice = element("div", "context-response-error");
        errorNotice.append(icon("circle-alert"), element("span", "", serializeTraceContent(errorValue)));
        responseGroup.append(errorNotice);
      }
      const responseHasData = responseSnapshot !== undefined || response !== undefined || messages !== undefined || rawOutput !== undefined || action || errorValue;
      if (!responseHasData) {
        responseGroup.append(element("div", "trace-response-empty", "该运行暂未关联响应记录"));
      } else {
        const displayedResponseSnapshot = responseSnapshot ?? response;
        if (displayedResponseSnapshot !== undefined) {
          responseGroup.append(traceTextViewer(responseSnapshot !== undefined ? "response_snapshot · 完整 LLMResponse" : "兼容记录 · 完整响应", displayedResponseSnapshot, {
            format: displayedResponseSnapshot && typeof displayedResponseSnapshot === "object" ? "json" : firstTraceValue(responseSource, ["raw_format", "content_type"]),
            expandable: true,
          }));
        }
        if (messages !== undefined) {
          const visibleMessages = Array.isArray(messages) && messages.every((item) => typeof item === "string") ? messages.join("\n\n") : messages;
          responseGroup.append(traceTextViewer("发送正文", visibleMessages, { format: typeof visibleMessages === "string" ? "plain" : "json", emptyText: "响应没有用户可见正文" }));
        }
        if (responseSnapshot === undefined && response === undefined && rawOutput !== undefined) {
          responseGroup.append(traceTextViewer("模型原始输出", rawOutput, { format: firstTraceValue(responseSource, ["raw_format", "content_type"]), expandable: true }));
        }
      }
      container.append(responseGroup);
      refreshIcons();
    } catch (error) {
      if (requestId !== state.contextDetailRequestId) return;
      container.replaceChildren(element("div", "dynamic-loading error", error.message || "上下文详情加载失败"));
    }
  }

  async function renderProtocolView() {
    refs.dynamicWorkspace.replaceChildren(dynamicHeader("协议监控", "只展示真实解析、拦截和 Action 记录。", "chart-no-axes-combined"));
    const panel = element("section", "dynamic-panel");
    panel.append(element("div", "dynamic-loading", "正在读取协议日志…"));
    refs.dynamicWorkspace.append(panel);
    try {
      const payload = await api.getProtocolLogs({ page: 1, page_size: 100 });
      if (state.activeView !== "protocol") return;
      const logs = normalizeCollection(payload).items;
      panel.replaceChildren(element("h3", "", `最近 ${logs.length} 条记录`));
      const wrap = element("div", "protocol-full-wrap");
      const table = element("table", "protocol-table protocol-full-table");
      const head = element("thead");
      const row = element("tr");
      ["时间", "Action", "状态", "详情"].forEach((label) => row.append(element("th", "", label)));
      head.append(row);
      const body = element("tbody");
      renderProtocolRows(body, logs);
      if (!logs.length) { const empty = element("tr"); const cell = element("td", "dynamic-empty", "暂无协议日志"); cell.colSpan = 4; empty.append(cell); body.append(empty); }
      table.append(head, body); wrap.append(table); panel.append(wrap);
    } catch (error) {
      panel.replaceChildren(element("div", "dynamic-loading error", error.message || "协议日志加载失败"));
    }
  }

  function appendPromptTemplateCards(container, templates) {
    const list = element("div", "prompt-template-list");
    templates.forEach((template) => {
      const card = element("article", "prompt-template-card");
      const header = element("header", "prompt-template-head");
      const heading = element("div");
      heading.append(element("h4", "", template.name));
      const badges = element("div", "prompt-template-badges");
      badges.append(element("code", "prompt-template-key", template.key));
      if (template.source) badges.append(element("span", "prompt-template-source", template.source));
      heading.append(badges);
      if (template.updatedAt) header.append(heading, element("span", "prompt-template-updated", `更新于 ${template.updatedAt}`));
      else header.append(heading);
      card.append(header);
      if (template.description) card.append(element("p", "prompt-template-description", template.description));
      if (template.variables.length) {
        const variables = element("div", "prompt-template-variables");
        variables.append(element("span", "", "可用变量"));
        template.variables.forEach((variable) => {
          const label = variable.startsWith("{{") && variable.endsWith("}}")
            ? variable
            : `{{${variable}}}`;
          variables.append(element("code", "", label));
        });
        card.append(variables);
      }
      const editor = element("textarea", "prompt-template-editor");
      editor.value = template.content;
      editor.rows = 12;
      editor.spellcheck = false;
      editor.readOnly = !template.editable;
      editor.setAttribute("aria-label", `编辑${template.name}`);
      card.append(editor);

      const footer = element("footer", "prompt-template-footer");
      const status = element("span", "prompt-template-status", template.editable ? `${template.content.length} 字符` : "只读模板");
      const actions = element("div", "prompt-template-actions");
      const resetButton = element("button", "text-button");
      resetButton.type = "button";
      resetButton.disabled = !template.editable;
      resetButton.append(icon("rotate-ccw"), element("span", "", "恢复默认"));
      const discardButton = element("button", "text-button");
      discardButton.type = "button";
      discardButton.disabled = !template.editable;
      discardButton.append(icon("undo-2"), element("span", "", "撤销修改"));
      const saveButton = element("button", "secondary-button prompt-template-save");
      saveButton.type = "button";
      saveButton.disabled = true;
      saveButton.append(icon("save"), element("span", "", "保存模板"));
      actions.append(resetButton, discardButton, saveButton);
      footer.append(status, actions);
      card.append(footer);

      const updateDirtyState = () => {
        const dirty = editor.value !== template.content;
        const pending = state.promptTemplatePendingKey === template.key;
        card.classList.toggle("dirty", dirty);
        status.textContent = pending ? "正在保存…" : dirty ? `未保存 · ${editor.value.length} 字符` : template.editable ? `${editor.value.length} 字符` : "只读模板";
        editor.disabled = pending;
        resetButton.disabled = !template.editable || pending;
        discardButton.disabled = !template.editable || !dirty || pending;
        saveButton.disabled = !template.editable || !dirty || pending || !editor.value.trim();
      };
      editor.addEventListener("input", updateDirtyState);
      discardButton.addEventListener("click", () => {
        editor.value = template.content;
        updateDirtyState();
      });
      resetButton.addEventListener("click", async () => {
        if (state.promptTemplatePendingKey || !global.confirm(`确定将“${template.name}”恢复为内置默认模板吗？`)) return;
        state.promptTemplatePendingKey = template.key;
        updateDirtyState();
        try {
          const payload = await api.savePromptTemplate({ key: template.key, action: "reset" });
          let returned = normalizePromptTemplates(payload);
          if (!returned.some((item) => item.key === template.key)) returned = normalizePromptTemplates(await api.getPromptTemplates());
          const restored = returned.find((item) => item.key === template.key);
          if (!restored) throw new Error("后端未返回恢复后的模板");
          Object.assign(template, restored);
          editor.value = template.content;
          showToast(`${template.name}已恢复默认`, "success");
        } catch (error) {
          showToast(error.message || "恢复默认模板失败", "error");
        } finally {
          state.promptTemplatePendingKey = "";
          updateDirtyState();
        }
      });
      saveButton.addEventListener("click", async () => {
        const content = editor.value;
        if (!content.trim() || state.promptTemplatePendingKey) return;
        state.promptTemplatePendingKey = template.key;
        updateDirtyState();
        try {
          const payload = await api.savePromptTemplate({ key: template.key, content });
          const returned = normalizePromptTemplates(payload);
          const saved = returned.find((item) => item.key === template.key);
          template.content = saved ? saved.content : content;
          editor.value = template.content;
          showToast(`${template.name}已保存`, "success");
        } catch (error) {
          showToast(error.message || "提示词模板保存失败", "error");
        } finally {
          state.promptTemplatePendingKey = "";
          updateDirtyState();
        }
      });
      updateDirtyState();
      list.append(card);
    });
    container.append(list);
  }

  async function renderSettingsView() {
    refs.dynamicWorkspace.replaceChildren(dynamicHeader("设置", "查看公开配置，并直接管理 Humanize 实际使用的提示词模板。", "settings"));
    const workspace = element("section", "settings-workspace");
    const configPanel = element("section", "dynamic-panel settings-config-panel");
    const promptPanel = element("section", "dynamic-panel prompt-template-panel");
    configPanel.append(element("div", "dynamic-loading", "正在读取设置…"));
    promptPanel.append(element("div", "dynamic-loading", "正在读取提示词模板…"));
    workspace.append(configPanel, promptPanel);
    refs.dynamicWorkspace.append(workspace);
    try {
      const settings = await api.getSettings();
      if (state.activeView !== "settings") return;
      state.settings = settings && typeof settings === "object" ? settings : {};
      configPanel.replaceChildren(element("h3", "", "当前公开配置"));
      const list = element("dl", "settings-list");
      Object.entries(state.settings).filter(([, value]) => Array.isArray(value) || ["string", "number", "boolean"].includes(typeof value)).forEach(([key, value]) => {
        const displayValue = Array.isArray(value)
          ? (value.length ? value.map((item) => String(item)).join("、") : "（空）")
          : typeof value === "boolean" ? (value ? "开启" : "关闭") : value;
        const row = element("div"); row.append(element("dt", "", key), element("dd", "", displayValue)); list.append(row);
      });
      configPanel.append(list);
      renderSettingsSummary();

      let promptPayload;
      let promptError = "";
      try {
        promptPayload = await api.getPromptTemplates();
      } catch (error) {
        const embedded = firstTraceValue(state.settings, ["prompt_templates", "prompts"]);
        if (embedded !== undefined) promptPayload = { templates: embedded };
        else promptError = error.message || "提示词模板接口暂不可用";
      }
      if (state.activeView !== "settings") return;
      state.promptTemplates = normalizePromptTemplates(promptPayload);
      const promptHeading = element("div", "context-group-title prompt-template-title");
      promptHeading.append(element("h3", "", "提示词模板"), element("span", "", `${state.promptTemplates.length} 个模板`));
      promptPanel.replaceChildren(promptHeading, element("p", "prompt-template-intro", "这里编辑的是插件运行时模板；变量占位符会在实际调用时由后端填充，请勿随意删除。"));
      if (state.promptTemplates.length) appendPromptTemplateCards(promptPanel, state.promptTemplates);
      else promptPanel.append(element("div", `dynamic-empty${promptError ? " error" : ""}`, promptError || "暂无可编辑的提示词模板"));
      refreshIcons();
    } catch (error) {
      configPanel.replaceChildren(element("div", "dynamic-loading error", error.message || "设置加载失败"));
      promptPanel.replaceChildren(element("div", "dynamic-loading error", "提示词模板无法加载"));
    }
  }

  function activateView(view) {
    state.activeView = view;
    if (view !== "memory" && global.HumanizeMemory && typeof global.HumanizeMemory.close === "function") global.HumanizeMemory.close();
    if (view !== "examples" && global.HumanizeExamples && typeof global.HumanizeExamples.close === "function") global.HumanizeExamples.close();
    const isJargon = view === "jargons";
    document.querySelectorAll("[data-nav]").forEach((button) => {
      const active = button.dataset.nav === view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    refs.legacyWorkspace.hidden = !isJargon;
    refs.dynamicWorkspace.hidden = isJargon;
    refs.scopePickerWrap.hidden = !isJargon;
    refs.jargonTopbarActions.hidden = !isJargon;
    refs.pageTitle.textContent = isJargon ? "黑话词库" : DYNAMIC_TITLES[view] || "Humanize";
    if (!isJargon) {
      if (view === "overview") renderOverviewView();
      else if (view === "context") renderContextView();
      else if (view === "memory") global.HumanizeMemory.open();
      else if (view === "examples") global.HumanizeExamples.open();
      else if (view === "protocol") renderProtocolView();
      else if (view === "settings") renderSettingsView();
    }
  }

  function scheduleSearch(value) {
    state.search = value.trim();
    refs.globalSearch.value = value;
    refs.tableSearch.value = value;
    global.clearTimeout(searchTimer);
    searchTimer = global.setTimeout(() => { state.page = 1; loadJargons(); }, 260);
  }

  function bindEvents() {
    refs.globalSearch.addEventListener("input", (event) => scheduleSearch(event.target.value));
    refs.tableSearch.addEventListener("input", (event) => scheduleSearch(event.target.value));
    refs.statusFilter.addEventListener("change", (event) => { state.status = event.target.value; state.page = 1; loadJargons(); });
    [refs.scopeFilter, refs.scopePicker].forEach((select) => select.addEventListener("change", (event) => {
      const scope = parseScopeKey(event.target.value); state.scopeType = scope.type; state.scopeId = scope.id; refs.scopeFilter.value = event.target.value; refs.scopePicker.value = event.target.value; state.page = 1; loadJargons();
    }));
    refs.pageSize.addEventListener("change", (event) => { state.pageSize = Math.max(1, Number(event.target.value) || 10); state.page = 1; loadJargons(); });
    refs.exportButton.addEventListener("click", exportJargons);
    refs.learningToggle.addEventListener("change", () => { refs.learningToggle.checked = (state.settings.learning_enabled ?? state.settings.jargon_enabled) !== false; showToast("学习状态请在 AstrBot 插件配置页修改", "info"); });
    refs.saveEntryButton.addEventListener("click", saveEntrySettings);
    refs.toggleNewSenseButton.addEventListener("click", () => { refs.newSenseForm.hidden = false; refs.newSenseInput.focus(); });
    refs.cancelNewSenseButton.addEventListener("click", () => { refs.newSenseForm.hidden = true; refs.newSenseInput.value = ""; });
    refs.createSenseButton.addEventListener("click", createSense);
    refs.evidenceSenseFilter.addEventListener("change", () => { if (state.selectedDetail) renderEvidence(state.selectedDetail); });
    refs.mergeSensesButton.addEventListener("click", () => {
      const source = refs.mergeSourceSelect.value; const target = refs.mergeTargetSelect.value;
      if (!source || !target || source === target) { showToast("请选择两个不同含义", "error"); return; }
      performJargonAction("merge_sense", { source_sense_id: Number(source), target_sense_id: Number(target) });
    });
    refs.deleteEntryButton.addEventListener("click", () => { if (global.confirm("确定删除整个词条及其含义和证据吗？")) performJargonAction("delete"); });
    document.querySelectorAll("[data-nav]").forEach((button) => button.addEventListener("click", () => activateView(button.dataset.nav)));
  }

  function collectRefs() {
    [
      "scopePicker", "scopePickerWrap", "jargonTopbarActions", "globalSearch", "exportButton", "learningToggle",
      "metricLearned", "metricPending", "metricProtocol", "metricBlocked", "tableSearch", "statusFilter", "scopeFilter",
      "pageSize", "jargonRows", "tableState", "paginationSummary", "pagination", "detailEmpty", "detailContent",
      "detailTerm", "detailStatus", "detailConflict", "detailEnabledLabel", "matchModeSelect", "caseSensitiveToggle",
      "entryEnabledToggle", "aliasesInput", "saveEntryButton", "senseSummary", "toggleNewSenseButton", "newSenseForm",
      "newSenseInput", "cancelNewSenseButton", "createSenseButton", "senseList", "mergeSenses", "mergeSourceSelect",
      "mergeTargetSelect", "mergeSensesButton", "detailConfidence", "confidenceFill", "detailScope", "detailOccurrences",
      "detailFirstSeen", "detailLastSeen", "evidenceSenseFilter", "evidenceList", "deleteEntryButton", "chartSuccessRate",
      "protocolChart", "chartLabels", "protocolRows", "protocolState", "ruleEnabled", "ruleInjectionMode", "ruleAdministrator",
      "ruleMessageLimit", "ruleMessageCount", "toastRegion", "legacyWorkspace", "dynamicWorkspace",
    ].forEach((id) => { refs[id] = document.getElementById(id); });
    refs.pageTitle = document.querySelector(".page-heading h1");
  }

  async function boot() {
    collectRefs();
    global.HumanizeMemory.mount(refs.dynamicWorkspace, { notify: showToast });
    global.HumanizeExamples.mount(refs.dynamicWorkspace, { notify: showToast });
    bindEvents();
    refreshIcons();
    setStateNode(refs.tableState, "正在连接插件…");
    setStateNode(refs.protocolState, "正在连接插件…");
    try {
      await api.ready();
      const [settings, overview] = await Promise.all([api.getSettings(), api.getOverview()]);
      state.settings = settings && typeof settings === "object" ? settings : {};
      state.overview = overview && typeof overview === "object" ? overview : {};
      renderScopes();
      renderSettingsSummary();
      renderMetrics();
      await Promise.all([loadJargons(), loadProtocolLogs()]);
    } catch (error) {
      setStateNode(refs.tableState, error.message || "插件连接失败");
      setStateNode(refs.protocolState, error.message || "插件连接失败");
      showToast(error.message || "插件连接失败", "error");
    }
  }

  document.addEventListener("DOMContentLoaded", boot, { once: true });
})(window, document);
