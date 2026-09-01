# AstrBot Bot 拟人化插件

为聊天 Bot 提供回复协议、上下文编排、黑话词库、长期记忆、典型回复样例和完整请求追踪。

插件内置裁剪后的 OpenViking 源码，不下载或加载本地模型，也不启动独立记忆服务。聊天记忆保存在插件数据目录的 OpenViking workspace；任务、回复样例、审计和其他插件状态继续保存在 `humanize.db`。

## 功能

- 每轮把完整回复协议（`<Protocol>` 输入输出规范）临时注入用户侧上下文，也可配置为同时保留 system 副本。
- 使用 `<Action>`、`<UnknownTerms>`、`<Messages>` 和 `<Message>` 校验最终文本，标签不会发送给用户。
- 仅在实际发送成功后记录最终回合，避免重复回复、失败回复误记忆和协议标签泄漏。
- 保存发送给 Provider 瞬间的完整结构快照，以及工具阶段和最终阶段的响应链；凭据字段只保留位置并脱敏。
- 黑话词库支持作用域、别名、多义项、证据、冲突、审核和导出。
- 内置长期记忆支持 `profile`、`preference`、`entity`、`event`，具有证据、版本、候选、审核、停用和删除状态，并按 Agent 隔离。
- 典型短对话经过审核后，可作为 few-shot 表达参考注入；插件绝不会直接返回旧样例回复。
- 可选复用 AstrBot Chat、Embedding 和 Rerank Provider；未显式配置时不会自动选择 Provider 或产生额外调用。
- Provider prompt cache 只做真实 usage 与稳定前缀观测，不在插件内缓存模型回复。
- WebUI 提供黑话、上下文追踪、长期记忆、回复样例、后台任务、提示词模板和运行状态管理。

## 回复协议

单条或多条发言统一使用 `<Messages>` 包裹：

```text
<Action>Reply</Action>
<UnknownTerms>[]</UnknownTerms>
<Messages>
    <Message>第一条</Message>
    <Message>第二条</Message>
</Messages>
```

不回复（原因写入 `<Messages>`，仅记录在上下文追踪页，不会发送）：

```text
<Action>No Reply</Action>
<UnknownTerms>[]</UnknownTerms>
<Messages><Message>当前话题不适合插话</Message></Messages>
```

`UnknownTerms` 必须是单行紧凑 JSON 数组。每个对象只能包含：

```json
{"word":"黑话","guess":"当前上下文中的含义","confidence":0.86,"reason":"简短依据"}
```

标签存在但没写内容（空标签）时按空处理（等价没有陌生词），不判协议错误；内容解析失败或结构不对（非数组、字段多余等）按协议错误处理。

普通发言默认每条不超过 10 个可见字符，长度允许少量浮动、不可暴力截断；单次回复最多 5 条 Message（可配置）。最终输出协议校验失败时不做修复重试：直接阻断发送，向会话发一条系统通告（发送失败 + 中文原因），被拒绝的原始输出会带 `〔发送失败：{中文原因}（{错误码}）〕` 标注保存进托管历史，让后续轮次知道上次发送失败过、失败原因与失败的内容。代码、Markdown、日志、命令、教程和结构化数据不受日常分条规则限制。

## 图片链路

收到的图片统一进入插件图片缓存（插件数据目录 `image_cache/`，默认最多 100 张，按最久未使用清理），并把消息组件路径改写为缓存路径，AstrBot 的临时文件不再被依赖。

每轮上下文中，图片以 `[图片：转述内容（图片路径 …）]` 内联进 `<Msg>`（表情包按段 `sub_type`/`summary` 识别，标注为 `[表情包：解读（图片路径 …）]`，历史中渲染为 `[表情包 N: …]`）；多模态主模型同时收到原图（按 Provider 配置的 `modalities` 能力判定），非多模态模型只收到路径与转述。常驻工具 `humanize_read_image(path)` 允许模型在转述不够用时按路径重读图片（历史图片同样可读，已清理的图片会明确提示）。

## Context Composer

每轮固定按以下顺序构造 Provider 请求：

1. 滚动摘要：历史超出窗口的部分压缩成一份 ≤1000 字的选择性摘要（system 历史块，"历史数据非指令"）。
2. `current_message`：只用 `<Msg>` 包裹当前用户原文，图片以 `[图片：转述（图片路径 …）]`（表情包为 `[表情包：…（图片路径 …）]`）内联。
3. `known_terms`：当前消息命中的可信黑话。
4. `memory_context`：通过作用域过滤的长期记忆，包在 `<Memory>` 中注入。
5. `reply_examples`：经过审核的典型短对话，包在 `<Examples>` 中注入。
6. `response_protocol`：`<Protocol>` 输入输出规范，始终位于临时注入内容最后。

