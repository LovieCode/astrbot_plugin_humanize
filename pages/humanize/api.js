(function initializeHumanizeApi(global) {
  "use strict";

  /** AstrBot 桥接 SDK 就绪最长等待时间，超时抛错避免页面卡死。 */
  const BRIDGE_TIMEOUT_MS = 5000;

  /**
   * Normalize endpoint path so apiGet/apiPost can locate the plugin route.
   * @param {string} endpoint Raw endpoint, may omit leading slash.
   * @returns {string} Endpoint guaranteed to start with "/".
   */
  function normalizeEndpoint(endpoint) {
    const value = String(endpoint || "").trim();
    return value.startsWith("/") ? value : `/${value}`;
  }

  /**
   * Unwrap plugin response envelope into raw data, throwing on error shapes.
   * @param {object} payload Response payload from AstrBotPluginPage bridge.
   * @returns {*} Unwrapped data field or the original payload.
   * @throws {Error} When payload reports failure via success=false or status=error.
   */
  function unwrap(payload) {
    if (payload && typeof payload === "object" && payload.success === false) {
      throw new Error(payload.message || "请求失败");
    }
    if (payload && typeof payload === "object" && payload.status === "error") {
      throw new Error(payload.message || "请求失败");
    }
    if (payload && typeof payload === "object" && Object.prototype.hasOwnProperty.call(payload, "data")) {
      return payload.data;
    }
    return payload;
  }

  /**
   * Wait for AstrBotPluginPage bridge to be ready before issuing requests.
   * @returns {Promise<object>} The ready bridge object.
   * @throws {Error} When bridge does not appear within BRIDGE_TIMEOUT_MS.
   */
  async function waitForBridge() {
    const startedAt = Date.now();
    while (!global.AstrBotPluginPage) {
      if (Date.now() - startedAt >= BRIDGE_TIMEOUT_MS) {
        throw new Error("AstrBotPluginPage SDK 未就绪");
      }
      await new Promise((resolve) => global.setTimeout(resolve, 50));
    }
    await global.AstrBotPluginPage.ready();
    return global.AstrBotPluginPage;
  }

  /**
   * Issue a GET request via the bridge and unwrap the response.
   * @param {string} endpoint Plugin sub-path, e.g. "overview".
   * @param {object} [params] Query parameters.
   * @returns {Promise<*>} Unwrapped response data.
   */
  async function get(endpoint, params) {
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiGet(normalizeEndpoint(endpoint), params || {}));
  }

  /**
   * Issue a POST request via the bridge and unwrap the response.
   * @param {string} endpoint Plugin sub-path, e.g. "jargon-action".
   * @param {object} [body] JSON body.
   * @returns {Promise<*>} Unwrapped response data.
   */
  async function post(endpoint, body) {
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiPost(normalizeEndpoint(endpoint), body || {}));
  }

  global.HumanizeApi = Object.freeze({
    /** Wait for the bridge to become ready. */
    ready: waitForBridge,

    // 总览与设置
    getOverview: () => get("overview"),
    getSettings: () => get("settings"),

    // 黑话词库
    getJargons: (params) => get("jargons", params),
    getJargonDetail: (id) => get("jargon-detail", { id }),
    exportJargons: (params) => get("jargon-export", params),
    jargonAction: (body) => post("jargon-action", body),

    // 协议监控
    getProtocolLogs: (params) => get("protocol-logs", params),

    // 上下文追踪
    getContextRuns: (params) => get("context-runs", params),
    getContextRun: (id) => get("context-run", { id }),
    getContextStats: (params) => get("context-stats", params),

    // Provider 观测
    getProviderCacheCapabilities: () => get("provider-cache-capabilities"),
    getChatProviders: () => get("chat-providers"),
    getMemoryProviders: () => get("memory-providers"),

    // 长期记忆 - 状态
    getMemoryStatus: () => get("memory-status"),
    getMemoryOverview: () => get("memory-overview"),
    getMemoryAgentOptions: () => get("memory-agent-options"),

    // 长期记忆 - 列表与详情
    getMemories: (params) => get("memories", params),
    getMemoryDetail: (id) => get("memory-detail", { id }),
    getMemoryJobs: (params) => get("memory-jobs", params),
    memoryAction: (body) => post("memory-action", body),
    debugMemoryRecall: (body) => post("memory-recall-debug", body),

    // 回复样例
    getReplyExamples: (params) => get("reply-examples", params),
    getReplyExampleDetail: (id) => get("reply-example-detail", { id }),
    replyExampleAction: (body) => post("reply-example-action", body),
    debugReplyExamples: (body) => post("reply-example-recall-debug", body),

    // 提示词模板
    getPromptTemplates: () => get("prompt-templates"),
    /** Update or reset prompt templates. action: "update" | "save" | "reset". */
    savePromptTemplate: (body) => post("prompt-templates", body),
  });
})(window);