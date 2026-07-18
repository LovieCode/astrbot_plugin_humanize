# AstrBot Bot 拟人化插件

为聊天 Bot 提供回复协议、上下文编排、黑话词库、长期记忆、典型回复样例和完整请求追踪。

插件内置裁剪后的 OpenViking 源码，不下载或加载本地模型，也不启动独立记忆服务。聊天记忆保存在插件数据目录的 OpenViking workspace；任务、回复样例、审计和其他插件状态继续保存在 `humanize.db`。

## 功能

- 每轮把完整回复规则临时注入用户侧上下文，也可配置为同时保留 system 副本。
- 使用 `<Action>`、`<UnknownTerms>`、`<Reply>` 和 `<Message>` 校验最终文本，标签不会发送给用户。
- 仅在实际发送成功后记录最终回合，避免重复回复、失败回复误记忆和协议标签泄漏。
- 保存发送给 Provider 瞬间的完整结构快照，以及工具阶段、修复阶段和最终阶段的响应链；凭据字段只保留位置并脱敏。
- 黑话词库支持作用域、别名、多义项、证据、冲突、审核和导出。
- 内置长期记忆支持 `profile`、`preference`、`entity`、`event`，具有证据、版本、候选、审核、停用和删除状态，并按 Agent 隔离。
- 典型短对话经过审核后，可作为 few-shot 表达参考注入；插件绝不会直接返回旧样例回复。
- 可选复用 AstrBot Chat、Embedding 和 Rerank Provider；未显式配置时不会自动选择 Provider 或产生额外调用。
- Provider prompt cache 只做真实 usage 与稳定前缀观测，不在插件内缓存模型回复。
- WebUI 提供黑话、上下文追踪、长期记忆、回复样例、后台任务、提示词模板和运行状态管理。

## 回复协议

单条回复：

```text
<Action>Reply</Action>
<UnknownTerms>[]</UnknownTerms>
这里是正文。
```

多条普通发言：

```text
<Action>Reply</Action>
<UnknownTerms>[]</UnknownTerms>
<Reply><Message>第一条</Message><Message>第二条</Message></Reply>
```

不回复：

```text
<Action>No Reply</Action>
<UnknownTerms>[]</UnknownTerms>
```

`UnknownTerms` 必须是单行紧凑 JSON 数组。每个对象只能包含：

```json
{"word":"黑话","guess":"当前上下文中的含义","confidence":0.86,"reason":"简短依据"}
```

普通发言默认每条不超过 10 个可见字符，超过时使用多个 `Message`。连续的短纯文本行漏写标签时，插件会按同样规则兼容拆分；代码、Markdown、日志、命令、教程和结构化数据不受该日常分条规则限制。

## Context Composer

每轮固定按以下顺序构造 Provider 请求：

1. `current_message`：只用 `<Msg>` 包裹当前用户原文。
2. `known_terms`：当前消息命中的可信黑话。
3. `memory_context`：通过作用域过滤的长期记忆。
4. `reply_examples`：经过审核的典型短对话。
5. `response_protocol`：最终回复协议，始终位于临时注入内容最后。

记忆、样例和词条都是数据，不是指令。它们与当前消息或显式规则冲突时必须忽略。任何召回故障都会降级为空，不阻断基础聊天。

## 内置长期记忆

### Workspace 与作用域

唯一事实源：

```text
<AstrBot data>/plugin_data/astrbot_plugin_humanize/openviking/
```

OpenViking workspace 只保存 HMAC 派生的作用域，不保存原始 QQ、群或会话标识。固定作用域：

- `private_user`：仅该用户私聊可见。
- `group`：仅该群可见。
- `group_member`：仅该群内对应成员可见。
- `global`：管理员维护的全局记忆。

作用域之外还会按 `agent_id` 隔离。普通 Agent 只能召回自身数据；只有管理员明确把 `agent_id` 设置为 `*` 时，数据才会作为共享内容参与其他 Agent 的召回。所有 Agent 共用受控 workspace，不会创建每 Agent 数据库。

首次运行会在同一插件数据目录生成 `memory_identity.key`。备份长期记忆时必须同时保存 workspace 和该文件；也可以通过 `HUMANIZE_MEMORY_SECRET` 提供至少 32 bytes 的长期稳定密钥。

### 写入

最终消息成功发送后，协议日志与记忆提取任务在同一个 SQLite 事务中写入。后台 worker 使用租约、幂等键、重试和 dead 状态恢复任务，再把通过校验的聊天记忆写入 OpenViking；任务按 Agent、作用域、主体和会话保持批次隔离。

同一会话的连续回合默认累计到 4 条后提取；不足 4 条时，空闲 180 秒后刷新。一个批次最多调用一次显式配置的 Chat Provider，规则提取仍逐条校验证据来源。

