# AstrBot Humanize：内置 OpenViking 裁剪计划

> 目标：内置 OpenViking 作为唯一聊天记忆内核，删除旧 SQLite memory 及重复能力；插件只保留 AstrBot 适配、安全、协议、审计、任务和管理能力。

## 1. 固定基线

- 上游：`https://github.com/volcengine/OpenViking.git`
- Tag：`v0.4.9`
- Commit：`4f0bd86f32c5a98ed78e7ba04adb5708c0bdb89a`
- License：`AGPL-3.0`
- 私有命名空间：`humanize.vendor.openviking_core`
- 运行方式：内置源码、插件数据目录 workspace，不安装完整 wheel，不启动独立服务。

## 2. 保留范围

### OpenViking 内核

- Message、Session、Memory、merge/replace/patch/sum/immutable。
- L0/L1/L2、Memory Link 和聊天记忆分层召回。
- Identifier、Namespace、URI、时间、token 和必要文件工具。
- workspace 原子写、锁、损坏恢复、许可证、来源和修改记录。

### 插件能力

- AstrBot adapter、Provider Bridge、生命周期和 fail-open。
- 回复协议、Context Composer、发送安全和重复回复防护。
- scope、Agent、subject、HMAC 身份和召回后二次过滤。
- Trace、无正文审计、任务租约、黑话词库和回复样例。
- OpenViking 记忆管理、召回调试、任务状态、设置和 WebUI。

## 3. 删除范围

### OpenViking 上游多余模块

- server、Web Studio、HTTP API、CLI、远程 client 和 MCP 转换。
- AGFS、远程文件系统、独立锁和 transaction 服务。
- 独立 VectorDB、Qdrant、VikingDB、CuVS、本地模型和 GPU 管理。
- parse、ingest、resource、目录扫描和外部内容导入。
- Skill、Tool、Trajectory、Experience、Training、Eval 和 benchmark。
- 上游 Chat、Embedding、Rerank、VLM Provider、telemetry、metrics 和 queuefs。
- 未被白名单 import graph 引用的 docs、tests、数据集和二进制文件。

### 插件重复功能

- 旧 SQLite memory items、evidence、aliases、revisions、recall logs 和 vector index state。
- 旧 memory CRUD、提取写入、召回、embedding、rerank、FTS 和索引逻辑。
- 旧数据迁移、shadow write/read、cutover、回退和兼容配置。
- 自研 Session/archive、commit、L0/L1/L2、merge、diff、link 和分层展开。
- 回复、query embedding、召回结果和 rerank 结果缓存。
- Persona、State、Behavior、Expression、Relationship Memory 运行时逻辑。
- 多租户、Workflow、Resource Hub、Data Bank、知识库、导入器、Skill、Tool、Trajectory 和 Training。
- OpenViking 安装器、安装按钮、独立服务和 Web Studio 入口。
- 对应配置、API、页面、测试、文案和依赖。

旧 SQLite memory 数据不迁移、不保留回滚路径，升级初始化时直接删除。SQLite 仅继续保存插件任务、回复样例、审计和其他非记忆业务状态。

## 4. 安全与验收

- OpenViking 是唯一聊天记忆事实源，运行时不得读写旧 memory 表。
- workspace 只存聊天记忆；原始身份和密钥不得进入 workspace、日志或 WebUI。
- 召回后再次校验 scope、Agent、subject、状态和有效期。
- OpenViking 和 Provider 故障时返回空记忆，不阻断基础聊天。
- 不依赖顶层 `openviking`、AGFS、独立 VectorDB、本地模型或额外数据库服务。
- 单条聊天完成 `Session append -> commit -> memory diff -> L0/L1/L2 -> recall`，重试不产生重复数据。
- 管理 API/WebUI 只通过 OpenViking 管理适配器访问聊天记忆。
- 发布包包含许可证、上游版本、来源和修改说明。
- Python 测试、Ruff、JavaScript 静态检查和 `git diff --check` 通过。

## 5. Todo

### 进行中

- 无。

### 已完成

- 固定 OpenViking `v0.4.9` commit、AGPL-3.0、私有命名空间和 vendor 白名单。
- 内置 Message、Session、Memory、merge、Memory Link 和 L0/L1/L2 最小内核。
- 实现 workspace 原子写、跨实例锁、损坏恢复和重试幂等。
- 实现 AstrBot Provider Bridge、分层召回和读取后二次过滤。
- 实现 OpenViking 管理适配器、稳定记忆 ID、无正文审计和 Web API 接口。
- 聊天记忆读写和管理切换为 OpenViking 单一路径，故障时 fail-open。
- 删除旧数据迁移、shadow、cutover 和 SQLite recall fallback 过渡层。
- 删除旧 SQLite memory、重复运行时代码及残留 schema。
- 清理对应配置、API、WebUI 文案、测试、README 和依赖。
- 完成全量回归、发布包审计和最终提交。
