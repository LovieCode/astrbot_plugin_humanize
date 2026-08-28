# Humanize WebUI 实现计划（TODO）

> 状态：`[ ]` 未开始、`[~]` 进行中、`[x]` 已完成、`[!]` 阻塞/缺陷。
> 状态：**正式开发已基本完成**（2026-08-05）。7 页全部对接真实 API（commit a024cf6），后端补齐接口（commit 2984a2e~8d7bb78），SDK mock 测试通过。剩余为收尾验证与已知缺口。
> 数据契约以 `humanize/web/routes.py`、`humanize/ports.py`、`humanize/repositories/*`、`humanize/memory.py`、`humanize/config.py`、`humanize/domain/prompts.py` 为准。
> 每完成一项：更新状态、可勾选项与验证记录；未验证不得标记完成。

## 实现原则（用户已授权）

- **没接口就补**：WebUI 需要的字段/路由在后端缺失时，直接补接口（如 `POST settings`、审计查询、`pending_items`、`protocol_summary`）。
- **多余的接口就删**：后端有但 WebUI 不用的接口（如 `protocol-logs` 独立页并入上下文页后不再需要的路由）按需清理。
- **不符合要求就改**：契约与页面字段不一致时以后端真实能力为准修改页面或后端，改完同步更新本计划。
- 后端改动必须跑相关 pytest + Ruff format/check + `git diff --check`，前端跑 `node --check`。

---

## 0. 全局 / 架构

### G01 共享请求层

- [x] （已完成，见 commit a024cf6）
- [ ] 建 `shared/api.js`：统一 `HZ.api.get/post(path, query, body)`，返回 `data`（后端统一 `{success, data}` 包裹），错误码映射（400/403/404/409/500 + 后端中文 message）。
- [ ] 统一 loading / 空态 / 失败态组件：列表骨架屏、空态插画、错误条 + 重试。
- [ ] 统一分页组件：page/page_size 参数（后端上限 page_size≤100），`total/page` 驱动页码。
- [ ] 统一时间格式化：UTC ISO → 本地展示，相对时间（x 分钟前）。
- [ ] 统一 scope 筛选控件：`scope_type` + `scope_id`/`scope_token` 下拉（后端按 scope 过滤的页面都要用）。

### G02 共享路由与安全

- [x] （已完成，见 commit a024cf6）
- [ ] 挂载路径：`/api/plugin/humanize/...`（确认 `main.py` `register_web_api` 前缀）。
- [ ] 所有写操作走 POST，弹确认；删除/拒绝/停用二次确认。
- [ ] 全部渲染走 DOM 文本 API / `textContent`，持久化内容禁止拼 `innerHTML`（黑话释义、记忆正文、样例 turns、协议原文都算）。

### G03 共享样式

- [x] （已完成，见 commit a024cf6）
- [ ] 各页独有 CSS 与共享层最终对齐 tokens（粉色 `#f25c8f/#f4719d/#e5437b/#ec5c8c`，圆角、阴影）。
- [ ] 全站禁 CSS grid（必须 flex）；禁止 emoji 图标（统一 `shared/icons.js` 线性 SVG，20px/2px/圆头）。
- [ ] `.main` 不设 max-width，宽屏占满。

---

## 1. 仪表盘（dashboard.html）

- 后端：`GET overview` → `learned/pending/protocol_success_rate/protocol_samples/blocked_week/protocol_trend/action_distribution{Reply,No Reply}/context_stats{total_runs,average_tokens,omitted_runs}/scopes[]`

### D01 真实数据接入

- [x] （已完成）
- [ ] 顶栏日期由 mock 改为真实日期；hero 文案数字改为接口值（成功率、待审数、运行数）。
- [ ] 4 张统计卡绑定：已学词条=learned（角标 pending 待审）、协议通过率=protocol_success_rate（角标 protocol_samples 样本）、上下文运行=context_stats.total_runs（角标 omitted_runs 省略）、平均 tokens=average_tokens。
- [ ] 回复动作 7 天柱状图：用 `protocol_trend`（date/label/value/total），按日渲染 success=value% 高度 + 底部灰条表示失败。
- [ ] 回复占比环：`action_distribution` 计算 Reply 占比（注意 total=0 时显示空态，不除以 0）。
- [ ] 词库作用域分布：`scopes`（scope_type/scope_id/count），scope 名映射中文标签（global=全局，group=群聊，private_user=私聊用户，group_member=群成员）。
- [ ] 待审核词条卡：接口没有单独 pending 列表字段——待确认后端是否补 `pending_items`，否则改为只显示 pending 数字并跳转 jargon 页。
- [ ] 格式预览卡（协议样例）保留为静态说明，不需要接口。

