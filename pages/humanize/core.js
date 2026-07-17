(function initializeHumanizeCore(global) {
  "use strict";

  /**
   * Create a DOM element with optional class and text.
   * @param {string} tag Element tag name.
   * @param {string} [className] Optional class names.
   * @param {string} [text] Optional text content (set via textContent).
   * @returns {HTMLElement} The created element.
   */
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  /**
   * Create a lucide icon placeholder element. lucide.createIcons() will replace it.
   * @param {string} name Icon name in kebab-case (e.g. "book-open").
   * @returns {HTMLElement} <i data-lucide="name">
   */
  function icon(name) {
    const node = document.createElement("i");
    node.setAttribute("data-lucide", name);
    return node;
  }

  /** Refresh lucide icons in the document. */
  function refreshIcons() {
    if (global.lucide && typeof global.lucide.createIcons === "function") {
      global.lucide.createIcons();
    }
  }

  /**
   * Parse a value into a finite number, falling back when invalid.
   * @param {*} value Raw value.
   * @param {number} fallback Default when not finite.
   * @returns {number} Parsed number or fallback.
   */
  function numberValue(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  /**
   * Parse a value into a boolean, accepting common truthy strings.
   * @param {*} value Raw value.
   * @param {boolean} fallback Default when value is empty.
   * @returns {boolean} Parsed boolean.
   */
  function booleanValue(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    const text = String(value).trim().toLowerCase();
    if (["true", "1", "yes", "on", "enabled", "active"].includes(text)) return true;
    if (["false", "0", "no", "off", "disabled", "inactive"].includes(text)) return false;
    return fallback;
  }

  /**
   * Format an ISO timestamp into zh-CN local time string.
   * @param {string|number|Date} value Timestamp.
   * @returns {string} Formatted time or "--".
   */
  function formatTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  /**
   * Format an ISO timestamp into zh-CN date only.
   * @param {string|number|Date} value Timestamp.
   * @returns {string} Formatted date or "--".
   */
  function formatDate(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "--";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /**
   * Format a 0-1 score into percentage string.
   * @param {number} value Score between 0 and 1.
   * @returns {string} "85%" or "--".
   */
  function formatScore(value) {
    const num = numberValue(value, NaN);
    if (!Number.isFinite(num)) return "--";
    return `${Math.round(num * 100)}%`;
  }

  /**
   * Format a 0-100 rate into percentage string with one decimal.
   * @param {number} value Rate between 0 and 100.
   * @returns {string} "85.0%" or "--".
   */
  function formatRate(value) {
    const num = numberValue(value, NaN);
    if (!Number.isFinite(num)) return "--";
    return `${num.toFixed(1)}%`;
  }

  /**
   * Format a confidence value (0-1) into descriptive label.
   * @param {number} value Confidence.
   * @returns {string} "0.85" or "--".
   */
  function formatConfidence(value) {
    const num = numberValue(value, NaN);
    if (!Number.isFinite(num)) return "--";
    return num.toFixed(2);
  }

  /**
   * Build a human-readable scope label from type and id.
   * @param {string} scopeType Scope type (group/private/global...).
   * @param {string} scopeId Scope identifier.
   * @returns {string} "群组 123" or "全部作用域".
   */
  function formatScopeLabel(scopeType, scopeId) {
    const typeText = {
      global: "全局",
      group: "群组",
      private: "私聊",
      channel: "频道",
      chat: "会话",
      group_member: "群成员",
    }[String(scopeType || "").toLowerCase()] || scopeType || "";
    if (!typeText) return "全部作用域";
    if (!scopeId) return typeText;
    return `${typeText} ${scopeId}`;
  }

  /**
   * Build a stable composite key for jargon scope (type:id).
   * @param {string} scopeType
   * @param {string} scopeId
   * @returns {string} Composite key string.
   */
  function makeScopeKey(scopeType, scopeId) {
    return `${scopeType || ""}:${scopeId || ""}`;
  }

  /**
   * Parse a jargon scope key back into type and id.
   * @param {string} key Composite key.
   * @returns {{scopeType: string, scopeId: string}}
   */
  function parseScopeKey(key) {
    const text = String(key || "");
    const idx = text.indexOf(":");
    if (idx < 0) return { scopeType: "", scopeId: "" };
    return { scopeType: text.slice(0, idx), scopeId: text.slice(idx + 1) };
  }

  /**
   * Encode a scope option (memory/examples) into a single select value.
   * Uses encodeURIComponent to tolerate special chars in tokens.
   * @param {{type: string, token: string}} scope
   * @returns {string} Encoded "type:token".
   */
  function scopeOptionValue(scope) {
    if (!scope) return "";
    const type = encodeURIComponent(String(scope.type || ""));
    const token = encodeURIComponent(String(scope.token || ""));
    return `${type}:${token}`;
  }

  /**
   * Decode a select value back into scope type and token.
   * @param {string} value Encoded "type:token".
   * @returns {{type: string, token: string}}
   */
  function parseScopeSelection(value) {
    const text = String(value || "");
    const idx = text.indexOf(":");
    if (idx < 0) return { type: "", token: "" };
    try {
      return {
        type: decodeURIComponent(text.slice(0, idx)),
        token: decodeURIComponent(text.slice(idx + 1)),
      };
    } catch {
      return { type: text.slice(0, idx), token: text.slice(idx + 1) };
    }
  }

  /**
   * Fill a select element with scope options from memory-overview.
   * @param {HTMLSelectElement} select Target select.
   * @param {Array} options Scope options [{type, token, label?}].
   * @param {string} [current] Currently selected value.
   * @param {string} [allLabel] Label for the empty option.
   */
  function fillScopeSelect(select, options, current, allLabel) {
    if (!select) return;
    const value = current || select.value;
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = allLabel || "全部作用域";
    select.append(empty);
    (options || []).forEach((scope) => {
      if (!scope || !scope.type) return;
      const opt = document.createElement("option");
      opt.value = scopeOptionValue(scope);
      opt.textContent = scope.label || formatScopeLabel(scope.type, scope.token || scope.scope_hash || scope.subject_hash || "");
      select.append(opt);
    });
    select.value = value;
  }

  /**
   * Normalize a payload that may be {items, total, page} or a bare array.
   * @param {*} payload Raw response.
   * @param {string[]} [altKeys] Alternate keys to try for the items list.
   * @returns {{items: Array, total: number, page: number, pageSize: number}}
   */
  function normalizeCollection(payload, altKeys) {
    if (Array.isArray(payload)) {
      return { items: payload, total: payload.length, page: 1, pageSize: payload.length };
    }
    if (payload && typeof payload === "object") {
      const keys = ["items", ...(altKeys || [])];
      let items = [];
      for (const key of keys) {
        if (Array.isArray(payload[key])) {
          items = payload[key];
          break;
        }
      }
      return {
        items,
        total: numberValue(payload.total, items.length),
        page: numberValue(payload.page, 1),
        pageSize: numberValue(payload.page_size || payload.pageSize, items.length),
      };
    }
    return { items: [], total: 0, page: 1, pageSize: 0 };
  }

  /**
   * Serialize any value into a string for trace display.
   * @param {*} value
   * @returns {string}
   */
  function serializeTraceContent(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  /**
   * Detect the display format for a trace value.
   * @param {*} value
   * @param {string} [hint] Optional hint ("json"|"markdown"|"code"|"plain").
   * @returns {"json"|"markdown"|"code"|"plain"}
   */
  function detectTraceFormat(value, hint) {
    if (hint && hint !== "auto") return hint;
    if (typeof value === "object" && value !== null) return "json";
    const text = String(value || "");
    const trimmed = text.trim();
    if (!trimmed) return "plain";
    if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
      try {
        JSON.parse(trimmed);
        return "json";
      } catch (err) {
        // fall through
      }
    }
    if (/^<\w/.test(trimmed)) return "code";
    if (/^#{1,6}\s|^\*\s|^\d+\.\s/m.test(trimmed)) return "markdown";
    return "plain";
  }

  /**
   * Copy text to clipboard with fallback for non-secure contexts.
   * @param {string} text
   * @returns {Promise<boolean>}
   */
  async function copyText(text) {
    const value = String(text || "");
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (err) {
        // fall through to fallback
      }
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.append(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch (err) {
      return false;
    }
  }

  /**
   * Debounce a function call by a delay.
   * @param {Function} fn
   * @param {number} ms
   * @returns {Function} Debounced function with .cancel().
   */
  function debounce(fn, ms) {
    let timer = null;
    const debounced = function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        fn.apply(this, args);
      }, ms);
    };
    debounced.cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };
    return debounced;
  }

  /**
   * Create a request-id guard to drop stale async responses.
   * @returns {{current: () => number, bump: () => number, isStale: (n: number) => boolean}}
   */
  function requestIdGuard() {
    let counter = 0;
    return {
      current: () => counter,
      bump: () => { counter += 1; return counter; },
      isStale: (n) => n !== counter,
    };
  }

  /**
   * Escape a string for safe inclusion in HTML (used only for static SVG fragments).
   * Prefer textContent for all dynamic content; this is for trace fallback only.
   * @param {string} text
   * @returns {string}
   */
  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Truncate a long identifier for display (e.g. SHA-256 memory id).
   * @param {string} id
   * @param {number} [head=8] Head length.
   * @param {number} [tail=0] Tail length, 0 to omit.
   * @returns {string}
   */
  function truncateId(id, head, tail) {
    const text = String(id || "");
    const headLen = head === undefined ? 8 : head;
    const tailLen = tail || 0;
    if (text.length <= headLen + tailLen + 1) return text;
    if (tailLen > 0) return `${text.slice(0, headLen)}…${text.slice(-tailLen)}`;
    return `${text.slice(0, headLen)}…`;
  }

  global.HumanizeCore = Object.freeze({
    element,
    icon,
    refreshIcons,
    numberValue,
    booleanValue,
    formatTime,
    formatDate,
    formatScore,
    formatRate,
    formatConfidence,
    formatScopeLabel,
    makeScopeKey,
    parseScopeKey,
    scopeOptionValue,
    parseScopeSelection,
    fillScopeSelect,
    normalizeCollection,
    serializeTraceContent,
    detectTraceFormat,
    copyText,
    debounce,
    requestIdGuard,
    escapeHtml,
    truncateId,
  });
})(window);
