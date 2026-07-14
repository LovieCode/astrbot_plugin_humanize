(function initializeHumanizeApi(global) {
  "use strict";

  const DEMO_MODE = new URLSearchParams(global.location.search).get("demo") === "1";
  const BRIDGE_TIMEOUT_MS = 5000;

  const demoScopes = [
    { id: "qq-group-cyber-tea", type: "group", label: "QQ群 · 赛博茶话会" },
    { id: "qq-group-astrbot", type: "group", label: "QQ群 · AstrBot 交流" },
    { id: "qq-friend-alpha", type: "private", label: "QQ · Alpha" },
  ];

  const demoJargons = [
    {
      id: 101,
      status: "candidate",
      term: "开香槟",
      meaning: "提前庆祝成功",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.72,
      occurrence_count: 3,
      first_seen: "2025-05-23 22:18",
      last_seen: "2025-05-24 21:13",
      evidence: [
        { id: 1, time: "2025-05-24 21:13:08", sender: "Alpha", source: "群消息", text: "项目上线了！开香槟 🎉" },
        { id: 2, time: "2025-05-23 22:18:47", sender: "Beta", source: "群消息", text: "这个脚本终于搞定了，开香槟！" },
      ],
    },
    {
      id: 102,
      status: "confirmed",
      term: "上桌",
      meaning: "获得参与资格",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.94,
      occurrence_count: 7,
      first_seen: "2025-05-20 19:32",
      last_seen: "2025-05-24 20:51",
      evidence: [
        { id: 3, time: "2025-05-24 20:51:10", sender: "Mori", source: "群消息", text: "这次测试服终于能上桌了。" },
        { id: 4, time: "2025-05-22 18:06:31", sender: "Gamma", source: "群消息", text: "报名过了就算上桌。" },
      ],
    },
    {
      id: 103,
      status: "ambiguous",
      term: "红温",
      meaning: "情绪激动或恼怒",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.61,
      occurrence_count: 5,
      first_seen: "2025-05-19 12:44",
      last_seen: "2025-05-24 19:47",
      evidence: [
        { id: 5, time: "2025-05-24 19:47:02", sender: "Delta", source: "群消息", text: "再排一次这个 bug 我真要红温了。" },
        { id: 6, time: "2025-05-21 23:05:44", sender: "Alpha", source: "群消息", text: "打到最后一把已经红温。" },
      ],
    },
    {
      id: 104,
      status: "confirmed",
      term: "吃瓜",
      meaning: "围观并关注事态发展",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.88,
      occurrence_count: 12,
      first_seen: "2025-05-12 09:21",
      last_seen: "2025-05-24 18:30",
      evidence: [{ id: 7, time: "2025-05-24 18:30:22", sender: "Beta", source: "群消息", text: "先吃瓜，等后续。" }],
    },
    {
      id: 105,
      status: "candidate",
      term: "烧起来了",
      meaning: "话题或情绪开始激烈",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.69,
      occurrence_count: 4,
      first_seen: "2025-05-22 10:02",
      last_seen: "2025-05-24 17:58",
      evidence: [{ id: 8, time: "2025-05-24 17:58:08", sender: "Gamma", source: "群消息", text: "评论区已经烧起来了。" }],
    },
    {
      id: 106,
      status: "confirmed",
      term: "私聊",
      meaning: "转为私下交流",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.97,
      occurrence_count: 15,
      first_seen: "2025-05-08 21:10",
      last_seen: "2025-05-24 17:22",
      evidence: [{ id: 9, time: "2025-05-24 17:22:34", sender: "Mori", source: "群消息", text: "细节私聊说。" }],
    },
    {
      id: 107,
      status: "ambiguous",
      term: "鲜掉住了",
      meaning: "哭笑不得或无语",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.58,
      occurrence_count: 6,
      first_seen: "2025-05-18 16:09",
      last_seen: "2025-05-24 16:41",
      evidence: [{ id: 10, time: "2025-05-24 16:41:19", sender: "Delta", source: "群消息", text: "看到这个提交我鲜掉住了。" }],
    },
    {
      id: 108,
      status: "candidate",
      term: "抬走",
      meaning: "结束当前话题或淘汰",
      scope_id: "qq-group-astrbot",
      scope_label: "AstrBot 交流",
      confidence: 0.67,
      occurrence_count: 3,
      first_seen: "2025-05-21 15:02",
      last_seen: "2025-05-24 15:54",
      evidence: [{ id: 11, time: "2025-05-24 15:54:07", sender: "Navi", source: "群消息", text: "这个方案不行，抬走下一个。" }],
    },
    {
      id: 109,
      status: "confirmed",
      term: "落地",
      meaning: "从方案进入实际实施",
      scope_id: "qq-group-astrbot",
      scope_label: "AstrBot 交流",
      confidence: 0.91,
      occurrence_count: 11,
      first_seen: "2025-05-10 11:31",
      last_seen: "2025-05-24 14:40",
      evidence: [{ id: 12, time: "2025-05-24 14:40:53", sender: "Iris", source: "群消息", text: "接口定了就开始落地。" }],
    },
    {
      id: 110,
      status: "rejected",
      term: "1234",
      meaning: "",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.18,
      occurrence_count: 2,
      first_seen: "2025-05-24 12:02",
      last_seen: "2025-05-24 12:05",
      evidence: [{ id: 13, time: "2025-05-24 12:05:11", sender: "Alpha", source: "群消息", text: "验证码 1234。" }],
    },
    {
      id: 111,
      status: "candidate",
      term: "蹲一个",
      meaning: "等待后续消息或结果",
      scope_id: "qq-friend-alpha",
      scope_label: "QQ · Alpha",
      confidence: 0.75,
      occurrence_count: 4,
      first_seen: "2025-05-21 09:42",
      last_seen: "2025-05-24 11:28",
      evidence: [{ id: 14, time: "2025-05-24 11:28:42", sender: "Alpha", source: "私聊", text: "蹲一个最终版本。" }],
    },
    {
      id: 112,
      status: "confirmed",
      term: "补票",
      meaning: "事后补充认可或参与",
      scope_id: "qq-group-cyber-tea",
      scope_label: "本群",
      confidence: 0.86,
      occurrence_count: 8,
      first_seen: "2025-05-13 20:12",
      last_seen: "2025-05-24 10:16",
      evidence: [{ id: 15, time: "2025-05-24 10:16:19", sender: "Beta", source: "群消息", text: "昨晚没看直播，今天来补票。" }],
    },
    {
      id: 113,
      status: "ambiguous",
      term: "对齐",
      meaning: "统一理解、目标或实现方式",
      scope_id: "qq-group-astrbot",
      scope_label: "AstrBot 交流",
      confidence: 0.64,
      occurrence_count: 5,
      first_seen: "2025-05-17 13:56",
      last_seen: "2025-05-23 23:49",
      evidence: [{ id: 16, time: "2025-05-23 23:49:02", sender: "Navi", source: "群消息", text: "先把预期对齐一下。" }],
    },
  ];

  const demoProtocolLogs = [
    { id: 1, created_at: "2025-05-24 21:14:02", type: "Reply success", status: "success", detail: "已注入 1 条回复" },
    { id: 2, created_at: "2025-05-24 21:12:11", type: "No Reply success", status: "success", detail: "未生成回复（按规则）" },
    { id: 3, created_at: "2025-05-24 21:11:03", type: "Reply blocked", status: "blocked", detail: "缺少 Action 控制头" },
    { id: 4, created_at: "2025-05-24 20:59:47", type: "Reply success", status: "success", detail: "已注入 2 条回复" },
  ];

  const demoSettings = {
    learning_enabled: true,
    current_scope_id: "qq-group-cyber-tea",
    current_scope_label: "QQ群 · 赛博茶话会",
    scopes: demoScopes,
    default_rule_enabled: true,
    protocol_injection_mode: "user",
    administrator_name: "管理员",
    max_message_chars: 10,
  };

  const demoFeatures = {
    persona: {
      name: "眠汐",
      identity: "AstrBot 的拟人化助手",
      traits: ["冷静", "直接", "有边界感"],
      values: ["诚实", "可靠", "尊重上下文"],
      boundaries: ["不冒充真人", "不泄露隐私"],
    },
    state: {
      mood: 0.68,
      energy: 0.74,
      interest: 0.82,
      stress: 0.2,
      focus: "当前对话",
      updated_at: "2026-07-14 19:42:00",
    },
    behavior: {
      enabled: true,
      allow_no_reply: true,
      allow_follow_up: true,
      allow_proactive: false,
      allow_end_topic: true,
      reply_threshold: 0.54,
      follow_up_threshold: 0.68,
      proactive_threshold: 0.86,
      end_topic_threshold: 0.76,
      cooldown_minutes: 8,
    },
    expression: {
      enabled: true,
      provider: "builtin",
      mode: "observe",
      profile: "日常对话保持自然简短，复杂任务允许完整展开。",
      integration_status: "ready",
      last_checked_at: "2026-07-14 19:42:00",
      last_error: "",
    },
    audit: [
      { time: "2026-07-14 19:42", section: "state", action: "自动更新", detail: "情绪与精力已刷新" },
      { time: "2026-07-14 18:26", section: "behavior", action: "保存", detail: "决策阈值已更新" },
    ],
  };

  function copy(value) {
    return JSON.parse(JSON.stringify(value));
  }

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

  function demoOverview() {
    return {
      learned_count: 128,
      pending_count: demoJargons.filter((item) => item.status === "candidate").length + 8,
      protocol_success_rate: 98.7,
      blocked_week: 9,
      protocol_trend: [
        { label: "05-18", value: 98.4 },
        { label: "05-19", value: 98.2 },
        { label: "05-20", value: 96.1 },
        { label: "05-21", value: 97.0 },
        { label: "05-22", value: 98.8 },
        { label: "05-23", value: 96.8 },
        { label: "05-24", value: 97.5 },
      ],
    };
  }

  function demoListJargons(params) {
    const search = String(params.search || "").trim().toLocaleLowerCase("zh-CN");
    const status = String(params.status || "");
    const scopeId = String(params.scope_id || "");
    const scopeType = String(params.scope_type || "");
    const page = Math.max(1, Number(params.page) || 1);
    const pageSize = Math.max(1, Number(params.page_size) || 10);
    const filtered = demoJargons.filter((item) => {
      const matchesSearch = !search || item.term.toLocaleLowerCase("zh-CN").includes(search)
        || item.meaning.toLocaleLowerCase("zh-CN").includes(search);
      const itemScopeType = item.scope_type || (item.scope_id.startsWith("qq-friend-") ? "private" : "group");
      return matchesSearch && (!status || item.status === status)
        && (!scopeId || item.scope_id === scopeId)
        && (!scopeType || itemScopeType === scopeType);
    });
    const offset = (page - 1) * pageSize;
    return {
      items: filtered.slice(offset, offset + pageSize).map((item) => {
        const row = copy(item);
        delete row.evidence;
        return row;
      }),
      total: filtered.length,
      page,
      page_size: pageSize,
    };
  }

  function demoJargonDetail(id) {
    const item = demoJargons.find((entry) => String(entry.id) === String(id));
    if (!item) {
      throw new Error("词条不存在");
    }
    return copy(item);
  }

  function demoJargonAction(body) {
    const item = demoJargons.find((entry) => String(entry.id) === String(body.id));
    if (!item) {
      throw new Error("词条不存在");
    }
    if (body.action === "confirm") {
      item.status = "confirmed";
      item.confidence = Math.max(item.confidence, 0.9);
    } else if (body.action === "reject") {
      item.status = "rejected";
    } else if (body.action === "update_meaning") {
      const meaning = String(body.meaning || "").trim();
      if (!meaning) {
        throw new Error("释义不能为空");
      }
      item.meaning = meaning;
    } else {
      throw new Error("未知操作");
    }
    return copy(item);
  }

  async function demoGet(endpoint, params) {
    await new Promise((resolve) => global.setTimeout(resolve, 90));
    if (endpoint === "/overview") return demoOverview();
    if (endpoint === "/jargons") return demoListJargons(params || {});
    if (endpoint === "/jargon-detail") return demoJargonDetail(params && params.id);
    if (endpoint === "/protocol-logs") {
      const page = Math.max(1, Number(params && params.page) || 1);
      const pageSize = Math.max(1, Number(params && params.page_size) || 10);
      const offset = (page - 1) * pageSize;
      return { items: copy(demoProtocolLogs.slice(offset, offset + pageSize)), total: demoProtocolLogs.length };
    }
    if (endpoint === "/settings") return copy(demoSettings);
    if (endpoint === "/features") return copy(demoFeatures);
    throw new Error(`未知演示接口：${endpoint}`);
  }

  async function demoPost(endpoint, body) {
    await new Promise((resolve) => global.setTimeout(resolve, 100));
    if (endpoint === "/jargon-action") return demoJargonAction(body || {});
    const section = endpoint.slice(1);
    if (["persona", "state", "behavior", "expression"].includes(section)) {
      demoFeatures[section] = { ...copy(demoFeatures[section]), ...copy(body || {}) };
      if (section === "state") demoFeatures.state.updated_at = new Date().toLocaleString("zh-CN");
      return copy(demoFeatures[section]);
    }
    if (endpoint === "/control/reset" || endpoint === "/control-reset") {
      const target = String((body && body.section) || "all");
      const defaults = {
        persona: { name: "", identity: "", traits: [], values: [], boundaries: [] },
        state: { mood: 0.5, energy: 0.5, interest: 0.5, stress: 0, focus: "" },
        behavior: {
          enabled: true, allow_no_reply: true, allow_follow_up: true, allow_proactive: false,
          allow_end_topic: true, reply_threshold: 0.5, follow_up_threshold: 0.65,
          proactive_threshold: 0.85, end_topic_threshold: 0.75, cooldown_minutes: 10,
        },
        expression: { enabled: false, provider: "builtin", mode: "off", profile: "" },
      };
      const reset = target === "all" ? Object.keys(defaults) : [target];
      reset.forEach((key) => {
        if (defaults[key]) demoFeatures[key] = copy(defaults[key]);
      });
      return { reset, sections: copy(demoFeatures) };
    }
    throw new Error(`未知演示接口：${endpoint}`);
  }

  async function get(endpoint, params) {
    const normalized = normalizeEndpoint(endpoint);
    if (DEMO_MODE) return demoGet(normalized, params || {});
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiGet(normalized, params || {}));
  }

  async function post(endpoint, body) {
    const normalized = normalizeEndpoint(endpoint);
    if (DEMO_MODE) return demoPost(normalized, body || {});
    const bridge = await waitForBridge();
    return unwrap(await bridge.apiPost(normalized, body || {}));
  }

  async function getFeatures() {
    try {
      return await get("features");
    } catch (featuresError) {
      try {
        const [persona, featureState, behavior, expression, auditPayload] = await Promise.all([
          get("persona"),
          get("state"),
          get("behavior"),
          get("expression"),
          get("control-audit", { page: 1, page_size: 8 }).catch(() => ({ items: [] })),
        ]);
        return {
          persona,
          state: featureState,
          behavior,
          expression,
          audit: Array.isArray(auditPayload) ? auditPayload : auditPayload.items || [],
        };
      } catch {
        throw featuresError;
      }
    }
  }

  async function resetControl(body) {
    try {
      return await post("control-reset", body);
    } catch (legacyError) {
      try {
        return await post("control/reset", body);
      } catch {
        throw legacyError;
      }
    }
  }

  global.HumanizeApi = Object.freeze({
    demoMode: DEMO_MODE,
    ready: async function ready() {
      if (!DEMO_MODE) await waitForBridge();
    },
    getOverview: () => get("overview"),
    getJargons: (params) => get("jargons", params),
    getJargonDetail: (id) => get("jargon-detail", { id }),
    getProtocolLogs: (params) => get("protocol-logs", params),
    getSettings: () => get("settings"),
    getFeatures,
    jargonAction: (body) => post("jargon-action", body),
    savePersona: (body) => post("persona", body),
    saveState: (body) => post("state", body),
    saveBehavior: (body) => post("behavior", body),
    saveExpression: (body) => post("expression", body),
    resetControl,
  });
})(window);