### D02 交互

- [x] （已完成）
- [ ] 协议预览 tab（Reply/No Reply）保留现有前端切换，无接口。
- [ ] 导出按钮：无对应后端导出总览接口——确认后**移除**或改为导出 jargon（跳 jargon 页）。
- [ ] 卡片跳转链接对齐真实页面（已改：context.html / jargon.html）。

### D03 空/异常态

- [x] （已完成）
- [ ] 全部接口 7 天无数据时的空态（成功率 None、total=0、trend 全 0）。
- [ ] overview 请求失败时的错误条 + 重试。

---

## 2. 长期记忆（memory.html）

- 后端：`GET memory-status`、`memory-overview`、`memories`、`memory-detail`、`memory-agent-options`、`memory-jobs`；`POST memory-action`、`memory-recall-debug`。
- OpenViking 记忆字段：`id(memory_id 64hex)/agent_id/memory_type/profile|preference|entity|event/memory_key/content/structured_value/status(active|candidate|rejected|superseded)/confidence/importance/evidence[]/evidence_count/version/valid_from/valid_until/scope_token/scope_label/created_at/updated_at/uri`
- 作用域枚举：`global|private_user|group|group_member`；记忆状态枚举：`active|candidate|rejected|superseded`

### M01 记忆列表主区

- [x] （已完成）
- [ ] 调 `GET memories`（page/page_size/search/status/type/scope_type/agent_id 参数；scope 用 `scope_token`）。
- [ ] 数据源 Tab：长期记忆 走 memories；「会话提交」页当前无对应接口——确认后**移除**或改为「后台任务」（`GET memory-jobs`）。
- [ ] 状态筛选 seg 对齐枚举：active/candidate/rejected/superseded。
- [ ] 类型筛选：profile/preference/entity/event。
- [ ] Agent 筛选：`GET memory-agent-options`（返回 configured/observed 合并项，default_id 置顶，`*`=共享记忆、`_chatui_default_`=WebChat 默认人格）。
- [ ] 作用域筛选：`GET memory-overview` 的 `scope_options`（已含全局 + HMAC token），选中后带 `scope_token` 请求。
- [ ] 卡片字段：memory_key、content 摘要、memory_type 标签、status 标签、scope_label、confidence/importance、version、updated_at。
- [ ] 分页：total/page/page_size（page_size≤100）。

### M02 记忆详情抽屉

- [x] （已完成）
- [ ] 调 `GET memory-detail?id=<id>`（id 为 64hex 或旧整数）。
- [ ] 展示：完整 content、structured_value（JSON 折叠展示）、confidence/importance、valid_from/valid_until、agent_id/memory_type/scope_label、version、uri。
- [ ] evidence 列表：quote/occurred_at/source_request_id/source_complete。
- [ ] revisions（memory_diffs + history manifest）+ audit（memory_admin JSON：action/actor/reason/before_hash/after_hash/created_at/version）。
- [ ] 编辑：弹窗内改 content/confidence/importance/status/valid_until，提交 `POST memory-action`（action=update，带 id/revision 乐观锁，409 冲突提示）。
- [ ] 状态操作：activate/approve/confirm（→active）、reject（→rejected）、delete（→rejected + 理由），均带二次确认 + reason。
- [ ] 新建：弹窗选 scope_token（必须，后端 400 校验）+ agent_id/memory_type/memory_key/content/confidence/importance，action=create。

### M03 召回测试

- [x] （已完成）
- [ ] 输入 query + scope_token + agent_id + type 过滤，`POST memory-recall-debug`。
- [ ] 展示返回 items（召回详情）+ content（将注入的 XML 片段，代码块展示）+ included 布尔。
- [ ] 校验：scope_token 必填、agent_id 不能为 `*`（后端 400）。

### M04 空/异常态

