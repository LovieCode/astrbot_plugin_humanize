/**
 * 共享 API 请求层。
 * 挂载到 HZ.api。依赖：无（纯 fetch）。
 * 后端约定（humanize/web/routes.py）：
 *   - GET/POST /api/v1/plugins/extensions/astrbot_plugin_humanize/<path>
 *   - 成功：{ success: true, data: ... }（HTTP 2xx）
 *   - 失败：{ status: "error", message: "...", data: ... }（HTTP 非 2xx）
 */
(function (global) {
  const DEFAULT_BASE = "/api/v1/plugins/extensions/astrbot_plugin_humanize/";

  /* ---------- 基础请求 ---------- */
  /**
   * 发起请求并返回后端 data。
   * @param {string} path 相对路径，如 "jargons"
   * @param {object} [opts] { method, query, body }
   * @returns {Promise<any>} 后端 {success,data} 中的 data
   * @throws {Error} 错误对象带中文 message、status、retryable
   */
  async function request(path, opts = {}) {
    const method = (opts.method || (opts.body !== undefined ? "POST" : "GET")).toUpperCase();
    const url = new URL(HZ.api.baseUrl + String(path).replace(/^\/+/, ""), location.origin);
    if (opts.query) {
      Object.entries(opts.query).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
      });
    }
    const init = { method, credentials: "same-origin", headers: {} };
    if (opts.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      const err = new Error("网络请求失败，请检查连接");
      err.status = 0;
      err.retryable = true;
      throw err;
    }
    let payload = null;
    try {
      payload = await res.json();
    } catch (e) {
      payload = null;
    }
    if (!res.ok || (payload && payload.success === false)) {
      const message =
        (payload && (payload.message || payload.error)) ||
        (res.status === 404 ? "接口不存在" : res.status >= 500 ? "服务内部错误" : "请求失败");
      const err = new Error(String(message));
      err.status = res.status;
      err.retryable = res.status === 0 || res.status >= 500 || res.status === 429;
      throw err;
    }
    return payload && payload.success === true ? payload.data : payload;
  }

  /**
   * GET 便捷方法。
   * @param {string} path 相对路径
   * @param {object} [query] 查询参数对象
   * @returns {Promise<any>} 后端 data
   */
  function get(path, query) {
    return request(path, { method: "GET", query });
  }

  /**
   * POST 便捷方法。
   * @param {string} path 相对路径
   * @param {object} [body] JSON 请求体
   * @returns {Promise<any>} 后端 data
   */
  function post(path, body) {
    return request(path, { method: "POST", body });
  }

  /* ---------- 错误归一 ---------- */
  /**
   * 归一化任意错误，供页面展示错误条。
   * @param {*} e 捕获的异常
   * @returns {{message: string, status: number, retryable: boolean}}
   */
  function errorOf(e) {
    if (e && typeof e === "object" && typeof e.message === "string" && "status" in e) {
      return { message: e.message, status: e.status || 0, retryable: !!e.retryable };
    }
    return { message: String((e && e.message) || e || "未知错误"), status: 0, retryable: true };
  }

  /* ---------- 分页 ---------- */
  /**
   * 生成后端分页参数（page_size 上限 100，默认 20）。
   * @param {{page?: number, pageSize?: number}} [p]
   * @returns {{page: number, page_size: number}}
   */
  function pageParams(p = {}) {
    const page = Math.max(1, parseInt(p.page, 10) || 1);
    const pageSize = Math.min(100, Math.max(1, parseInt(p.pageSize, 10) || 20));
    return { page, page_size: pageSize };
  }

  /* ---------- 作用域 ---------- */
  const SCOPE_LABELS = {
    global: "全局",
    group: "群聊",
    private_user: "私聊用户",
    group_member: "群成员",
  };

  /**
   * 生成作用域查询参数（空值自动剔除）。
   * @param {{scopeType?: string, scopeToken?: string}} [s]
   * @returns {{scope_type?: string, scope_token?: string}}
   */
  function scopeFilter(s = {}) {
    const out = {};
    if (s.scopeType) out.scope_type = s.scopeType;
    if (s.scopeToken) out.scope_token = s.scopeToken;
    return out;
  }

  /**
   * 作用域中文标签。
   * @param {string} type 作用域类型
   * @param {string} [hash] 作用域哈希（可省略）
   * @returns {string} 如 "群聊 · 1a2b3c4d"
   */
  function scopeLabel(type, hash) {
    const label = SCOPE_LABELS[type] || type || "";
    return hash ? `${label} · ${String(hash).slice(0, 8)}` : label;
  }

  /* ---------- 时间格式化 ---------- */
  function toDate(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  /**
   * 本地时间 "MM-DD HH:mm"。
   * @param {string} iso UTC ISO 字符串
   * @returns {string}
   */
  function time(iso) {
    const d = toDate(iso);
    if (!d) return String(iso || "");
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  /**
   * 相对时间："刚刚 / x 分钟前 / x 小时前 / x 天前"。
   * @param {string} iso UTC ISO 字符串
   * @returns {string}
   */
  function ago(iso) {
    const d = toDate(iso);
    if (!d) return String(iso || "");
    const diff = Math.max(0, Date.now() - d.getTime());
    const min = Math.floor(diff / 60000);
    if (min < 1) return "刚刚";
    if (min < 60) return `${min} 分钟前`;
    const hours = Math.floor(min / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  }

  /* ---------- 导出 ---------- */
  global.HZ = global.HZ || {};
  global.HZ.api = { baseUrl: DEFAULT_BASE, request, get, post, errorOf, pageParams, scopeFilter, scopeLabel, time, ago };
})(window);