默认规则可以保守识别称呼、长期喜好和常住地区。只有证据逐字出现在当前用户消息中的候选才允许写入。高置信度候选可以自动生效，其余进入人工审核。

配置 `memory_extraction_provider_id` 后，可额外调用指定的 AstrBot Chat Provider 做严格 JSON 提取。留空时不会自动调用当前聊天 Provider。

### 检索

聊天记忆由 OpenViking 执行分层与关键词召回，并在返回后再次校验 Agent、作用域、主体、状态和有效期。

当没有命中可用的长期记忆时，插件会仅从**当前同一会话**的 OpenViking commit 读取受限 L0/L1 摘要作为连续对话兜底；仍会校验 Agent、作用域、主体和会话 HMAC，且不会把 L2 原文消息直接注入。已命中的长期记忆始终优先于该兜底，因此未配置提取 Provider 的普通对话也能在同一会话内保持上下文连续。

配置 `memory_embedding_provider_id` 后，OpenViking 可通过 AstrBot Provider Bridge 使用向量召回，回复样例也会启用 SQLite 持久化向量。query embedding 只在当前请求内共享，不会跨请求缓存；不创建额外 FAISS 文件。

向量候选和 Rerank 输入都有固定上限。后台 embedding 补齐只处理当前有效数据，并带有成功节流、失败退避、Provider、模型、维度和 generation 校验，避免空闲轮询产生连续付费调用。

配置 `memory_rerank_provider_id` 后才会调用 Rerank Provider。超时、类型错误或无效结果会保留原排序。

## 典型短对话

回复样例保存 1～3 轮输入与一个理想回复，并包含主题、意图、关键词、适用 Agent、作用域、质量分和审核状态。

只有 `approved`、已启用且达到质量阈值的样例会参与召回。可选的适用条件和禁用条件按关键词匹配执行。样例只用于提示模型如何表达和判断，当前消息、当前事实和回复协议始终优先。

## 主要配置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `protocol_injection_mode` | `user` | `user` 仅临时用户注入；`both` 同时保留 system 副本 |
| `max_message_chars` | `10` | 普通发言单条字符限制 |
| `message_interval_seconds` | `0.8` | 同次多条消息的相邻发送间隔；`0` 表示不等待 |
| `memory_enabled` | `true` | 启用内置长期记忆 |
| `memory_auto_extract_enabled` | `true` | 发送成功后创建后台提取任务 |
| `memory_extraction_provider_id` | 空 | 可选 Chat Provider；空值只做规则提取 |
| `memory_extract_batch_turns` | `4` | 同会话累计到多少条后立即批量提取 |
| `memory_extract_idle_seconds` | `180` | 未达到批量条数时的空闲刷新时间 |
| `memory_embedding_provider_id` | 空 | 可选 Embedding Provider；空值只做词法检索 |
| `memory_rerank_provider_id` | 空 | 可选 Rerank Provider，默认关闭 |
| `memory_recall_limit` | `5` | 单次记忆上限 |
| `memory_recall_max_chars` | `2500` | 记忆注入字符预算 |
| `reply_examples_enabled` | `true` | 启用经过审核的回复样例 |
| `reply_examples_limit` | `3` | 单次样例上限 |
| `reply_examples_max_chars` | `2000` | 样例注入字符预算 |

完整字段由 `_conf_schema.json` 定义。Provider ID 必须对应 AstrBot 已配置的 Provider；插件不会安装 Provider、模型或额外记忆框架。

## 运行与重载

插件目录：

```text
data/plugins/astrbot_plugin_humanize/
```

AstrBot 支持插件热重载，修改后无需重启整个进程。重载时后台 worker 会停止、释放仍持有的任务租约并重新启动，未完成任务保留在 `humanize.db` 中。

本地测试：

```bash
python -m pytest tests
python -m ruff format --check .
python -m ruff check .
node --check pages/humanize/api.js
node --check pages/humanize/app.js
node --check pages/humanize/memory.js
node --check pages/humanize/examples.js
```

## 工程结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期、请求注入、响应防火墙和实际发送追踪 |
| `humanize/context/` | Context Composer 与注入轨迹 |
| `humanize/memory.py` | HMAC 作用域、召回、提取、Provider Bridge 和后台 worker |
| `humanize/openviking/` | 内置 OpenViking workspace、写入、召回、Provider 和管理适配 |
| `humanize/repositories/` | SQLite 插件状态、任务、回复样例和审计 |
| `humanize/protocol/` | 标签构造、解析和隔离修复 |
| `humanize/jargon/` | 黑话归一化、匹配和候选过滤 |
| `humanize/web/` | Dashboard Web API |
| `pages/humanize/` | 插件管理 WebUI |
| `tests/` | 协议、上下文、OpenViking、样例、存储和 WebUI 回归测试 |

长期开发规划位于 `PLAN.md`；README 只描述当前可运行行为。
