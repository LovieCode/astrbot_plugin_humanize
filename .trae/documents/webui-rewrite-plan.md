# Humanize WebUI 全量重写计划

## Context（背景）

当前 WebUI 在 `feat/webui-optimization` 分支存在两个核心问题：

1. **功能与插件对不齐**

   * 缺失 `memory-status`、`memory-providers` 接口对接；运行总览不完整。

   * `api.js` 仅缺 2 个方法（已确认）。

   * 现有 UI 仍含 Persona/State/Behavior/Expression（Control）入口的引用残留，但按 `PLAN.md` 第 3 节"删除范围"，Control 运行时逻辑正由另一个 agent 裁剪，新 WebUI **不应**包含任何 Control 入口。

2. **样式与交互极差**

   * 单文件臃肿：`app.js` 77KB、`memory.js` 51KB、`examples.js` 46KB、`style.css` 75KB、`lucide.js` 399KB。

   * 现有粉色主题堆砌大量装饰元素（`sidebar-signal-grid`、`bot-halo`、`bot-face`、`bot-antenna`、`brand-sakura`、`signal-motif`），过于花哨。

**目标**：在 `feat/webui-optimization` 分支全量重写 `pages/humanize/`，对齐 `PLAN.md` 保留范围，采用克制粉色 + 樱花元素 + 书卷气衬线字体的简洁柔和风格。

## 用户已确认的决策

* **重制范围**：全量重写 HTML/CSS/JS，保留 `api.js` 桥接层和后端接口契约。

* **构建方式**：可用轻量库但**不要工程化**（无 Vite/webpack），保持 `<script defer>` 直接引入。

* **图标方案**：裁剪 lucide 精简包（< 20KB，仅 32 个图标）。

* **字体方案**：Noto Serif SC（标题，国内 CDN）+ 系统无衬线（正文）。

* **风格**：粉色樱花树主题，简洁柔和、书卷气、不过于花哨。

## 功能边界（对齐 PLAN.md）

### 必须包含（8 个视图）

1. 运行总览（overview + context-stats + provider-cache + memory-status）
2. 黑话词库（jargons 列表 + 详情 + 含义/别名/证据/合并 + 导出）
3. 长期记忆（memories + memory-detail + memory-action + 召回调试 + 后台任务）
4. 回复样例（reply-examples + 详情 + 1-3 轮对话编辑器 + 召回调试）
5. 上下文追踪（context-runs + context-run 详情 + TraceViewer）
6. 协议监控（protocol-logs + 7 天趋势 SVG + 当前规则）
7. 提示词模板（5 个 key：rule/protocol/repair/memory\_extraction/reply\_examples）
8. 设置（公开配置 + chat-providers + memory-providers + memory-agent-options + provider-cache-capabilities）

### 明确不包含（与另一个 agent 协同）

* Control（persona/state/behavior/expression）入口、`control-audit`、`control/reset`、`features/control-overview`。

* 安装器、Web Studio、独立服务入口。

## 文件结构

所有文件位于 `pages/humanize/`，扁平 + `views/` 子目录：

| 文件                  | 职责                                                                        | 预计大小     |
| ------------------- | ------------------------------------------------------------------------- | -------- |
| `index.html`        | HTML 外壳：sidebar + workspace + drawer root + toast region                  | \~3.5 KB |
| `style.css`         | 设计 tokens + 布局 + 通用组件 + 各视图样式（全新编写）                                       | \~30 KB  |
| `lucide.js`         | 32 个图标的精简 IIFE 包                                                          | \~16 KB  |
| `api.js`            | 现有基础上仅补 `getMemoryStatus` / `getMemoryProviders`                          | \~3.5 KB |
| `core.js`           | 共享工具：element/icon/format\*/scope\*/debounce/requestIdGuard                | \~6 KB   |
| `ui.js`             | 通用组件：Button/Field/Drawer/Toast/Tabs/Pagination/Badge/Metric/TraceViewer 等 | \~12 KB  |
| `views/overview.js` | 运行总览                                                                      | \~6 KB   |
| `views/jargons.js`  | 黑话词库（最复杂，含 sense 合并）                                                      | \~13 KB  |
| `views/memory.js`   | 长期记忆（4 tab + drawer）                                                      | \~12 KB  |
| `views/examples.js` | 回复样例（3 tab + TurnEditor）                                                  | \~12 KB  |
| `views/context.js`  | 上下文追踪                                                                     | \~8 KB   |
| `views/protocol.js` | 协议监控                                                                      | \~6 KB   |
| `views/prompts.js`  | 提示词模板                                                                     | \~6 KB   |
| `views/settings.js` | 设置（只读）                                                                    | \~7 KB   |
| `app.js`            | 入口：sidebar 渲染 + 路由 + mount/unmount + boot                                 | \~5 KB   |