- [x] （已完成）
- [ ] 记忆服务未初始化（`memory-status.state != ready`）时全页引导态，展示 reason 与 openviking_state。
- [ ] memory 接口 409（服务未初始化）错误提示。
- [ ] 列表空态、搜索无结果。

---

## 3. 黑话词库（jargon.html）

- 后端：`GET jargons`、`jargon-detail`、`jargon-export`；`POST jargon-action`。
- 词条字段：`id/term/normalized_term/scope_type/scope_id/status/occurrence_count/confidence/last_seen_at/enabled/match_mode/case_sensitive/preferred_sense{alias_count/sense_count/verified_sense_count/pending_sense_count/has_conflict}`
- 义项枚举：`candidate|provisional|verified|ambiguous|rejected`；状态筛选取值：`verified/pending(candidate+provisional)/conflict/disabled/rejected/candidate/ambiguous`
- action 白名单：`confirm/reject/update/delete/update_entry/replace_aliases/create_sense/update_sense/confirm_sense/reject_sense/set_preferred/merge_sense/delete_sense`（+带下划线别名）

### J01 列表主区

- [x] （已完成）
- [ ] 调 `GET jargons`（search/status/scope_id/scope_type/page/page_size）。
- [ ] 状态 seg：全部/待审核(pending)/已验证(verified)/冲突(conflict)/已停用(disabled)/已拒绝(rejected)。
- [ ] 作用域筛选：`GET jargons` 的 scope_id/scope_type（jargon 的 scope 是明文 scope_id，非 HMAC token）。
- [ ] 卡片：term、meaning（preferred_sense.meaning）、status 标签、scope 标签、occurrence_count、confidence、alias_count/sense_count、enabled 停用态。
- [ ] 分页 + 搜索（term/别名/释义模糊）。

### J02 详情抽屉

- [x] （已完成）
- [ ] 调 `GET jargon-detail?id`。
- [ ] 展示：entry 全字段、aliases[]、senses[]（is_preferred/status/confidence/version/created_by/reason/evidence_count）、evidence[]（source_text/message_id/sender_id/observed_at/valid）、inferences[]（proposed_meaning/confidence/reason/accepted/created_at）、injections[]（request_id/scope_id/selected/reason/created_at）。
- [ ] 义项操作（每条 sense 的按钮 → `POST jargon-action` + id）：
  - confirm_sense（→verified，可 preferred）
  - reject_sense（→rejected）
  - set_preferred（必须是 verified，否则后端 400）
  - update_sense（弹窗改 meaning/confidence）
  - merge_sense（source_sense_id→target_sense_id，二次确认）
  - delete_sense
- [ ] 词条级操作：
  - confirm（激活首选义项为 verified）
  - reject（整条 rejected，二次确认 + reason）
  - update（改首选义项 meaning，→verified）
  - update_entry（term/enabled/match_mode/case_sensitive 编辑）
  - replace_aliases（别名编辑，整表替换）
  - delete（物理删除，二次确认，仅未使用词条？确认后端约束）
- [ ] 停用/重新启用：update_entry(enabled=0/1)。

### J03 导出

- [x] （已完成）
- [ ] `GET jargon-export`（带当前筛选），下载 JSON（schema_version=2 + items 含 detail）。

### J04 空/异常态

- [x] （已完成）
- [ ] 无词条空态；搜索无结果；加载失败重试。
- [ ] 后端 400（含义/别名/scope 冲突、preferred 未 verified）错误提示。

---

## 4. 回复样例（examples.html）

- 后端：`GET reply-examples`、`reply-example-detail`；`POST reply-example-action`、`reply-example-recall-debug`。
- 字段：`id/example_id/title/topic/intent/style_tags/keywords/turns[{role,content}]/ideal_reply/conditions/exclusions/notes/status(draft|approved|rejected|tombstoned)/enabled/quality_score/source_type/manual|extracted|learned/source_context_run_id/revision/version/content_hash/scope_token/scope_label/agent_id/created_at/updated_at/deleted_at`
- action 白名单：`create/update/approve/reject/delete/tombstone/restore/enable/disable`（save 映射）。

### E01 列表主区

