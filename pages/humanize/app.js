(function initializeHumanizePage(global, document) {
  "use strict";

  const api = global.HumanizeApi;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const STATUS_META = {
    candidate: { label: "待确认", className: "status-candidate" },
    confirmed: { label: "已确认", className: "status-confirmed" },
    ambiguous: { label: "有歧义", className: "status-ambiguous" },
    rejected: { label: "已拒绝", className: "status-rejected" },
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
    loadingList: false,
    loadingDetail: false,
    actionPending: false,
    listRequestId: 0,
    detailRequestId: 0,
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

  function normalizeConfidence(value) {
    const numeric = numberValue(value, 0);
    return Math.max(0, Math.min(100, numeric <= 1 ? numeric * 100 : numeric));
  }

  function formatConfidence(value) {
    return `${Math.round(normalizeConfidence(value))}%`;
  }

  function formatRate(value) {
    if (value === null || value === undefined || value === "") return "--";
    const numeric = numberValue(value, 0);
    return `${numeric.toFixed(1)}%`;
  }

  function normalizeStatus(value) {
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
      return {
        type: decodeURIComponent(key.slice(0, separator)),
        id: decodeURIComponent(key.slice(separator + 1)),
      };
    } catch {
      return { type: "", id: "" };
    }
  }

  function normalizeJargon(item) {
    const envelope = item && typeof item === "object" ? item : {};
    const source = envelope.entry && typeof envelope.entry === "object" ? envelope.entry : envelope;
    return {
      id: source.id,
      status: normalizeStatus(source.status || source.state),
      term: String(source.term || source.content || source.word || ""),
      meaning: String(source.meaning || source.guess || source.inferred_meaning || "含义待推断"),
      scopeType: String(source.scope_type || (String(source.scope_id || source.chat_id || "").startsWith("qq-friend-") ? "private" : "group")),
      scopeId: String(source.scope_id || source.chat_id || ""),
      scopeLabel: String(source.scope_label || source.scope_name || source._chat_name
        || formatScopeLabel(source.scope_type, source.scope_id || source.chat_id)),
      confidence: normalizeConfidence(source.confidence),
      occurrences: numberValue(source.occurrence_count ?? source.count, 0),
      firstSeen: String(source.first_seen || source.first_seen_at || source.created_at || "--"),
      lastSeen: String(source.last_seen || source.last_seen_at || source.updated_at || "--"),
      evidence: Array.isArray(envelope.evidence)
        ? envelope.evidence
        : Array.isArray(source.evidence) ? source.evidence : [],
    };
  }

  function normalizeCollection(payload) {
    if (Array.isArray(payload)) return { items: payload, total: payload.length };
    const source = payload && typeof payload === "object" ? payload : {};
    const items = Array.isArray(source.items) ? source.items : [];
    return { items, total: numberValue(source.total, items.length) };
  }

  function showToast(message, type) {
    const toast = element("div", `toast ${type || "info"}`);
    toast.append(icon(type === "success" ? "check-circle-2" : type === "error" ? "circle-alert" : "info"));
    toast.append(element("span", "", message));
    refs.toastRegion.append(toast);
    refreshIcons();
    global.setTimeout(() => toast.remove(), 3200);
  }

  function setTableState(message) {
    if (!message) {
      refs.tableState.hidden = true;
      refs.tableState.textContent = "";
      return;
    }
    refs.tableState.textContent = message;
    refs.tableState.hidden = false;
  }

  function setProtocolState(message) {
    if (!message) {
      refs.protocolState.hidden = true;
      refs.protocolState.textContent = "";
      return;
    }
    refs.protocolState.textContent = message;
    refs.protocolState.hidden = false;
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
      value: item.value === null || item.value === undefined
        ? null
        : Math.max(0, Math.min(100, numberValue(item.value ?? item.rate, 0))),
      total: numberValue(item.total, 0),
    }));

    refs.protocolChart.replaceChildren();
    refs.chartLabels.replaceChildren();

    points.forEach((point) => refs.chartLabels.append(element("span", "", point.label)));
    const sampled = points
      .map((point, index) => ({ ...point, index }))
      .filter((point) => point.value !== null && (point.total > 0 || point.value !== null));

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
    const pointText = sampled.map((point) => {
      const x = (point.index / Math.max(1, points.length - 1)) * 352 + 4;
      const y = 94 - (point.value / 100) * 84;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    polyline.setAttribute("points", pointText);
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", "#709b72");
    polyline.setAttribute("stroke-width", "1.5");
    polyline.setAttribute("vector-effect", "non-scaling-stroke");
    if (sampled.length > 1) refs.protocolChart.append(polyline);

    sampled.forEach((point) => {
      const x = (point.index / Math.max(1, points.length - 1)) * 352 + 4;
      const y = 94 - (point.value / 100) * 84;
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", x.toFixed(1));
      circle.setAttribute("cy", y.toFixed(1));
      circle.setAttribute("r", "2.6");
      circle.setAttribute("fill", "#709b72");
      circle.setAttribute("vector-effect", "non-scaling-stroke");
      refs.protocolChart.append(circle);
    });
  }

  function renderScopes() {
    const settings = state.settings || {};
    const overview = state.overview || {};
    const configuredScopes = Array.isArray(settings.scopes)
      ? settings.scopes
      : Array.isArray(overview.scopes) ? overview.scopes : [];
    const scopes = configuredScopes.map((scope) => ({
      type: String(scope.type || scope.scope_type || ""),
      id: String(scope.id || scope.scope_id || ""),
      label: String(scope.label || scope.name || scope.scope_label
        || formatScopeLabel(scope.scope_type, scope.id || scope.scope_id)),
    })).filter((scope) => scope.type && scope.id);

    if (!scopes.length && settings.current_scope_id && settings.current_scope_type) {
      scopes.push({
        type: String(settings.current_scope_type || ""),
        id: String(settings.current_scope_id),
        label: String(settings.current_scope_label || settings.current_scope_id),
      });
    }

    refs.scopePicker.replaceChildren(element("option", "", "全部作用域"));
    refs.scopePicker.firstChild.value = "";
    refs.scopeFilter.replaceChildren(element("option", "", "作用域"));
    refs.scopeFilter.firstChild.value = "";

    scopes.forEach((scope) => {
      const topOption = element("option", "", scope.label);
      topOption.value = makeScopeKey(scope.type, scope.id);
      refs.scopePicker.append(topOption);
      const filterOption = element("option", "", scope.label);
      filterOption.value = makeScopeKey(scope.type, scope.id);
      refs.scopeFilter.append(filterOption);
    });

    if (!state.scopeId && settings.current_scope_id && settings.current_scope_type) {
      state.scopeType = String(settings.current_scope_type);
      state.scopeId = String(settings.current_scope_id);
    }
    const selectedScope = makeScopeKey(state.scopeType, state.scopeId);
    refs.scopePicker.value = selectedScope;
    refs.scopeFilter.value = selectedScope;
    refs.learningToggle.checked = (settings.learning_enabled ?? settings.jargon_enabled) !== false;
  }

  function renderSettings() {
    const settings = state.settings || {};
    const enabled = settings.default_rule_enabled !== false;
    refs.ruleEnabled.textContent = enabled ? "已启用" : "未启用";
    refs.ruleDot.classList.toggle("disabled", !enabled);
    refs.ruleInjectionMode.textContent = settings.protocol_injection_mode === "both"
      ? "用户 + System"
      : "用户消息（临时）";
    refs.ruleAdministrator.textContent = String(settings.administrator_name || settings.admin_name || "管理员");
    refs.ruleMessageLimit.textContent = `${numberValue(settings.max_message_chars, 10)} 字`;
    refs.ruleMessageCount.textContent = `${numberValue(settings.max_reply_messages, 12)} 条`;
  }

  function createStatusBadge(status) {
    const meta = STATUS_META[normalizeStatus(status)];
    return element("span", `status-badge ${meta.className}`, meta.label);
  }

  function createCell(text, title) {
    const cell = element("td", "", text);
    if (title) cell.title = title;
    return cell;
  }

  function selectJargon(id, options) {
    const normalizedId = String(id);
    state.selectedId = normalizedId;
    renderJargonRows();
    loadJargonDetail(normalizedId, options || {});
  }

  function renderJargonRows() {
    refs.jargonRows.replaceChildren();
    state.jargons.forEach((item) => {
      const row = element("tr", "jargon-row");
      row.dataset.id = String(item.id);
      row.tabIndex = 0;
      row.classList.toggle("selected", String(item.id) === String(state.selectedId));
      row.addEventListener("click", () => selectJargon(item.id));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectJargon(item.id);
        }
      });

      const statusCell = element("td");
      statusCell.append(createStatusBadge(item.status));
      row.append(statusCell);
      row.append(createCell(item.term, item.term));
      row.append(createCell(item.meaning, item.meaning));
      row.append(createCell(item.scopeLabel, item.scopeLabel));
      row.append(createCell(formatConfidence(item.confidence)));
      row.append(createCell(item.occurrences));
      row.append(createCell(item.lastSeen, item.lastSeen));

      const actionCell = element("td");
      const actions = element("span", "row-actions");
      const viewButton = element("button", "row-action");
      viewButton.type = "button";
      viewButton.title = "查看证据";
      viewButton.setAttribute("aria-label", `查看 ${item.term} 的证据`);
      viewButton.append(icon("eye"));
      viewButton.addEventListener("click", (event) => {
        event.stopPropagation();
        selectJargon(item.id);
      });
      const editButton = element("button", "row-action");
      editButton.type = "button";
      editButton.title = "编辑释义";
      editButton.setAttribute("aria-label", `编辑 ${item.term} 的释义`);
      editButton.append(icon("pencil"));
      editButton.addEventListener("click", (event) => {
        event.stopPropagation();
        selectJargon(item.id, { edit: true });
      });
      actions.append(viewButton, editButton);
      actionCell.append(actions);
      row.append(actionCell);
      refs.jargonRows.append(row);
    });
    refreshIcons();
  }

  function renderPagination() {
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    refs.paginationSummary.textContent = `共 ${state.total} 条`;
    refs.pagination.replaceChildren();

    const addButton = (label, page, options) => {
      const button = element("button", `page-button${options.active ? " active" : ""}`);
      button.type = "button";
      button.disabled = Boolean(options.disabled);
      button.setAttribute("aria-label", options.ariaLabel || `第 ${page} 页`);
      if (options.icon) button.append(icon(options.icon));
      else button.textContent = label;
      button.addEventListener("click", () => {
        if (page === state.page || button.disabled) return;
        state.page = page;
        loadJargons();
      });
      refs.pagination.append(button);
    };

    addButton("", Math.max(1, state.page - 1), {
      disabled: state.page <= 1,
      icon: "chevron-left",
      ariaLabel: "上一页",
      active: false,
    });

    const candidates = new Set([1, totalPages, state.page - 1, state.page, state.page + 1]);
    const pages = Array.from(candidates).filter((page) => page >= 1 && page <= totalPages).sort((a, b) => a - b);
    let previous = 0;
    pages.forEach((page) => {
      if (previous && page - previous > 1) refs.pagination.append(element("span", "page-ellipsis", "…"));
      addButton(String(page), page, { disabled: false, active: page === state.page });
      previous = page;
    });

    addButton("", Math.min(totalPages, state.page + 1), {
      disabled: state.page >= totalPages,
      icon: "chevron-right",
      ariaLabel: "下一页",
      active: false,
    });
    refreshIcons();
  }

  async function loadJargons(options) {
    const requestId = ++state.listRequestId;
    state.loadingList = true;
    setTableState("正在读取词条…");
    try {
      const payload = await api.getJargons({
        search: state.search,
        status: state.status,
        scope_type: state.scopeType,
        scope_id: state.scopeId,
        page: state.page,
        page_size: state.pageSize,
      });
      if (requestId !== state.listRequestId) return;
      const collection = normalizeCollection(payload);
      state.jargons = collection.items.map(normalizeJargon);
      state.total = collection.total;
      renderJargonRows();
      renderPagination();
      setTableState(state.jargons.length ? "" : "没有符合条件的词条");

      const keepSelection = state.jargons.some((item) => String(item.id) === String(state.selectedId));
      if (!keepSelection && state.jargons.length) {
        selectJargon(state.jargons[0].id, options || {});
      } else if (!state.jargons.length) {
        state.selectedId = null;
        state.selectedDetail = null;
        renderDetail();
      } else if (options && options.refreshDetail && state.selectedId) {
        await loadJargonDetail(state.selectedId);
      }
    } catch (error) {
      if (requestId !== state.listRequestId) return;
      state.jargons = [];
      state.total = 0;
      renderJargonRows();
      renderPagination();
      setTableState(error.message || "词条加载失败");
      showToast(error.message || "词条加载失败", "error");
    } finally {
      if (requestId === state.listRequestId) state.loadingList = false;
    }
  }

  function renderEvidence(evidence) {
    refs.evidenceList.replaceChildren();
    if (!evidence.length) {
      const empty = element("li", "evidence-item");
      empty.append(element("p", "evidence-text", "暂无可展示的上下文证据"));
      refs.evidenceList.append(empty);
      return;
    }

    evidence.forEach((entry) => {
      const item = element("li", "evidence-item");
      const meta = element("div", "evidence-meta");
      meta.append(
        element("span", "", String(entry.time || entry.observed_at || entry.created_at || "--")),
        element("span", "", `用户 · ${entry.sender || entry.sender_name || entry.sender_id || "未知"}`),
        element("span", "", `（${entry.source || entry.source_type || "消息"}）`),
      );
      item.append(meta, element("p", "evidence-text", String(
        entry.text || entry.source_text || entry.context || entry.excerpt || "",
      )));
      refs.evidenceList.append(item);
    });
  }

  function renderDetail() {
    const detail = state.selectedDetail;
    refs.detailEmpty.hidden = Boolean(detail);
    refs.detailContent.hidden = !detail;
    if (!detail) return;

    const meta = STATUS_META[detail.status];
    refs.detailTerm.textContent = detail.term || "--";
    refs.detailStatus.textContent = meta.label;
    refs.detailStatus.className = `status-badge ${meta.className}`;
    refs.detailMeaning.textContent = detail.meaning || "含义待推断";
    refs.meaningInput.value = detail.meaning || "";
    refs.meaningEditor.hidden = true;
    refs.detailMeaning.hidden = false;
    refs.detailConfidence.textContent = formatConfidence(detail.confidence);
    refs.confidenceFill.style.width = `${normalizeConfidence(detail.confidence)}%`;
    refs.detailScope.textContent = detail.scopeLabel || "--";
    refs.detailOccurrences.textContent = String(detail.occurrences ?? 0);
    refs.detailFirstSeen.textContent = detail.firstSeen || "--";
    refs.detailLastSeen.textContent = detail.lastSeen || "--";
    refs.editMeaningButton.disabled = state.actionPending;
    refs.saveMeaningButton.disabled = state.actionPending;
    refs.confirmButton.disabled = state.actionPending || detail.status === "confirmed";
    refs.rejectButton.disabled = state.actionPending || detail.status === "rejected";
    refs.confirmButton.querySelector("span").textContent = detail.status === "confirmed" ? "已确认" : "确认释义";
    refs.rejectButton.querySelector("span").textContent = detail.status === "rejected" ? "已拒绝" : "拒绝";
    renderEvidence(detail.evidence || []);
  }

  async function loadJargonDetail(id, options) {
    const requestId = ++state.detailRequestId;
    state.loadingDetail = true;
    refs.detailEmpty.hidden = false;
    refs.detailEmpty.querySelector("span").textContent = "正在读取证据…";
    refs.detailContent.hidden = true;
    try {
      const payload = await api.getJargonDetail(id);
      if (requestId !== state.detailRequestId || String(id) !== String(state.selectedId)) return;
      state.selectedDetail = normalizeJargon(payload);
      renderDetail();
      if (options && options.edit) openMeaningEditor();
    } catch (error) {
      if (requestId !== state.detailRequestId) return;
      state.selectedDetail = null;
      renderDetail();
      refs.detailEmpty.querySelector("span").textContent = error.message || "详情加载失败";
      showToast(error.message || "详情加载失败", "error");
    } finally {
      if (requestId === state.detailRequestId) state.loadingDetail = false;
    }
  }

  function renderProtocolLogs() {
    refs.protocolRows.replaceChildren();
    if (!state.protocolLogs.length) {
      setProtocolState("暂无协议日志");
      return;
    }
    setProtocolState("");
    state.protocolLogs.slice(0, 4).forEach((entry) => {
      const row = element("tr");
      row.append(createCell(entry.created_at || entry.time || "--"));
      row.append(createCell(entry.type || entry.action || "--"));
      const statusCell = element("td");
      const status = String(entry.status || "").toLowerCase();
      const succeeded = entry.success === true || entry.success === 1 || status === "success";
      const blocked = !succeeded;
      statusCell.append(element("span", `protocol-status${blocked ? " blocked" : ""}`, blocked ? "拦截" : "成功"));
      row.append(statusCell);
      const detail = entry.detail || entry.failure_detail || entry.reason || entry.failure_code || "--";
      row.append(createCell(detail, detail === "--" ? "" : detail));
      refs.protocolRows.append(row);
    });
  }

  async function loadProtocolLogs() {
    setProtocolState("正在读取协议日志…");
    try {
      const payload = await api.getProtocolLogs({ page: 1, page_size: 4 });
      state.protocolLogs = normalizeCollection(payload).items;
      renderProtocolLogs();
    } catch (error) {
      state.protocolLogs = [];
      renderProtocolLogs();
      setProtocolState(error.message || "协议日志加载失败");
    }
  }

  function openMeaningEditor() {
    if (!state.selectedDetail) return;
    refs.meaningInput.value = state.selectedDetail.meaning || "";
    refs.detailMeaning.hidden = true;
    refs.meaningEditor.hidden = false;
    refs.meaningInput.focus();
    refs.meaningInput.setSelectionRange(refs.meaningInput.value.length, refs.meaningInput.value.length);
  }

  function closeMeaningEditor() {
    refs.meaningEditor.hidden = true;
    refs.detailMeaning.hidden = false;
  }

  async function performJargonAction(action, meaning) {
    if (!state.selectedDetail || state.actionPending) return;
    state.actionPending = true;
    renderDetail();
    try {
      const body = { action, id: state.selectedDetail.id };
      if (meaning !== undefined) body.meaning = meaning;
      const updated = await api.jargonAction(body);
      if (updated && typeof updated === "object" && updated.id !== undefined) {
        state.selectedDetail = normalizeJargon(updated);
      } else {
        if (action === "confirm") state.selectedDetail.status = "confirmed";
        if (action === "reject") state.selectedDetail.status = "rejected";
        if (action === "update_meaning") state.selectedDetail.meaning = meaning;
      }
      closeMeaningEditor();
      renderDetail();
      const message = action === "confirm" ? "释义已确认" : action === "reject" ? "词条已拒绝" : "释义已更新";
      showToast(message, "success");
      await loadJargons({ refreshDetail: true });
    } catch (error) {
      showToast(error.message || "操作失败", "error");
    } finally {
      state.actionPending = false;
      renderDetail();
    }
  }

  function exportVisibleJargons() {
    if (!state.jargons.length) {
      showToast("当前没有可导出的词条", "error");
      return;
    }
    const payload = state.jargons.map((item) => ({
      id: item.id,
      status: item.status,
      term: item.term,
      meaning: item.meaning,
      scope_id: item.scopeId,
      scope_type: item.scopeType,
      confidence: item.confidence / 100,
      occurrence_count: item.occurrences,
      last_seen: item.lastSeen,
    }));
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "humanize-jargons.json";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    showToast(`已导出当前 ${payload.length} 条词条`, "success");
  }

  function scheduleSearch(value) {
    state.search = value.trim();
    refs.globalSearch.value = value;
    refs.tableSearch.value = value;
    global.clearTimeout(searchTimer);
    searchTimer = global.setTimeout(() => {
      state.page = 1;
      loadJargons();
    }, 260);
  }

  function bindEvents() {
    refs.globalSearch.addEventListener("input", (event) => scheduleSearch(event.target.value));
    refs.tableSearch.addEventListener("input", (event) => scheduleSearch(event.target.value));
    refs.statusFilter.addEventListener("change", (event) => {
      state.status = event.target.value;
      state.page = 1;
      loadJargons();
    });
    refs.scopeFilter.addEventListener("change", (event) => {
      const scope = parseScopeKey(event.target.value);
      state.scopeType = scope.type;
      state.scopeId = scope.id;
      refs.scopePicker.value = event.target.value;
      state.page = 1;
      loadJargons();
    });
    refs.scopePicker.addEventListener("change", (event) => {
      const scope = parseScopeKey(event.target.value);
      state.scopeType = scope.type;
      state.scopeId = scope.id;
      refs.scopeFilter.value = event.target.value;
      state.page = 1;
      loadJargons();
    });
    refs.pageSize.addEventListener("change", (event) => {
      state.pageSize = Math.max(1, Number(event.target.value) || 10);
      state.page = 1;
      loadJargons();
    });
    refs.exportButton.addEventListener("click", exportVisibleJargons);
    refs.editMeaningButton.addEventListener("click", openMeaningEditor);
    refs.cancelMeaningButton.addEventListener("click", closeMeaningEditor);
    refs.saveMeaningButton.addEventListener("click", () => {
      const meaning = refs.meaningInput.value.trim();
      if (!meaning) {
        showToast("释义不能为空", "error");
        refs.meaningInput.focus();
        return;
      }
      performJargonAction("update_meaning", meaning);
    });
    refs.confirmButton.addEventListener("click", () => performJargonAction("confirm", state.selectedDetail && state.selectedDetail.meaning));
    refs.rejectButton.addEventListener("click", () => performJargonAction("reject"));
    refs.ruleDetailsButton.addEventListener("click", () => showToast("完整规则可在“规则与人格”阶段管理", "info"));
    refs.learningToggle.addEventListener("change", () => {
      if (api.demoMode) {
        showToast(refs.learningToggle.checked ? "已恢复演示学习状态" : "已暂停演示学习状态", "info");
        return;
      }
      refs.learningToggle.checked = (state.settings.learning_enabled ?? state.settings.jargon_enabled) !== false;
      showToast("学习状态请在插件配置中修改", "info");
    });
    document.querySelectorAll("[data-nav]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.nav === "jargons") return;
        const label = button.querySelector("span");
        showToast(`${label ? label.textContent : "该视图"}将在后续阶段开放`, "info");
      });
    });
  }

  function collectRefs() {
    [
      "scopePicker", "globalSearch", "exportButton", "learningToggle", "metricLearned",
      "metricPending", "metricProtocol", "metricBlocked", "tableSearch", "statusFilter",
      "scopeFilter", "pageSize", "jargonRows", "tableState", "paginationSummary", "pagination",
      "detailEmpty", "detailContent", "detailTerm", "detailStatus", "editMeaningButton",
      "detailMeaning", "meaningEditor", "meaningInput", "cancelMeaningButton", "saveMeaningButton",
      "detailConfidence", "confidenceFill", "detailScope", "detailOccurrences", "detailFirstSeen",
      "detailLastSeen", "evidenceList", "confirmButton", "rejectButton", "chartSuccessRate",
      "protocolChart", "chartLabels", "protocolRows", "protocolState", "ruleEnabled",
      "ruleInjectionMode", "ruleAdministrator", "ruleMessageLimit", "ruleMessageCount", "ruleDetailsButton", "toastRegion",
    ].forEach((id) => {
      refs[id] = document.getElementById(id);
    });
    refs.ruleDot = document.querySelector(".rule-dot");
  }

  async function boot() {
    collectRefs();
    bindEvents();
    refreshIcons();
    setTableState("正在连接插件…");
    setProtocolState("正在连接插件…");

    try {
      await api.ready();
      const [settings, overview] = await Promise.all([api.getSettings(), api.getOverview()]);
      state.settings = settings && typeof settings === "object" ? settings : {};
      state.overview = overview && typeof overview === "object" ? overview : {};
      renderScopes();
      renderSettings();
      renderMetrics();
      await Promise.all([loadJargons(), loadProtocolLogs()]);
      if (api.demoMode) showToast("已载入演示数据", "info");
    } catch (error) {
      setTableState(error.message || "插件连接失败");
      setProtocolState(error.message || "插件连接失败");
      showToast(error.message || "插件连接失败", "error");
    }
  }

  document.addEventListener("DOMContentLoaded", boot, { once: true });
})(window, document);