**Script 加载顺序**：`lucide → api → core → ui → views/* → app`，全部 `defer`。

## 设计系统

### 颜色（克制粉色）

```
--pink: #b86d8a          主色（nav 激活、主按钮）
--pink-strong: #9c4d6e   hover/激活边框
--pink-soft: #f4dde6     浅背景（徽章、tab 激活）
--pink-faint: #fbf2f5    极浅（hover、表格条纹）
--surface: #fdfbfc       主背景
--surface-blush: #fbf6f8 sidebar 背景
--border: #ece4e8        常规边框
--text: #4c4549          正文
--text-strong: #2f292c   标题
--text-muted: #8a8186    次要
--success/warning/danger/info  低饱和语义色
```

### 字体（国内可访问 CDN）

```html
<link rel="preconnect" href="https://fonts.font.im" crossorigin>
<link rel="stylesheet"
      href="https://fonts.font.im/css2?family=Noto+Serif+SC:wght@400;500;700&display=swap">
```

* 标题（h1-h4、品牌名、metric 数值）：`var(--font-serif)`，500/700

* 正文/表单/表格/按钮：系统无衬线

* 代码/JSON/trace content：等宽字体

* fallback 链含 `"Source Han Serif SC", "Songti SC", serif`，CDN 失败时降级到系统宋体。

### 樱花装饰（仅 3 处，克制使用）

1. Sidebar 品牌：单朵简笔樱花 SVG（24×24，5 瓣描边，无填充）
2. Nav 激活态：左侧 2px 竖条 + 4px 圆点（替代原 `signal-motif`）
3. EmptyState：48×48 灰粉色樱花 SVG

**完全删除**：`sidebar-signal-grid`、`bot-halo`、`bot-face`、`bot-antenna`、`brand-sakura`、`signal-motif`、`nav-signal`。

### 布局

* `app-shell`：sidebar (220px) + workspace（topbar + main）

* 视图统一骨架：PageHeader → Toolbar → MetricsGrid（可选）→ 主面板 → Pagination（可选）

* Drawer：右侧滑入，520px 宽，<1280px 时占满视口-32px；ESC/backdrop 关闭；Tab 焦点陷阱。

* 最小宽度 1024px（桌面优先）。

## api.js 扩展（仅补 2 个方法）

完整方法集见 `pages/humanize/api.js`。新增：

* `getMemoryStatus: () => get("memory-status")`

* `getMemoryProviders: () => get("memory-providers")`

**不补** `getFeatures` / `getControlOverview`（Control 已裁剪）。

保留现有 `unwrap` / `waitForBridge` / `get` / `post` 实现不变。

## 关键技术约束