- [x] （已完成）
- [ ] 调 `GET reply-examples`（search/status/scope_type/scope_token/agent_id/topic/intent/enabled/page/page_size）。
- [ ] 状态 seg：全部/draft/approved/rejected/tombstoned。
- [ ] 筛选：作用域 scope_token、agent_id（复用 memory-agent-options）、topic/intent 搜索、启用态开关。
- [ ] 卡片：title、turns 气泡预览、ideal_reply、status 标签、quality_score、style_tags、enabled、scope_label、updated_at。
- [ ] 分页 + 搜索（title/topic/intent/keywords/style_tags/turns/ideal_reply 模糊）。

### E02 详情抽屉

- [x] （已完成）
- [ ] 调 `GET reply-example-detail?id`。
- [ ] 展示：完整 turns、ideal_reply、conditions/exclusions/notes、keywords/style_tags、quality_score、source_type/source_context_run_id、revision、audit[]（before/after JSON 折叠）、usage[]（request_id/score/rank/selected/candidate_count/duration_ms/reason/created_at）、embeddings[]（provider/model/dimension/generation/updated_at，不含 vector）。
- [ ] 编辑：弹窗改 title/topic/intent/turns(1-3)/ideal_reply/keywords/style_tags/conditions/exclusions/notes/quality_score，`POST reply-example-action`（action=update，带 id/revision 乐观锁）。
- [ ] 状态操作：approve（→approved+enabled）、reject（→rejected）、tombstone（→tombstoned）、restore（→draft）、enable/disable、delete（物理删除，二次确认）。
- [ ] 新建：scope_token 必填（后端 400）+ agent_id + turns(1-3 校验) + ideal_reply，action=create；创建后若配置 embedding 会自动异步 embed。

### E03 召回测试

- [x] （已完成）
- [ ] 输入 query + scope_token + agent_id，`POST reply-example-recall-debug`。
- [ ] 展示 items + content（渲染后的 Examples XML）+ included。

### E04 空/异常态

- [x] （已完成）
- [ ] 无样例空态；搜索无结果；加载失败重试。
- [ ] 400（turns 数量、scope 缺失、quality 越界、revision 冲突 409）。

---

## 5. 上下文追踪（context.html，含协议日志）

- 后端：`GET context-runs`、`context-run`、`context-stats`、`protocol-logs`；本页左侧合并协议日志。
- run 字段：`id/request_id/scope_type/scope_id/message_id/sender_id/protocol_mode/estimated_tokens/included_sections/omitted_sections/created_at`
- 区块枚举：`current_message|known_terms|memory_context|reply_examples|response_protocol`（source_type：message/repository/memory/reply_examples/protocol）
- detail：`run/request_snapshot{provider_request,snapshot_complete}/response_snapshot{llm_response,protocol,success/action/failure_code/failure_detail/model/duration_ms/raw_output}/sections[]/snapshot{run,sections}/response`
- stats：`days/runs/total_tokens/average_tokens/sections[{section_key,occurrences,included,omitted,average_tokens,total_applied_tokens,total_items}]/reasons[{section_key,reason,count}]`

### C01 运行列表

- [x] （已完成）
- [ ] 调 `GET context-runs`（scope_type/scope_id/section_key/page/page_size）。
- [ ] 协议结果标签：run 的协议结果来自 `protocol-logs`（按 request_id 关联）或 `context-run` 的 response——确认列表接口是否要补 `protocol_summary`，否则按 request_id 并发查 protocol-logs 折叠。
- [ ] 筛选：scope_type seg、section_key seg、注入模式（protocol_mode: user/both）。
- [ ] 卡片精简：request_id 短显、scope_label、estimated_tokens、included/omitted_sections、耗时（来自关联 protocol）、created_at。
- [ ] 分页 + 分页态。

### C02 详情

- [x] （已完成）
- [ ] 调 `GET context-run?request_id=`。
- [ ] 顶部概览胶囊：scope、message_id、sender_id、protocol_mode、estimated_tokens、included/omitted、耗时、model、created_at。
- [ ] Token 预算分布条：sections[] 的 estimated/applied/budget_tokens。
- [ ] 5 区块时间线（按 ordinal）：section_key/优先级/source_type/targets/required/included/reason/estimated/applied/budget/item_count/content 预览（preview_truncated）+「查看原文」展开 content（snapshot_complete 标记）。
- [ ] 原始上下文卡：request_snapshot.provider_request（逐条 role+content，参考 llm_debugger 格式）+ response_snapshot.llm_response + 协议元信息（success/action/failure_code/failure_detail/model/duration_ms/raw_output）+ 失败警示条（failure_code）。
- [ ] 复制 request_id 按钮（已有图标 copy）。

