# AstrBot Humanize：内置 OpenViking 裁剪计划

> 目标版本：OpenViking 0.4.9。当前插件代码是过渡基线；本计划完成后，OpenViking 承担聊天记忆核心，插件只负责 AstrBot 适配、安全、协议、审计和管理面。

固定上游基线：

- 仓库：`https://github.com/volcengine/OpenViking.git`
- Tag：`v0.4.9`
- Commit：`4f0bd86f32c5a98ed78e7ba04adb5708c0bdb89a`
- License：`AGPL-3.0`

## 1. 最终目标

把 OpenViking 的聊天记忆核心以内置源码方式放入插件，不安装完整 `openviking` wheel，不启动独立服务，不引入第二个数据库。

最终链路：

```text
AstrBot 消息
  -> 插件构造 Session
  -> OpenViking 归档完整经历
  -> OpenViking 提取/更新 Memory
  -> 生成 L0/L1/L2
  -> 按 scope、Agent、subject 过滤并召回
  -> Context Composer 注入 <MemoryContext>
  -> LLM 回复
  -> 发送成功后提交下一轮 Session 任务
```

## 2. 最终边界

### 2.1 OpenViking 负责

- Session：保存完整聊天经历、消息和 commit。
- Memory：提取、创建、修改、合并、替代和删除记忆。
- Memory Diff：记录每次记忆变化及其来源。
- Memory Link：维护必要的 `related_to`、`derived_from`、`contradicts` 等关系。
- L0/L1/L2：生成摘要、概览和完整内容，支持按需读取。
- URI/Namespace：提供记忆页面和关系的内部定位。
- 分层召回：先找候选摘要，再按需展开完整内容。

### 2.2 插件负责

- AstrBot 事件接入、插件生命周期和热重载。
- `Reply / No Reply` 协议、发送闸门和重复回复防护。
- `<Msg>`、规则、黑话和记忆上下文注入。
- Agent、私聊、群聊、群成员的作用域和 HMAC 身份映射。
- Chat、Embedding、Rerank Provider 适配。
- `humanize.db` 中的任务租约、审计、Context Trace 和迁移元数据。
- WebUI 管理、召回调试和错误状态展示。
- fail-open：记忆系统失败不能阻断基础聊天。

## 3. OpenViking 裁剪清单

### 3.1 保留并改造

- `session/`：只保留聊天 Session、消息归档和 commit。
- `session/memory/`：保留聊天记忆提取、更新、merge、patch、replace 和 diff。
- `retrieve/`：保留层级检索、摘要候选和按需读取。
- `service/`：只保留记忆更新与召回所需服务代码。
- `core/`：保留 URI、Namespace、Context、Identifier 和路径校验。
- `message/`：保留用户、助手、工具和附件的统一消息模型。
- `memory/`：保留类型注册、策略、关系和必要工具。
- `crypto/`：只保留 workspace 身份隔离所需的最小文件，复用 AstrBot 已有依赖。

### 3.2 直接裁剪

- `server/`、`web_studio/`、FastAPI、HTTP API、CLI。
- `client/`、异步远程客户端、MCP 转换和跨进程协议。
- `pyagfs/`、AGFS、远程文件系统和独立锁服务。
- `vectordb/`、`vectordb_adapters/`、Qdrant、VikingDB、CuVS 和独立向量服务。
- `parse/`、`ingest/`、目录扫描、Git/HTTP/Feishu 导入。
- `resource/`、通用文件资源、知识库和外部内容平台。
- `skill/`、Tool、Trajectory、Experience、Skill Hub。
- `models/embedder/`、`models/rerank/`、`models/vlm/` 的模型注册和客户端实现。
- `metrics/`、`telemetry/`、`observability/` 的独立采集系统，改接 AstrBot 日志和现有 Trace。
- `eval/`、`train/`、benchmark、数据集和训练工具。
- `queuefs/`、semantic sidecar、重复任务队列和远程 transaction 服务。
- `pack_service.py`、`resource_service.py`、`vikingdb_manager.py` 等平台管理代码。

### 3.3 内置目录

```text
astrbot_plugin_humanize/
├─ humanize/
│  └─ vendor/
│     └─ openviking_core/
│        ├─ session/
│        ├─ memory/
│        ├─ retrieve/
│        ├─ core/
│        ├─ message/
│        └─ LICENSES/
└─ data/                         # 受控 OpenViking workspace
```

vendor 使用私有命名空间 `humanize.vendor.openviking_core`，禁止运行时依赖顶层 `openviking` 包。

### 3.4 文件级白名单

首批只从上游复制并改造以下聊天记忆内核；文件内导入统一改为私有命名空间：