| 约束                                 | 说明                                                                                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **OpenViking memory ID 是 SHA-256** | 64 位小写 hex 字符串，全程按字符串透传，禁止 `Number(id)`。Drawer 标题显示前 8 位 + `…`。`api.getMemoryDetail(id)` 接受 string。                                   |
| **scope\_token 是签名令牌**             | `base64url(payload).base64url(sig[:20])`，前端只透传不构造，options 来自 `memory-overview` 或 `memories` 响应。**不展示** `scope_hash` / `subject_hash`。 |
| **request\_id 是字符串**               | `api.getContextRun(id)` 接受 string，DOM `data-request-id` 存字符串。                                                                         |
| **AGENTS.md 安全规则**                 | 持久化内容必须用 `textContent` / `createElement`，**禁止** `innerHTML` 拼接后端数据。`innerHTML` 仅允许在 `lucide.js`（SVG 静态字符串）和受控 trace 渲染中使用。            |
| **乐观锁字段**                          | memory 用 `revision`，reply-example 用 `version`，提交时携带，409 时提示并刷新。                                                                       |
| **memory statuses 真值**             | `active / candidate / rejected / superseded`（仅 4 种）。                                                                                  |
| **reply example statuses 真值**      | `draft / approved / rejected / tombstoned`（仅 4 种）。                                                                                    |
| **memory actions**                 | `activate / approve / confirm / create / delete / reject / save / update`。                                                            |
| **全局作用域二次确认**                      | 选择 global 时 `confirm("全局记忆会对这个 Agent 的所有聊天生效，确定继续吗？")`。                                                                               |
| **防竞态**                            | 视图维护 `listRequestId` / `detailRequestId` / `viewEpoch` 计数器，过期响应丢弃；unmount 时 epoch++。                                                  |

## 视图实现要点（精简）

### 1. 运行总览 `views/overview.js`

* 并行加载：`getOverview` + `getContextStats({days:7})` + `getContextRuns({page:1,page_size:8})` + `getProviderCacheCapabilities` + `getChatProviders` + `getMemoryStatus`（新增）

* 6 个 metric 卡：词条 / 待处理 / 协议成功率 / 上下文运行 / 平均 Token / 省略段

* Provider Prompt Cache 面板：6 个观测统计 + capability 表

* 内置记忆状态面板：状态徽章 + dl（事实源/OpenViking state/worker/最近召回）

* 最近 8 条上下文运行表格（点击跳转 context 视图）

* 各面板失败时降级显示警告条，不阻断整体。

### 2. 黑话词库 `views/jargons.js`

* 左右双列：表格 + 详情

* 工具条：搜索 / 状态 / 作用域 / 导出 / 学习开关（只读提示）

* 详情含：匹配设置卡片（match\_mode/case\_sensitive/enabled/别名 textarea）+ 含义列表 SenseCard + 新增含义 + 合并 UI（≥2 senses）+ 事实 dl + 证据列表 + 删除区

* Actions：`confirm/reject/update/delete/update_entry/replace_aliases` + sense 类操作 + `merge_sense`（source/target）+ `delete_sense`

* jargon id 是**整数**。

### 3. 长期记忆 `views/memory.js`

* 4 tab：列表 / 候选审核 / 召回调试 / 后台任务

* 列表卡片：memory\_key + 状态徽章 + preview + 元数据 chips

* 召回调试：query textarea + 作用域 select（必填）+ 人格 select（必填，禁用 `*`）+ 类型 + limit

* Drawer 编辑器：key/type/scope/agent/content/structured\_value(JSON)/confidence/importance/reason/valid\_from/until

* RecordsSection × 5：证据链 / 冲突 / Revision / 召回记录 / 审计

### 4. 回复样例 `views/examples.js`

* 3 tab：样例库 / 候选审核 / 召回测试

* **TurnEditor**：1-3 轮对话，每轮 role select(user/assistant) + content textarea，移除按钮，"增加一轮"按钮（达 3 轮禁用）

* Drawer：title/scope/agent/topic/intent/style\_tags/keywords/turns/ideal\_reply/conditions/exclusions/notes/quality\_score

* RecordsSection × 3：使用记录 / Revision / 审核记录

* reply example id 是**整数**。

### 5. 上下文追踪 `views/context.js`

* 双列：runs 表格 + 详情面板

* 详情：summary dl + 模型请求快照（TraceViewer）+ 插入段列表（每段 TraceViewer）+ 响应快照 + error 警告条

* TraceViewer：自动检测 JSON/Markdown/Code/Plain 格式，字符数显示，复制按钮，展开/收起

