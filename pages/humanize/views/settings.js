(function registerSettingsView(global) {
  "use strict";

  global.HumanizeViews = global.HumanizeViews || {};

  const Api = global.HumanizeApi;
  const Core = global.HumanizeCore;
  const Ui = global.HumanizeUi;

  /** View state (reset on each mount). */
  let ctx = null;
  let loadGuard = null;
  let bodyRoot = null;
  let providerRoot = null;

  /**
   * Configuration groups rendered as panels. Each group has a title, icon and
   * a list of field descriptors used to build definition lists.
   *
   * Field descriptor:
   *   - key:    property name in as_public_dict()
   *   - label:  display name
   *   - kind:   "bool" | "text" | "list" | "rate" | "int" | "duration"
   *   - hint:   optional explanation shown as subtitle
   */
  const GROUPS = [
    {
      title: "基础设置",
      icon: "house",
      fields: [
        { key: "enabled", label: "插件总开关", kind: "bool" },
        { key: "default_rule_enabled", label: "默认规则注入", kind: "bool" },
        { key: "admin_name", label: "管理员称呼", kind: "text" },
        { key: "admin_qq_ids", label: "管理员 QQ 列表", kind: "list" },
        { key: "max_message_chars", label: "单条消息字数上限", kind: "int" },
      ],
    },
    {
      title: "协议控制",
      icon: "shield-check",
      fields: [
        { key: "protocol_enabled", label: "回复协议", kind: "bool" },
        { key: "protocol_injection_mode", label: "注入模式", kind: "text" },
        { key: "protocol_version", label: "协议版本", kind: "int" },
        { key: "protocol_repair_retry_enabled", label: "修复重试", kind: "bool" },
        { key: "protocol_log_retention_days", label: "日志保留天数", kind: "int" },
        { key: "no_reply_enabled", label: "允许 No Reply", kind: "bool" },
      ],
    },
    {
      title: "黑话注入",
      icon: "book-open",
      fields: [
        { key: "jargon_enabled", label: "黑话注入", kind: "bool" },
        { key: "min_confidence_for_injection", label: "注入最低置信度", kind: "rate" },
        { key: "max_injected_jargons", label: "最大注入条数", kind: "int" },
      ],
    },
    {
      title: "长期记忆",
      icon: "brain",
      fields: [
        { key: "memory_enabled", label: "记忆服务", kind: "bool" },
        { key: "memory_auto_extract_enabled", label: "自动提取", kind: "bool" },
        { key: "memory_extraction_provider_id", label: "提取 Provider", kind: "text" },
        { key: "memory_embedding_provider_id", label: "嵌入 Provider", kind: "text" },
        { key: "memory_rerank_provider_id", label: "重排 Provider", kind: "text" },
        { key: "memory_identity_secret_env", label: "身份密钥环境变量", kind: "text" },
        { key: "memory_recall_timeout_seconds", label: "召回超时（秒）", kind: "duration" },
        { key: "memory_auto_activate_confidence", label: "自动激活置信度", kind: "rate" },
        { key: "memory_candidate_min_confidence", label: "候选最低置信度", kind: "rate" },
        { key: "memory_recall_limit", label: "召回条数上限", kind: "int" },
        { key: "memory_recall_score_threshold", label: "召回评分阈值", kind: "rate" },
        { key: "memory_recall_max_chars", label: "召回最大字符数", kind: "int" },
        { key: "memory_extract_batch_turns", label: "提取批次轮数", kind: "int" },
        { key: "memory_extract_idle_seconds", label: "提取空闲秒数", kind: "int" },
        { key: "memory_job_max_attempts", label: "任务最大尝试次数", kind: "int" },
      ],
    },
    {
      title: "回复样例",
      icon: "messages-square",
      fields: [
        { key: "reply_examples_enabled", label: "回复样例", kind: "bool" },
        { key: "reply_examples_limit", label: "注入条数上限", kind: "int" },
        { key: "reply_examples_max_chars", label: "最大字符数", kind: "int" },
        { key: "reply_examples_min_quality", label: "最低质量分", kind: "rate" },
        { key: "reply_examples_recall_score_threshold", label: "召回评分阈值", kind: "rate" },
      ],
    },
  ];

  /**
   * Format a config value for display according to its declared kind.
   * @param {object} field Field descriptor.
   * @param {*} value Raw value from as_public_dict().
   * @returns {HTMLElement|string} Renderable content.
   */
  function formatFieldValue(field, value) {
    if (field.key === "memory_identity_secret_env") {
      return value ? "已配置" : "未配置";
    }
    if (field.key === "admin_qq_ids") {
      return Array.isArray(value) && value.length
        ? value.map((item) => `${String(item).slice(0, 3)}***`).join("、")
        : "—";
    }
    switch (field.kind) {
      case "bool":
        return Ui.createBadge(value ? "启用" : "停用", value ? "success" : "warning");
      case "list": {
        if (Array.isArray(value) && value.length) {
          return value.map((v) => String(v)).join("、");
        }
        return "—";
      }
      case "rate":
        return Core.formatRate(value);
      case "duration":
        return `${Core.numberValue(value)} 秒`;
      case "int":
        return String(Core.numberValue(value));
      case "text":
      default:
        if (value === "" || value == null) return "—";
        return String(value);
    }
  }

  /**
   * Build a definition-list entry for a single config field.
   * @param {object} field Field descriptor.
   * @param {*} value Raw value.
   * @returns {object|null} Definition list entry or null if value is missing.
   */
  function buildEntry(field, value) {
    const rendered = formatFieldValue(field, value);
    return { dt: field.label, dd: rendered };
  }

  /**
   * Render the entire settings view from a public config dict.
   * @param {object} config
   */
  function renderSettings(config) {
    bodyRoot.replaceChildren();
    bodyRoot.style.display = "flex";
    bodyRoot.style.flexDirection = "column";
    bodyRoot.style.gap = "var(--sp-4)";

    const intro = document.createElement("p");
    intro.className = "settings-intro";
    intro.textContent = "以下配置由 AstrBot 插件配置文件管理，此处为只读展示。如需修改，请编辑插件配置后重载。";
    bodyRoot.append(intro);

    GROUPS.forEach((group) => {
      const entries = group.fields
        .map((field) => buildEntry(field, config ? config[field.key] : undefined))
        .filter((entry) => entry !== null);

      const panel = Ui.createPanel({
        title: group.title,
        icon: group.icon,
        body: () => Ui.createDefinitionList(entries, { vertical: true }),
      });
      bodyRoot.append(panel);
    });

    Core.refreshIcons();
  }

  /** Render non-secret Provider discovery and cache observation panels. */
  function renderProviders(chatData, memoryData, agentData, cacheData) {
    providerRoot.replaceChildren();
    providerRoot.style.display = "grid";
    providerRoot.style.gridTemplateColumns = "repeat(auto-fit, minmax(280px, 1fr))";
    providerRoot.style.gap = "var(--sp-4)";

    const providerPanel = (title, icon, items, describe) => Ui.createPanel({
      title, icon,
      body: () => items.length ? Ui.createDefinitionList(items.map((item) => ({
        dt: String(item.label || item.id || item.provider_id || item.model || "Provider"),
        dd: describe(item),
      })), { vertical: true }) : Ui.createEmptyState({
        title: `暂无${title}`,
        message: "当前 AstrBot 运行时未发现可用项。",
      }),
    });

    const chat = Array.isArray(chatData && chatData.providers) ? chatData.providers : [];
    providerRoot.append(providerPanel("Chat Provider", "messages-square", chat, (item) => (
      `${item.adapter || "unknown"} · ${item.model || "未声明模型"} · cache ${item.capability || "unknown"}`
    )));

    const memoryGroups = ["chat", "embedding", "rerank"];
    const memoryItems = memoryGroups.flatMap((kind) => (
      Array.isArray(memoryData && memoryData[kind])
        ? memoryData[kind].map((item) => ({ ...item, kind }))
        : []
    ));
    providerRoot.append(providerPanel("Memory Provider", "brain", memoryItems, (item) => (
      `${item.kind} · ${item.provider_type || item.adapter || "unknown"}${item.model ? ` · ${item.model}` : ""}`
    )));

    const agents = Array.isArray(agentData && agentData.items) ? agentData.items : [];
    providerRoot.append(providerPanel("Agent Options", "badge-check", agents, (item) => (
      `${item.source || "runtime"}${item.id === agentData.default_id ? " · 默认" : ""}`
    )));

    const cache = Array.isArray(cacheData && cacheData.items) ? cacheData.items : [];
    providerRoot.append(providerPanel("Cache Capabilities", "database", cache, (item) => (
      `${item.capability || "unknown"} · ${Core.numberValue(item.cached_samples)}/${Core.numberValue(item.observed_samples)} 命中`
    )));
    Core.refreshIcons();
  }

  /**
   * Fetch the public config and render the settings view.
   */
  async function loadSettings() {
    if (!bodyRoot || !ctx || ctx.isStale()) return;
    const reqId = loadGuard.bump();
    bodyRoot.replaceChildren();
    bodyRoot.append(Ui.createLoading("加载配置…"));
    try {
      const [data, chatData, memoryData, agentData, cacheData] = await Promise.all([
        Api.getSettings(),
        Api.getChatProviders().catch(() => ({ state: "error", providers: [] })),
        Api.getMemoryProviders().catch(() => ({
          state: "error", chat: [], embedding: [], rerank: [],
        })),
        Api.getMemoryAgentOptions().catch(() => ({ state: "error", items: [] })),
        Api.getProviderCacheCapabilities().catch(() => ({ items: [] })),
      ]);
      if (loadGuard.isStale(reqId) || ctx.isStale()) return;
      renderSettings(data || {});
      renderProviders(chatData || {}, memoryData || {}, agentData || {}, cacheData || {});
    } catch (err) {
      if (loadGuard.isStale(reqId) || ctx.isStale()) return;
      bodyRoot.replaceChildren();
      bodyRoot.append(Ui.createAlert({
        variant: "danger",
        title: "加载失败",
        message: (err && err.message) || String(err),
      }));
      providerRoot.replaceChildren();
    }
  }

  /**
   * Mount the settings view: install the refresh action and load data.
   * @param {HTMLElement} root
   * @param {object} viewCtx
   */
  async function mount(root, viewCtx) {
    ctx = viewCtx;
    loadGuard = Core.requestIdGuard();
    root.replaceChildren();

    const shell = document.createElement("div");
    shell.className = "settings-view";
    shell.style.display = "flex";
    shell.style.flexDirection = "column";
    shell.style.gap = "var(--sp-4)";

    bodyRoot = document.createElement("div");
    shell.append(bodyRoot);
    providerRoot = document.createElement("div");
    providerRoot.className = "settings-providers";
    shell.append(providerRoot);
    root.append(shell);

    ctx.setTopbarActions([
      Ui.createButton({
        label: "刷新", variant: "outline", size: "sm", icon: "refresh-cw",
        onClick: () => loadSettings(),
      }),
    ]);

    await loadSettings();
  }

  /** Reset view-scoped state on unmount. */
  function unmount() {
    ctx = null;
    loadGuard = null;
    bodyRoot = null;
    providerRoot = null;
  }

  global.HumanizeViews.settings = { mount, unmount };
})(window);