### C03 统计

- [x] （已完成）
- [ ] 调 `GET context-stats?days=`（默认 7，可切 7/14/30）。
- [ ] 展示 runs/total_tokens/average_tokens、sections 汇总（occurrences/included/omitted/average_tokens/total_items）、reasons 分布。

### C04 协议日志合并

- [x] （已完成）
- [ ] `GET protocol-logs`（page/page_size）列表：request_id/success/action/failure_code/failure_detail/model/duration_ms/stage(final|tool)/is_final/created_at。
- [ ] 与 context-runs 按 request_id 合并展示或独立子 Tab；is_final 过滤「只显示最终阶段」。

### C05 空/异常态

- [x] （已完成）
- [ ] 无运行记录空态；detail 404 提示；加载失败重试。

---

## 6. 提示词模板（prompts.html）

- 后端：`GET prompt-templates`；`POST prompt-templates`（action=update/reset，key 或 all；body 可传 `templates` 整包或 `key+content`；必带 reason）。
- 模板 key：`rule/protocol/repair/memory_extraction/reply_examples`；变量 `{{name}}`，required_variables 校验在后端（未知变量/缺必需变量会 400）。
- items 结构：`key/label/description/content/default_content/variables/required_variables/updated_at`

### P01 模板列表与编辑

- [x] （已完成）
- [ ] 调 `GET prompt-templates` 渲染 5 模板（items 顺序即展示顺序）。
- [ ] 变量芯片：从 variables 生成 `{{name}}` 可点击复制（已有 copy 图标）。
- [ ] 字数统计（charCount）、脏标记（dirty）、保存按钮态。
- [ ] 保存：`POST prompt-templates`（action=update，key+content+reason），成功后回写 items/updated_at，清 dirty。
- [ ] 恢复默认：action=reset（key 或 all），二次确认 + reason（默认「恢复默认模板」）。
- [ ] 修改审计区：目前是静态 mock——需确认后端是否提供审计查询接口（`humanize_prompt_template_audit` 表目前**无 GET 路由**），若无则隐藏或标注「仅本次会话记录」。

### P02 空/异常态

- [x] （已完成）
- [ ] 加载失败重试；保存 400（未知变量/缺必需变量）展示后端 message。
- [ ] 恢复默认后审计记录刷新。

---

## 7. 设置（settings.html）

- 后端：`GET settings`（`PluginConfig.as_public_dict()`，只读）；**无保存接口**。
- 配置 key 全表见 `humanize/config.py as_public_dict()`：enabled/default_rule_enabled/admin_name/admin_qq_ids/max_message_chars/message_interval_seconds/protocol_enabled/protocol_injection_mode(user|both)/protocol_version/protocol_repair_retry_enabled/protocol_log_retention_days/no_reply_enabled/jargon_enabled/min_confidence_for_injection/max_injected_jargons/memory_enabled/memory_auto_extract_enabled/memory_extraction_provider_id/memory_embedding_provider_id/memory_rerank_provider_id/memory_identity_secret_env/memory_recall_timeout_seconds/memory_auto_activate_confidence/memory_candidate_min_confidence/memory_recall_limit/memory_recall_score_threshold/memory_recall_max_chars/memory_extract_batch_turns/memory_extract_idle_seconds/memory_job_max_attempts/reply_examples_enabled/reply_examples_limit/reply_examples_max_chars/reply_examples_min_quality/reply_examples_recall_score_threshold

### S01 数据接入

- [x] （已完成）
- [ ] 调 `GET settings` 回填全部控件（6 组：常规/回复协议/黑话注入/长期记忆/回复样例/服务商）。
- [ ] 顶部标签导航（已做真 Tab 分页）保留，按组只显示一组。