- `core/identifiers.py`、`core/peer_id.py`、`core/namespace.py`。
- `message/part.py`、`message/message.py`。
- `session/memory/dataclass.py`。
- `session/memory/merge_op/` 下的 `base.py`、`factory.py`、`immutable.py`、`link_merge.py`、`patch.py`、`patch_handler.py`、`replace.py`、`sum.py`。
- `session/memory/utils/` 下的 `line_numbers.py`、`link_renderer.py`、`memory_file_utils.py`、`messages.py`、`model.py`、`resource_refs.py`、`template_utils.py`、`uri.py`。
- `retrieve/memory_lifecycle.py`。
- `utils/time_utils.py`、`utils/token_estimation.py`。

`session/session.py`、`session/memory/extract_loop.py`、`session/memory/memory_updater.py` 和 `retrieve/hierarchical_retriever.py` 不直接复制。它们的上游静态 import 闭包会把被裁剪的平台模块重新带回，必须按现有语义拆成插件适配层，并只调用上述内核。

基线 import graph 审计结果：以上游 `message`、`session.session`、`extract_loop`、`memory_updater`、`hierarchical_retriever` 为入口，闭包共 307 个 Python 文件，其中包含 `server` 19 个、`pyagfs` 3 个、`storage` 49 个、`models` 10 个、`telemetry` 7 个、`session.train` 27 个、`session.skill` 4 个和 `openviking_cli` 24 个文件。该闭包不作为 vendor 清单；vendor 采用白名单并对每个新增文件执行禁用导入扫描。

## 4. 插件自身裁剪清单

### 4.1 保留

- 回复协议和发送安全。
- AstrBot 适配、Provider Bridge 和生命周期。
- scope、Agent、HMAC 身份和最终过滤。
- Context Composer、Context Trace、协议审计。
- 任务租约、重试、迁移和错误降级。
- 黑话词库的人工审核体验。
- 回复样例的人工审核、脱敏和 few-shot 注入。
- 运行总览、记忆管理、召回调试、任务状态和设置页面。

### 4.2 并入 OpenViking，不再双维护

- `ChatMemoryService` 中的提取语义。
- 插件自研 Session/archive 和 session commit。
- 自研 L0/L1/L2 摘要、页面和分层读取。
- 自研 memory merge、replace、patch、diff 和 link。
- 自研记忆召回排序、分层展开和来源关联。
- 记忆 embedding/vector 的核心索引逻辑。
- 后台 worker 中与 OpenViking commit/extract 重复的逻辑。

插件保留兼容 facade、任务租约、作用域过滤和审计；记忆语义只保留一套。

### 4.3 直接裁剪

- Persona、State、Behavior、Expression 的运行时逻辑。
- Relationship Memory、亲密度、信任度和心理状态推断。
- OpenViking 安装按钮、依赖安装器、独立服务和 Web Studio。
- 旧 archive、重复 session commit、重复 L0/L1 表和专用归档链路。
- 插件内部 LLM 回复缓存、query embedding 缓存、召回结果缓存和 rerank 结果缓存。
- FAISS、Qdrant、VikingDB、CuVS 等额外向量后端，除非评测证明必要。
- 本地模型、模型权重、GPU 管理和插件自建 Provider。
- 多租户、Workflow、Resource Hub、Data Bank、Skill、Tool、Trajectory 和 Training。
- 与聊天记忆无关的导入器、文件资源管理和通用知识库。

历史表和配置涉及用户数据时，只停止运行时读写并保留迁移窗口，不直接破坏性删除。

### 4.4 暂缓

- 黑话是否迁移为 OpenViking entity/alias。
- 回复样例是否迁移为 OpenViking experience。
- 完整 retention、导出、整域重置和 dead-letter 恢复。
- 大规模索引优化、FAISS 评估和显式厂商 cache-control。

## 5. 存储与安全边界

- `humanize.db` 保存任务、租约、审计、Context Trace、作用域映射和迁移元数据。
- OpenViking workspace 保存 Session、Memory、L0/L1/L2 和 Memory Link 内容。
- 不创建每 Agent、每用户、每群或每模块独立数据库。
- 不连接 AGFS、Qdrant、VikingDB 或远程 VectorDB。
- workspace 根目录必须位于插件数据目录内，路径、URI、文件名和锁都要校验。
- 页面读取后必须再次执行 scope、Agent、subject、状态和有效期过滤。
- 原始 QQ、群、会话标识不进入 workspace、日志或 WebUI，使用 HMAC 派生值。
- Provider 只通过 AstrBot adapter 调用；vendor 不创建模型客户端。
- OpenViking 初始化、Provider 调用、workspace 读写或召回失败时，返回空记忆并继续聊天。

## 6. 适配层

实现以下最小适配层：

- `OpenVikingMemoryAdapter`：AstrBot `MessageContext` <-> OpenViking Session/Message。
- `OpenVikingWorkspace`：根目录约束、原子写入、文件锁、manifest、恢复和迁移。
- `OpenVikingProviderBridge`：提取、Embedding、Rerank 统一转发到 AstrBot Provider。
- `OpenVikingRecallAdapter`：读取 L0/L1/L2，过滤后渲染现有 `<MemoryContext>`。
- `OpenVikingAuditBridge`：把 commit、memory diff、link 和召回来源写入 `humanize.db`。

