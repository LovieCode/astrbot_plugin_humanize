(function initializeHumanizeApi(global) {
  "use strict";

  const BRIDGE_TIMEOUT_MS = 5000;

  function normalizeEndpoint(endpoint) {
    const value = String(endpoint || "").trim();
    return value.startsWith("/") ? value : `/${value}`;
  }

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

  async function get(endpoint, params) {
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiGet(normalizeEndpoint(endpoint), params || {}));
  }

  async function post(endpoint, body) {
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiPost(normalizeEndpoint(endpoint), body || {}));
  }

  global.HumanizeApi = Object.freeze({
    ready: waitForBridge,
    getOverview: () => get("overview"),
    getSettings: () => get("settings"),
    getJargons: (params) => get("jargons", params),
    getJargonDetail: (id) => get("jargon-detail", { id }),
    getProtocolLogs: (params) => get("protocol-logs", params),
    getContextRuns: (params) => get("context-runs", params),
    getContextRun: (id) => get("context-run", { id }),
    getContextStats: () => get("context-stats"),
    getProviderCacheCapabilities: () => get("provider-cache-capabilities"),
    getMemoryOverview: () => get("memory-overview"),
    getMemoryAgentOptions: () => get("memory-agent-options"),
    getMemories: (params) => get("memories", params),
    getMemoryDetail: (id) => get("memory-detail", { id }),
    getMemoryJobs: (params) => get("memory-jobs", params),
    memoryAction: (body) => post("memory-action", body),
    debugMemoryRecall: (body) => post("memory-recall-debug", body),
    getReplyExamples: (params) => get("reply-examples", params),
    getReplyExampleDetail: (id) => get("reply-example-detail", { id }),
    replyExampleAction: (body) => post("reply-example-action", body),
    debugReplyExamples: (body) => post("reply-example-recall-debug", body),
    getChatProviders: () => get("chat-providers"),
    getPromptTemplates: () => get("prompt-templates"),
    exportJargons: (params) => get("jargon-export", params),
    jargonAction: (body) => post("jargon-action", body),
    savePromptTemplate: (body) => post("prompt-templates", body),
  });
})(window);