### S02 保存（需后端支持）
- [ ] **阻塞项**：后端当前无 `POST settings`。需新增路由：校验白名单 key → 通过 `astrbot.core.star.config.update_config(namespace, key, value)` 或 `AstrBotConfig.save_config` 写入插件配置 → 热重载插件（`plugin_manager.reload`）或提示重启生效。
- [ ] 保存按钮：脏检测 → 提交 → 成功提示 + 刷新 `GET settings`。
- [ ] 恢复默认：无后端接口——确认后仅提示「AstrBot 配置面板可重置」或补接口。

### S03 服务商组

- [x] （已完成）
- [ ] `GET chat-providers`（state/providers[]：id/adapter/model/model_revision/capability）——prompt_cache_capability 实际来自 `provider_identity`（implicit/explicit/unsupported/unknown），显示徽标。
- [ ] `GET memory-providers`（chat/embedding/rerank 三组，字段 id/adapter/model/provider_type）。
- [ ] 空态：state=not_initialized/error 时引导提示。

### S04 空/异常态

- [x] （已完成）
- [ ] settings 加载失败重试；保存失败展示后端 message。

---

## 8. 收尾 / 验收

### F01 正式接入前
- [ ] 确认 Web 页面实际挂载方式（`_PAGE_FILES` 目前只映射 index.html/style.css/app.js——正式版需把 7 页 + shared 静态资源一并注册，或改用 AstrBot 静态目录服务）。
- [ ] 确认前端框架选型：当前预览为原生 JS + 共享层；正式版沿用（不引框架），或按用户决定。

### F02 验证清单（每项完成后在对应节勾选）
- [ ] `node --check pages/humanize/shared/views/*.js shared/*.js`（按 AGENTS.md 要求）。
- [ ] 全部页面 Edge 无头截图自查 + 清理 `_shot_*`/`_crop_*` 临时文件。
- [ ] div 开闭平衡校验脚本通过。
- [ ] 后端改动（routes/settings 保存）跑相关 pytest + Ruff format/check + `git diff --check`。
- [ ] 窄屏（760px）与长文本冒烟。

### F03 待补齐接口清单（按「没接口就补」原则，实现时逐项完成）
- [ ] `POST settings`：白名单 key 校验 → `astrbot.core.star.config.update_config` 写入 → 插件热重载或提示重启（阻塞 S02）。
- [ ] 仪表盘 `overview` 补 `pending_items`（待审核词条列表，阻塞 D01 最后一项）。
- [ ] 记忆页「会话提交」Tab：改为后台任务视图（复用 `GET memory-jobs`，阻塞 M01）。
- [ ] `context-runs` 列表补 `protocol_summary`（协议结果标签，阻塞 C01）。
- [ ] 新增 `GET prompt-template-audit`（审计查询路由，阻塞 P01 审计区）。
- [ ] 仪表盘「导出」按钮：改为跳转 jargon 导出页或移除（阻塞 D02）。
- [ ] 黑话 `delete` 约束：确认是否限制为从未注入/未使用的词条，按需加后端校验（J02 备注）。

---

## 9. 单入口 SPA 重构（2026-08 当前）

### 背景：AstrBot 插件页面机制研究结论
- **多文件完全支持**：一个 Page 目录 = index.html 入口 + 任意相对资源（css/js/img），官方文档示例 `bridge-demo/` 即 index.html + app.js + style.css + assets/。
- **一个 Page 嵌套加载另一个 Page 的 HTML 不是官方模式**：Dashboard 前端只加载入口页（content_path 带 asset_token），嵌套 iframe 子页无 token、JS 动态 src 也不重写 → 401。JWT cookie 虽 SameSite=strict 同站可带，但嵌套 iframe 会破坏 bridge 的 postMessage 目标。
- **多页 = 多个 Page 目录**（pages/<name>/index.html）。但用户要「单入口」，故用**构建式 SPA**：7 个独立页保持源码，构建脚本合并生成单一 index.html。
- **官方认证链路**：页面 HTML 自动注入 bridge-sdk.js；API 调用走 `window.AstrBotPluginPage.apiGet/apiPost(endpoint)`（endpoint 为插件内相对路径，如 `stats`），父页面带登录态转发到 `/api/v1/plugins/extensions/<plugin>/<path>`，匹配 `register_web_api(f"{PLUGIN_NAME}/<path:subpath>")`。后端 `json_response(data)` → bridge resolve 完整 JSON；`error_response(msg)` → bridge reject Error。**禁用原生 fetch/EventSource**（不带认证）。