### 6. 协议监控 `views/protocol.js`

* 左：7 天成功率 + SVG 折线图（viewBox 360×100，stroke 用 `--success`）+ 最近 100 条日志表

* 右：当前规则 dl（enabled / 注入模式 / 配置者 / 字数限制 / 代码文本保持完整）

* 空数据居中显示 "暂无协议样本"。

### 7. 提示词模板 `views/prompts.js`

* 5 张卡片：rule / protocol / repair / memory\_extraction / reply\_examples

* 每张：label + key(code) + description + variables chips + textarea 编辑器 + 字符数 + \[恢复默认]\[撤销修改]\[保存模板]

* `required_variables` 用 `*` 标记

* 实时 dirty 状态，保存按钮仅在 dirty && 有内容时启用

* 重置前 confirm 对话框

* 保存失败 400 时 toast 显示具体错误（如 unsupported variable）

### 8. 设置 `views/settings.js`

* 全部只读

* 左：公开配置 dl（按 key 字母序，boolean → 开启/关闭，array → 顿号分隔）

* 右：4 个 Provider 子面板（Chat/Memory/Agent Options/Cache Capabilities）

* `memory_identity_secret_env` 不展示值，显示"已配置"/"未配置"

* `admin_qq_ids` 只显示前 3 位 + `***`

## lucide 裁剪清单（32 个）

**导航 8**：house, book-open, brain, messages-square, scan-search, chart-no-axes-combined, file-text, settings

**通用操作 10**：chevron-down, chevron-right, arrow-right, x, plus, save, refresh-cw, search, copy, maximize-2

**状态反馈 4**：circle-alert, check, ban, trash-2

**编辑操作 3**：rotate-ccw, undo-2, badge-check

**视图专属 7**：list-filter, download, quote, star, search-check, list, list-todo

**实现**：手工提取这 32 个 icon 的 SVG path，重写为 IIFE 精简包，挂到 `window.lucide`。`createIcons()` 兼容官方实现，扫描 `[data-lucide="..."]` 替换为 SVG，name 转 camelCase（`name.replace(/-([a-z])/g, (_, c) => c.toUpperCase())`）查表。

## 实施步骤

### Phase 0：准备（半天）

* 在 `feat/webui-optimization` 分支上工作（不切新分支，符合用户意图）。

* 备份现有 `pages/humanize/` 内容（git 已追踪，可随时回退）。

* 与另一个 agent 确认 Control 裁剪进度（不阻塞前端，前端独立于后端裁剪）。

* `uv run pytest -q` 确认当前基线绿。

### Phase 1：基础设施（1 天）

1. `api.js` - 补 2 个方法
2. `core.js` - 共享工具
3. `lucide.js` - 32 个图标精简包
4. `style.css` - tokens + 通用组件 + 布局
5. `ui.js` - 通用组件库
6. `index.html` - 最小外壳 + 引入所有 JS

**验收**：`node --check pages/humanize/*.js` 全绿；浏览器加载页面，sidebar 显示 8 个 nav，toast 可手动触发。

### Phase 2：核心视图（2 天）

1. `views/jargons.js` - 最复杂，先做
2. `views/memory.js` - SHA-256 id + scope\_token + 4 tab
3. `views/examples.js` - TurnEditor
4. `views/context.js` - TraceViewer

**验收**：每个视图能 mount，调真实后端返回数据，表单提交 toast + 刷新。

### Phase 3：辅助视图（1 天）

1. `views/overview.js` + `views/protocol.js` + `views/prompts.js` + `views/settings.js`

**验收**：8 视图完整加载，无 console error，无 Control 接口调用（grep 验证）。

### Phase 4：联调与稳定化（1 天）

1. `app.js` - 路由 + mount/unmount + boot
2. 全量回归：8 视图 CRUD 全链路
3. 安全审查：`grep -r "innerHTML" pages/humanize/` 仅 lucide.js/ui.js 受控使用
4. 裁剪审查：`grep -rE "getFeatures|getControlOverview|control-audit|persona|behavior" pages/humanize/` 无 Control 残留
5. 性能：1000+ jargons 分页流畅

