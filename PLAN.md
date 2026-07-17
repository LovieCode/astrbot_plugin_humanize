# AstrBot Humanize：内置 OpenViking 裁剪计划

> 目标：以内置源码方式使用 OpenViking 聊天记忆内核，由插件保留 AstrBot 适配、安全、协议、审计和管理能力。迁移验证完成前，不删除旧 `ChatMemoryService` 或历史数据。

## 1. 固定基线

- 仓库：`https://github.com/volcengine/OpenViking.git`
- Tag：`v0.4.9`
- Commit：`4f0bd86f32c5a98ed78e7ba04adb5708c0bdb89a`
- License：`AGPL-3.0`
- 私有命名空间：`humanize.vendor.openviking_core`
- 运行方式：不安装完整 `openviking` wheel，不启动独立服务，不引入第二个数据库。

## 2. 裁剪边界

### 2.1 OpenViking 保留

- `core/`：Identifier、Namespace、路径与 URI 基础能力。
- `message/`：聊天消息模型。
- `session/`：Session、commit 和聊天记忆数据模型。
- `memory/`：Memory 文件、类型、关系和生命周期。
- `session/memory/merge_op/`：merge、replace、patch、sum 和 immutable。
- `session/memory/utils/`：L0/L1/L2、链接和必要文件工具。
- `retrieve/`：聊天记忆分层召回所需的最小内核。
- `utils/`：被上述模块直接依赖的时间和 token 工具。
- AGPL-3.0、上游来源和修改记录。

只允许白名单 vendor；新增上游文件必须通过 import graph 和禁用导入扫描。

### 2.2 OpenViking 删除

- `server/`、`web_studio/`、HTTP API、CLI、远程 client 和 MCP 转换。
- `pyagfs/`、AGFS、远程文件系统、独立锁和 transaction 服务。
- `vectordb/`、Qdrant、VikingDB、CuVS 及其他独立向量服务。
- `parse/`、`ingest/`、`resource/`、目录扫描和外部内容导入。
- Skill、Tool、Trajectory、Experience、Training、Eval 和 benchmark。
- OpenViking 自带 Chat、Embedding、Rerank、VLM、本地模型和 GPU 管理。
- telemetry、metrics、独立 observability、queuefs 和平台管理服务。
- 上游 docs、tests、数据集、二进制文件及未被适配层引用的模块。

### 2.3 插件保留

- AstrBot adapter、Provider Bridge、生命周期和故障降级。
- 回复协议、Context Composer、发送安全和重复回复防护。
- scope、Agent、subject、HMAC 身份和读取后二次过滤。
- Trace、审计、任务租约和迁移元数据。
- 黑话词库、回复样例及其人工审核入口。
- 运行总览、记忆管理、召回调试、任务状态和设置页面。

### 2.4 插件迁移后删除

- `ChatMemoryService` 内与 OpenViking 重复的提取、更新和召回语义。
- 自研 Session/archive、commit、L0/L1/L2、merge、diff、link 和分层展开。
- worker 中重复的 commit/extract 流程。
- 旧 archive 专用链路和重复记忆索引逻辑。
- LLM 回复、query embedding、召回结果和 rerank 结果缓存。
- FAISS、Qdrant、VikingDB、CuVS、本地模型及插件自建 Provider。
- Persona、State、Behavior、Expression、Relationship Memory 的运行时逻辑。
- 多租户、Workflow、Resource Hub、Data Bank、知识库、导入器、Skill、Tool、Trajectory 和 Training。
- OpenViking 安装器、安装按钮、独立服务和 Web Studio 入口。
- 对应的配置、API、页面、测试和依赖。

历史表和配置先停止运行时读写并保留迁移窗口，不直接删除用户数据。

## 3. 安全底线

- workspace 只保存聊天记忆数据，位于插件数据目录内，不引入额外数据库。
- 原始身份标识和密钥不得进入 workspace、日志、WebUI 或发布包；身份统一使用 HMAC 派生值。
- 读取后必须再次校验 scope、Agent、subject、状态和有效期。
- vendor 不创建 Provider 客户端，只通过 AstrBot Provider Bridge 调用模型。
- OpenViking 或 Provider 故障时返回空记忆，不阻断基础聊天。

## 4. 实施顺序

1. **内核与 workspace**：固定版本、完成白名单 vendor、许可证、路径约束、原子写、锁和损坏恢复。
2. **Session 与 Memory 写入**：实现 append/commit、memory upsert、diff、link 和 L0/L1/L2；先 shadow write，旧 Repository 仍为正式写入路径。
3. **召回与 Provider**：实现 Provider Bridge 和 Recall Adapter；shadow read 对比旧服务，并执行读取后二次过滤。
4. **迁移与切换**：把旧 memory、evidence 和 revision 可重复地迁移到 workspace；校验通过后再切换正式读写，旧表保留作回滚来源。
5. **清理与发布**：删除不再使用的旧代码及其配置、API、页面、测试和依赖，完成发布验证。

任何阶段失败都继续使用旧路径或空记忆，不影响回复。不得跳过 shadow write、shadow read 和迁移校验直接删除旧实现。

## 5. 验收标准

- 单条聊天完成 `Session append -> commit -> memory diff -> L0/L1/L2 -> recall`。
- 同一操作重试不产生重复数据。
- 不依赖顶层 `openviking`、AGFS、独立 VectorDB、本地模型或额外数据库。
- scope、Agent、subject 读取后过滤无泄漏。
- workspace 损坏、Provider 超时和非法响应不阻断聊天。
- Embedding/Rerank 不可用时可降级到层级和关键词召回。
- 现有回复协议、上下文标签和发送闸门行为不变。
- 发布包包含许可证、上游版本、来源和修改说明。
- Python 测试、Ruff、JavaScript 静态检查和 `git diff --check` 通过。

## 6. Todo

### 进行中

- 完成旧数据全量迁移与逐项校验，保留旧表作为回滚来源。
- 完成管理 API/WebUI 的 OpenViking 切换，并停止旧记忆表全部运行时读写。
- 删除插件重复功能及对应配置、API、页面、测试和依赖，完成发布检查。

### 已完成

- 固定 OpenViking `v0.4.9` commit、AGPL-3.0 和私有命名空间。
- 完成 import graph 审计、文件级白名单和平台依赖裁剪。
- 内置 Message、Memory、merge/replace/patch/sum/immutable、Memory Link 和 L0/L1/L2 基础内核。
- 实现受控 workspace、manifest、原子写、跨实例锁和损坏恢复。
- 实现 Session append/commit、稳定消息 ID、deterministic fallback 和崩溃重试幂等。
- 实现 Memory upsert、diff、derived-from link、低置信度 keep 和中断重试幂等。
- 接入 worker shadow write，保留旧 Repository 正式写入并实现 OpenViking fail-open。
- 实现 Provider Bridge、Recall Adapter、shadow read、分层召回和读取后最终过滤。
- 实现旧 memory、evidence、revision 的幂等迁移、历史页校验、后台分页和无正文审计。
- 实现稳定主键迁移扫描、shadow read 连续一致门槛、持久化 cutover 和聊天 runtime 正式读写切换。