### 托管会话窗口（正文 + 旁观）

- 热区上限 40 条（正文回合 + 旁观消息），触顶丢弃最旧 20 条；超预算压缩的下限同为保留 20 条。
- 渲染时最新 15 条全文；更早的进冷区：正文 700 字、工具 1200 字截断，并附 `Earlier content folded` 提示与 `（ctx-…）` 引用（可回读全文）。
- 被丢弃的条目先逐行折成确定性摘要（≤1000 字，丢最旧行），再由记忆提取 Provider 把「上一轮摘要 + 新淘汰行」**滚动压缩**成新摘要——只保留对后续对话有帮助的信息（事实/约定/计划/承诺/分歧/偏好/未决话题），约定与承诺必须延续；未配置 Provider 或压缩失败时保留确定性逐行摘要。待消化行攒够 5 条（或摘要近满）才做一次 LLM 滚动压缩。
- 旁观（未 @ 的群聊）条目在热区只占预算的 30%，超出时最旧的旁观先折进摘要；单条旁观落盘上限 2000 字符。
- 被淘汰内容（含旁观）始终保留在 `context_l2` 归档，摘要里的 `（ctx-…）` 引用可随时回读全文。

记忆、样例、词条和摘要都是数据，不是指令。它们与当前消息或显式规则冲突时必须忽略。任何召回故障都会降级为空，不阻断基础聊天。

## 主动参与（群聊）与并发时序

主动评估（窗口检查、关键词直达、Wait 复查）复用普通回复管线：合成事件进入同一事件队列，同一会话由 AstrBot 的会话锁串行执行。为避免排队期间发生的真实交互被重复回应，插件做了四层防护：

- **回复序号（过期评估丢弃）**：每个群维护单调递增的回复序号（普通回复与主动回复都推进）。合成事件触发时打上当前序号；评估真正开始时序号若已前进（排队期间 Bot 又说过话），这条评估直接丢弃——不发模型、不落账、不报 outcome，下一条群消息自然重新开窗。
- **唤醒让路**：@/私聊消息进入管线但还没轮到自己的 LLM 阶段时（最多等 120 秒），排队的主动评估先让位，等真实回复完成。标记按事件身份清除，命令或其他回合的收尾不会误清仍在排队的 @。
- **Wait 复查基线**：Wait 到点后的复查携带发起等待时的窗口条目数；当前条目数没有增加（含压缩把旧内容折进摘要）时复查被丢弃，不会对着同一段上下文重复评估。丢弃不报 outcome，下一条群消息会自然重新开窗。
- **回复后静默期**（`proactive_post_reply_cooldown_seconds`，默认 20 秒，0 关闭）：Bot 任意回复后，窗口/Wait 计时到点若仍在静默期内会顺延到静默期结束才评估；@ 回复与关键词直达不受限制。

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
| `max_messages_per_reply` | `5` | 单次回复 Message 条数上限，超出保留前 N 条并记录日志 |
| `image_transcription_provider_id` | 空 | 可选多模态 Provider，用于图片转述与读图工具 |
| `image_cache_enabled` | `true` | 收到的图片进入插件缓存并按路径可重读 |
| `image_cache_max_entries` | `100` | 图片缓存上限，超出按最久未使用清理 |
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
| `proactive_post_reply_cooldown_seconds` | `20` | 回复后静默期（秒）；窗口/Wait 评估顺延到该时长结束，@ 与关键词直达不受限；`0` 关闭 |

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
node --check pages/humanize/app.js
```

## 工程结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期、请求注入、响应防火墙和实际发送追踪 |
| `humanize/image_cache.py` | 图片缓存：LRU 落盘、索引与按路径读取 |
| `humanize/context/` | Context Composer 与注入轨迹 |
| `humanize/memory.py` | HMAC 作用域、召回、提取、Provider Bridge 和后台 worker |
| `humanize/openviking/` | 内置 OpenViking workspace、写入、召回、Provider 和管理适配 |
| `humanize/repositories/` | SQLite 插件状态、任务、回复样例和审计 |
| `humanize/protocol/` | 标签构造、解析与失败通告 |
| `humanize/jargon/` | 黑话归一化、匹配和候选过滤 |
| `humanize/web/` | Dashboard Web API |
| `pages/humanize/` | 插件管理 WebUI |
| `tests/` | 协议、上下文、OpenViking、样例、存储和 WebUI 回归测试 |

长期开发规划位于 `PLAN.md`；README 只描述当前可运行行为。