### Phase 5：部署与文档（半天）

按 `PLAN.md` 第 6 节执行：`uv run pytest -q` / `uv run ruff check .` / `uv run ruff format --check .` / 所有 JS `node --check` / `git diff --check`。

提交 Conventional Commits：`feat(webui): rewrite humanize management UI with cherry-blossom theme`。

## 验收标准

### 代码质量

* [ ] `pages/humanize/*.js` 全部通过 `node --check`

* [ ] `uv run ruff check .` / `uv run ruff format --check .` / `git diff --check` 通过

### 功能对齐

* [ ] 8 视图全部能加载并对接后端

* [ ] api.js 暴露 28 个方法（21 GET + 6 POST + ready）

* [ ] OpenViking memory ID 按 SHA-256 字符串处理

* [ ] scope\_token 透传不构造，不展示 scope\_hash/subject\_hash

* [ ] 回复样例 TurnEditor 支持 1-3 轮 user/assistant

* [ ] 5 个提示词模板可编辑/撤销/重置/保存

* [ ] 召回调试正确传 scope\_token + agent\_id（禁用 `*`）

### 裁剪对齐

* [ ] 不含 Control（persona/state/behavior/expression）入口

* [ ] 不调用 `getFeatures` / `getControlOverview` / `control-audit`

* [ ] sidebar 仅 8 个 nav

* [ ] api.js 不暴露 Control 方法

### 安全合规

* [ ] 持久化内容通过 `textContent` / `createElement` 设置

* [ ] `innerHTML` 仅用于 lucide.js 静态 SVG 和受控 trace 渲染

* [ ] 用户输入（搜索词、表单）展示前不经过 HTML 解析

### 样式

* [ ] 粉色樱花主题，简洁柔和、书卷气，不过于花哨

* [ ] 装饰元素仅 3 处（sidebar 品牌、nav 激活态、empty state）

* [ ] 完全删除 `signal-motif`/`bot-halo`/`bot-face`/`bot-antenna`/`brand-sakura`/`sidebar-signal-grid`/`nav-signal`

* [ ] Noto Serif SC 标题 + 系统无衬线正文，国内 CDN 可访问

* [ ] lucide.js < 20KB

### 后端契约

* [ ] 不修改 `humanize/web/routes.py` 及任何 `humanize/` 下的 Python 代码

* [ ] 所有改动仅在 `pages/humanize/` 目录下

## 风险与对策

### R1：与另一个 agent 裁剪 Control 的协同

**当前状态**：`routes.py` 仍 `from ..services.control import ControlService` 但 `services/control.py` 不存在——后端处于引用已断、文件未删的中间态。
**对策**：

* 前端**绝不**对接 Control 接口，即使后端短暂残留也不调用。

* Phase 4 grep 验证：`grep -rE "control|persona|behavior|expression" pages/humanize/` 应无匹配（`expression` 在 protocol envelope 上下文中的合法用法除外）。

* 不与后端裁剪 agent 产生文件冲突：前端只改 `pages/humanize/`，后端只改 `humanize/`。

### R2：OpenViking memory ID 是 SHA-256

**风险**：UI 误 `Number(id)` → `Infinity`/NaN → 404。
**对策**：`views/memory.js` 全程保持 id 为字符串；Drawer 标题截断显示前 8 位 + `…`；DOM `data-id` 存字符串。

### R3：scope\_token 是签名令牌

**风险**：前端自行构造或缓存失效 token → 403。
**对策**：`ScopeSelect` options 必须来自后端响应，不接受手工输入；提交时直接透传。

### R4：提示词模板变量校验

**风险**：用户误删 `{{scene}}` 等占位符 → 后端 400。
**对策**：卡片上显著展示 variables chips（required 用 `*` 标记）；编辑器下方实时显示当前使用的变量与声明对比，缺失时高亮警告；400 错误 toast 显示具体错误。