## 7. 实施阶段

### Phase A：版本与依赖审计

1. 固定 OpenViking 0.4.9 的上游 commit。
2. 生成保留文件 import graph。
3. 确认裁剪后没有 `server`、`pyagfs`、`vectordb`、`parse`、`skill`、`train` 或本地模型导入。
4. 记录 AGPL-3.0、第三方许可证、源码来源和修改清单。

### Phase B：Vendor 与 Workspace

1. 建立 `humanize/vendor/openviking_core/` 私有命名空间。
2. 复制最小源码和许可证，不复制完整仓库、docs、tests、benchmark 或二进制文件。
3. 实现 workspace 根目录约束、原子写入、文件锁、版本 manifest 和损坏恢复。
4. 增加 vendor 核心的独立导入测试。

### Phase C：Session 与 Memory

1. 将 AstrBot 消息转换为 OpenViking Message。
2. 实现 `append -> commit`，保留完整经历和来源。
3. 接入 memory extraction、memory diff、merge/replace/patch 和最小 Memory Link。
4. 保留现有任务幂等、lease、重试、dead 状态和 fail-open。

### Phase D：分层召回与 Provider

1. 生成并读取 L0/L1/L2。
2. 先按 scope、Agent、subject 过滤，再执行关键词、层级和 Embedding 召回。
3. 通过 AstrBot Provider Bridge 调用 Chat、Embedding、Rerank。
4. 通过 Recall Adapter 输出现有 `<MemoryContext>`，不改回复协议。

### Phase E：迁移与切换

1. 将现有 `humanize_memory_items`、evidence、revision 转换为 OpenViking 页面和 URI。
2. 迁移必须可重复、可校验、可回滚；旧表先作为回滚来源。
3. Shadow read 对比旧 Memory Service 与 OpenViking 的召回结果。
4. 结果稳定后切换正式读写，再删除重复实现。

### Phase F：收口与发布

1. 删除未被 adapter 引用的旧提取、分层、关系和缓存代码。
2. 清理过期配置、页面、API、测试和依赖声明。
3. 完成跨平台、重载、迁移、损坏恢复和 Provider 故障测试。
4. 发布包包含裁剪源码、AGPL-3.0、来源版本和修改说明。

## 8. 验收标准

- 无需安装 `openviking` wheel 或启动独立服务即可运行。
- 无 AGFS、Qdrant、VikingDB、CuVS、本地模型和额外数据库依赖。
- 单条聊天可完成：`Session append -> commit -> memory diff -> L0/L1/L2 -> recall`。
- 同一 Session 重试不会生成重复页面、记忆或关系。
- scope、Agent、subject 过滤在最终读取后再次执行，泄漏为 0。
- workspace 损坏、Provider 超时、非法响应和 vendor 异常不阻断聊天。
- FTS/Embedding 不可用时可降级到层级和关键词召回。
- 现有回复协议、`<Msg>`、`<MemoryContext>` 和发送闸门行为不变。
- WebUI 不展示密钥、Authorization、原始身份标识或虚构状态。
- 插件不缓存最终模型回复、query embedding、召回结果或 rerank 结果。
- Python 测试、Ruff、JavaScript 静态检查和 `git diff --check` 通过。

## 9. 暂不纳入主线

- Relationship Memory 独立模型。
- Persona/State/Behavior/Expression 运行时。
- 多模态长期记忆。
- 本地模型和 GPU 管理。
- 通用知识库、资源平台、Skill/Tool/Trajectory/Training。
- 自动将普通回复沉淀为样例。

## 10. 合规要求

- 固定记录 OpenViking 上游版本、commit、许可证和修改清单。
- 发布包提供 OpenViking AGPL-3.0 文本、来源说明和裁剪说明。
- 不把裁剪代码伪装成原创实现。
- 不在日志、快照、提交或插件压缩包中写入密码、API Key、HMAC key 或原始身份标识。

## 11. Todo

### 进行中

- Phase B：建立 vendor 私有命名空间，复制首批白名单源码、AGPL-3.0 和来源/修改清单。
- Phase C-D：接入 Session、Memory、L0/L1/L2、Provider Bridge 和分层召回。
- Phase E-F：迁移旧数据，切换正式读写，裁剪重复实现并完成发布验证。

### 已完成

- 清理计划中的重复、推测性和偏离 OpenViking 内置目标的内容。
- 固定 OpenViking `v0.4.9` 上游 commit 和 AGPL-3.0 合规边界。
- 完成上游目标入口 import graph 审计，确认不能整目录复制。
- 形成首批文件级保留白名单和平台模块裁剪清单。
- 建立 `humanize.vendor.openviking_core` 私有命名空间、许可证和来源记录。
- 内置首批可独立导入的 OpenViking 领域内核，并增加禁用导入与行为测试。
- 内置 merge/replace/patch/sum/immutable、Memory Link 去重和带行号 patch 内核。
- 实现受控 workspace、版本 manifest、原子写入、跨实例文件锁和损坏恢复。