### 方案（构建式 SPA）
- `scripts/build_spa.py`（已写）：从 7 个 HTML 提取 `<main>` 内容 → 合并生成 `pages/humanize/index.html`；跨页重复 id 自动检测（FATAL 阻止构建）；共享 CSS/script 只保留 1 份；注入 `.view` 显隐 + app.js。
- `pages/humanize/app.js`（已写）：侧边栏渲染一次 + 视图切换（`HZ.views.<name>.init()` 按需调用、防重复）。
- `shared/api.js`：bridge 优先（endpoint 相对路径），无 bridge fallback fetch（独立预览）。
- 视图 JS 双模式：IIFE 包进 `init()`，`HZ.views` 存在则注册、否则立即执行（独立页可单独打开）。
- id 前缀：drawer/recall 系跨页冲突加 `mem-`/`jg-`/`ex-` 前缀（HTML + JS 同步）。
- `scripts/serve_spa.py`（已写）：本地模拟 AstrBot 页面机制（重写资源 + mock bridge 代理真实后端），用于端到端验证。

---

## 10. 单入口 SPA 构建与部署规范（定稿）

### 结构（源码/产物分离）
- `webui/<view>/`（7 个独立页源码，编程改这里，可独立预览）
- `pages/humanize/`（**构建产物**，唯一部署物，AstrBot 只发现这一个页面）
- `scripts/build_spa.py`（源码 → 产物；`--check` 校验产物不过期）
- `scripts/smoke_spa.py`（Playwright + mock bridge 端到端冒烟）
- `.astrbot-plugin/i18n/zh-CN.json`（页面显示名）

### 构建规则（build_spa.py 自动处理）
1. 提取每页 `<main>` + body 级弹层（drawer/modal）→ 7 个 `<section class="view" id="view-<name>">`
2. **跨页重复 id 自动加前缀**（`mem-`/`jg-`/`ex-`/`db-`/`cx-`/`pt-`/`st-`），HTML/JS/CSS 同步（含 `$("id")` 形式）；`sidebar`/`topbar` 全局唯一保留
3. 视图 JS IIFE 自动包装为 `HZ.views["<name>"].init`（懒加载）；删除视图内 `renderSidebar` 调用（app.js 统一渲染）
4. `shared/ui.js` 的 nav href 中和为 `#`（app.js 事件委托切换）
5. `webui/app.js` → 产物 `app.js`（侧边栏一次 + 视图切换 + 防重复 init）

### 部署规范（deploy_hotfix.sh）
- 改 `webui/` 或 `scripts/build_spa.py` → 脚本**自动重建产物**并纳入部署清单
- 部署后**插件热重载**（`POST /api/v1/plugins/reload` + API key，`.deploy.local.md` 的 `API key` 字段），**不重启 AstrBot**（除非 `--restart`）
- 改后端 `.py` → 自动热重载；`--restart` 才重启服务
- 使用：`bash scripts/deploy_hotfix.sh --pytest <相关测试> -- <改动文件...> [<测试文件>]`

## 11. 美化与交互优化待办（2026-08-28 本地实测发现）

> 来源：本地 WebUI（127.0.0.1:6185）实际巡检。均为非阻断瑕疵，随下批改动顺带处理。

- [ ] 仪表盘问候语不随时间变化：23:10 仍显示"早上好"，应按当前时段（凌晨/上午/下午/晚上）选择问候。
- [ ] 仪表盘 hero 日期为写死的 mock（显示"2026年3月30日 星期一"），应改为真实日期（WEBUI_TODO G01 旧项的遗留）。
- [ ] 仪表盘侧栏底部"距离件 36 天"文案不通顺（疑似"已陪伴/部署"截断），核实源字符串并修正。
- [ ] 插件侧边栏出现横向滚动条（窄容器内容溢出），压缩内边距或截断长项。
- [ ] 记忆页作用域筛选 chips 只显示 scope_type，多条"全局/群聊"无法区分，应附加短 scope_id 标识。
- [ ] 提示词模板列表项 label 与 key 文案重复（"基础规则 基础规则"），相同时隐藏 key 小标签。
- [ ] 上下文追踪页筛选区提示文案在窄屏被裁切（"注入模式…仅展示"截断）。