### R5：字体 CDN 兜底

**风险**：font.im 在某些网络环境不可用。
**对策**：`font-family` fallback 链含 `"Source Han Serif SC", "Songti SC", serif`；`display=swap` 不阻塞首屏。

### R6：装饰元素残留

**风险**：CSS/HTML 残留旧装饰类名被 JS 误引用。
**对策**：style.css 和 index.html 全新编写，不基于现有文件修改；Phase 4 grep 验证无残留。

### R7：drawer 焦点陷阱

**对策**：复用现有 `memory.js` 第 566 行 `drawerKeyHandler` 实现；drawer `role="dialog"` `aria-modal="true"`；ESC 关闭；记录 `lastFocus` 关闭时还原。

### R8：列表请求防竞态

**对策**：每视图维护 `listRequestId` / `detailRequestId` / `viewEpoch`；unmount 时 `epoch++` 让所有 in-flight 响应失效。

## 关键文件路径

### 现有可参考（勘察用，不修改后端）

* [humanize/web/routes.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/web/routes.py) - 后端契约

* [humanize/domain/prompts.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/domain/prompts.py) - 5 个模板 spec

* [humanize/config.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/config.py) - `as_public_dict` 字段

* [humanize/provider\_catalog.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/provider_catalog.py) - Provider 结构

* [humanize/openviking/management.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/openviking/management.py) - memory detail 结构

* [humanize/repositories/sqlite.py](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/humanize/repositories/sqlite.py) - jargon/context/protocol/job 字段

### 现有前端可复用逻辑（重写时提取）

* [pages/humanize/api.js](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/pages/humanize/api.js) - 桥接层（保留扩展）

* [pages/humanize/memory.js](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/pages/humanize/memory.js) - drawer/scopeSelect/personaSelect/recall 实现

* [pages/humanize/examples.js](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/pages/humanize/examples.js) - TurnEditor 实现

* [pages/humanize/app.js](file:///d:/Code/Python/_root/AstrBot/data/plugins/astrbot_plugin_humanize/pages/humanize/app.js) - TraceViewer/ProviderCache 面板实现

### 待重写（实施目标）

* `pages/humanize/index.html` / `style.css` / `lucide.js` / `api.js` / `app.js`

* `pages/humanize/core.js`（新增）/ `ui.js`（新增）

* `pages/humanize/views/overview.js` / `jargons.js` / `memory.js` / `examples.js` / `context.js` / `protocol.js` / `prompts.js` / `settings.js`（全部新增）

## 端到端验证

1. **本地启动 AstrBot**，加载 Humanize 插件，打开 WebUI `http://localhost:6185/static/plugins/astrbot_plugin_humanize/pages/humanize/index.html`。
2. **8 视图逐一手测**：

   * overview：6 metric 显示真实数值；Provider Cache 面板有数据；memory status 显示 ready/not\_initialized

   * jargons：列表加载 → 选中词条 → 改别名保存 → 新增含义 → 合并含义 → 删除词条

   * memory：4 tab 切换 → 创建候选记忆 → 召回调试 → 后台任务刷新 → Drawer 编辑保存

   * examples：3 tab 切换 → 创建样例（1-3 轮对话）→ 审核通过 → 召回测试

   * context：runs 列表 → 选中详情 → 展开/折叠 TraceViewer → 复制内容

   * protocol：7 天趋势图渲染 → 100 条日志翻页 → 当前规则展示

   * prompts：5 张卡片加载 → 编辑某模板 → 保存 → 撤销 → 恢复默认

   * settings：公开配置 dl 展示 → 4 个 Provider 子面板有数据
3. **安全审查**：DevTools 检查无 innerHTML 拼接后端数据；grep 验证。
4. **裁剪审查**：grep 验证无 Control 残留。
5. **样式走查**：粉色克制、无装饰堆砌、标题宋体、樱花仅 3 处。
6. **回归测试**：`uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .`、所有 JS `node --check`、`git diff --check` 全

