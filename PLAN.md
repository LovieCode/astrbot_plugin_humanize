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
- 删除旧 Control 后端的 Persona、State、Behavior、Expression 配置、服务、API、SQLite 表和测试；保留协议控制头、AstrBot Persona ID 与 OpenViking Agent 作用域。
- 将提示词模板审计从旧 Control 审计表拆分为专用 `humanize_prompt_template_audit`。
- 清理对应配置、API、WebUI 文案、测试、README 和依赖。
- 完成全量回归、发布包审计和最终提交。

## 6. 部署与验收流程

### 固定环境

- 默认 Shell：`D:\software\Code\Git\bin\bash.exe`。
- 本地 AstrBot：`D:\Code\Python\_root\AstrBot`。
- 本地插件：`D:\Code\Python\_root\AstrBot\data\plugins\astrbot_plugin_humanize`。
- SSH：`lovie@192.168.3.74:2222`，连接命令为 `ssh lovie@192.168.3.74 -p 2222`。
- SSH 密码：读取 `D:\Code\Python\_root\AstrBot\data\plugins\astrbot_plugin_humanize\.deploy.local.md`；该文件由 `.git/info/exclude` 排除，不得加入发布包或 Git。
- AstrBot WebUI：`http://192.168.3.74:6185`。
- 远端 AstrBot：`/home/lovie/AstrBot`。
- 远端 Python：`/home/lovie/AstrBot/.venv/bin/python`。
- 远端插件：`/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize`。
- 远端配置：`/home/lovie/AstrBot/data/cmd_config.json`，普通 SSH 用户可能无读取权限。
- 本地发布包：`D:\Code\Python\_root\AstrBot\tmp\astrbot_plugin_humanize-<timestamp>.tar.gz`。
- 远端发布包：`/home/lovie/astrbot_plugin_humanize-<timestamp>.tar.gz`。
- 远端暂存目录：`/home/lovie/astrbot_plugin_humanize.deploying-<timestamp>`。

### 发布前

1. 显式进入本地插件仓库 `D:\Code\Python\_root\AstrBot\data\plugins\astrbot_plugin_humanize`，用 `pwd` 和 `git rev-parse --show-toplevel` 确认当前位置；不要依赖终端初始目录，Git Bash 的 login shell 可能回到 AstrBot 根目录。
2. 在该目录确认待发布差异只包含本计划范围，不携带 `.git`、`.deploy.local.md`、pytest 缓存、临时文件、密钥或历史备份。
3. 执行 `uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .`、所有 WebUI JavaScript `node --check` 和 `git diff --check`；任一失败都停止部署。
4. 在本地 AstrBot 的 `tmp` 目录生成带时间戳的 `tar.gz`，排除 `.git`、`.deploy.local.md`、`.pytest_cache`、`.ruff_cache`、`.pytest-tmp-*`、`__pycache__` 和 `*.pyc`。
5. 执行 `sha256sum <发布包>` 并用 `tar -tzf <发布包>` 检查清单。发布包必须包含 OpenViking license、来源和版本说明。

### 远端部署

1. 使用 `scp -P 2222 <本地发布包> lovie@192.168.3.74:/home/lovie/` 上传，然后通过 SSH 执行 `sha256sum /home/lovie/<发布包>`，结果必须与本地一致。
2. 将发布包解压到 `/home/lovie/astrbot_plugin_humanize.deploying-<timestamp>`，不得直接在 `data/plugins` 中解压。
3. 在暂存目录确认 `main.py`、`humanize/openviking/management.py`、vendor 白名单、license 和 `pages/humanize` 完整；确认旧 migration、SQLite memory、Control 冻结页面、安装器和独立服务不存在。
4. 将正式插件短暂移动为同级 `.replacing` 目录，再把已验收的暂存目录移动到 `/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize`。`.replacing` 只用于本次替换，验收后立即删除，不保留部署备份。
5. 打开 `http://192.168.3.74:6185`，在插件管理中重载 Humanize。也可以使用带鉴权的 reload API，但不得读取或记录无权限访问的 `cmd_config.json`、JWT secret 或 token。

### 远端验收

1. 在 `/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize` 执行 `/home/lovie/AstrBot/.venv/bin/python -m pytest -q`、Ruff、Ruff format check 和所有 WebUI JavaScript `node --check`。
2. 检查加载日志：Humanize 无导入异常，OpenViking memory 状态为 `ready`；未配置 Provider 时允许按设计降级，但不得阻断基础聊天。
3. 确认 AstrBot 服务端口正常、首页返回 HTTP `200`、未鉴权管理 API 拒绝访问、插件 reload 返回成功。
4. 在真实会话完成一次记忆写入和召回，确认 `Session append -> commit -> memory diff -> L0/L1/L2 -> recall` 可用且重试不重复写入。
5. 打开 WebUI 检查长期记忆、召回调试、后台任务和回复样例；确认已裁剪入口、脚本、API 文案和死样式不存在。

### 收尾

1. 只删除本次时间戳对应的远端发布包、暂存目录、`.replacing` 目录、临时鉴权脚本和本地发布包；不得扩大路径或删除用户已有备份。
2. 再次确认 `/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize` 存在，`http://127.0.0.1:6185/` 在远端返回 HTTP `200`，Humanize 可加载；完成这些检查后才视为部署成功。

## 7. 远端全量测试清单

状态：`[ ]` 未开始，`[~]` 进行中，`[x]` 通过，`[!]` 缺陷。

| ID | 主要功能 | 基础功能 | 极限场景 1 | 极限场景 2 | 极限场景 3 | WebUI/API | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | 生命周期、配置与 Provider | 插件启动、配置加载 | 带 `/` 的 Provider ID | 非法控制字符 ID | Provider 缺失 | 设置页与状态接口 | [~] |
| T02 | 回复协议与实际发送 | Reply/No Reply | 破损控制头修复 | 多消息/超长正文 | 工具阶段与并发发送 | 协议记录视图 | [~] |
| T03 | 上下文编排与追踪 | 注入和记录 | token 预算截断 | 注入/重复前缀 | 快照缺失和重试 | 上下文追踪视图 | [~] |
| T04 | 黑话词库 | 识别、证据和注入 | 多义/别名冲突 | 无效 UnknownTerms | 作用域和并发变更 | 词库增改删导出 | [~] |
| T05 | OpenViking workspace | Session commit | 非法 Agent/路径 | 中断或损坏恢复 | 幂等和并发 commit | 运行状态接口 | [~] |
| T06 | 长期记忆与后台任务 | 提取、写入、召回 | 空/过期任务 | Provider 超时或失效 | scope/Agent 隔离 | 记忆、任务、召回调试 | [~] |
| T07 | 记忆管理 | 创建、修改、停用 | 非法 ID/动作 | revision 冲突 | 删除后召回边界 | 记忆详情和操作弹窗 | [~] |
| T08 | 回复样例 | 创建、审核、召回 | 空/超限轮次 | 作用域或状态排除 | 低分和 Provider 降级 | 样例管理和调试 | [~] |
| T09 | 提示词、存储与审计 | 模板保存/恢复 | 非法变量 | 大文本和并发写 | SQLite schema 升级 | 模板、统计、导出 | [~] |
| T10 | 管理 API 与 WebUI | 全部视图加载 | 空数据/加载失败 | 长文本和窄屏 | 快速切页与竞态 | 浏览器交互、静态检查 | [~] |

### 远端执行记录（2026-07-18）

- [x] 远端完整回归：`233 passed, 1 warning`；`ruff check .`、`ruff format --check .` 和全部 `pages/humanize/*.js` 的 `node --check` 通过。
- [x] 远端格式门禁：同步 `humanize/openviking/management.py` 后，`ruff format --check .` 通过（69 个文件）。
- [x] 远端定向矩阵：51 个基础/极限场景通过，覆盖 T01-T10；不读取聊天正文，测试全部使用临时数据库或无正文状态查询。
- [x] 远端 Provider 边界补测：`tests/test_config_schema.py` 5 项通过，确认带 `/` 的 ID 保留，控制字符和缺失值不进入 Provider lookup。
- T01：typed selector、带 `/` 的 Provider ID、记忆禁用身份稳定、身份初始化失败 fail-open。
- T02：Reply 解析、嵌套控制标签拒绝、长/格式化文本、发送间隔、并发工具阶段保留。
- T03：token 预算、记忆源异常 fail-open、并行召回、重复追踪冲突。
- T04：最长 verified 词优先、多义 sense、冲突/禁用/导出、部分 schema 升级恢复。
- T05：workspace 初始化、路径逃逸、损坏 manifest 恢复、并发原子写、并发 Session commit。
- T06：OpenViking 写入/召回、故障 fail-open、worker 租约续期、丢失租约、跨 Agent 批处理隔离。
- T07：记忆创建/列表/详情、更新/拒绝、无正文审计/幂等、revision/身份冲突。
- T08：样例 CRUD/召回、零分边界、条件/排除过滤、Agent 隔离。
- T09：模板专用审计、非法变量、批量保存/重置、旧 schema 清理、快照凭据脱敏。
- T10：管理 API 端到端、公开错误限界、静态资源/DOM、字号一致性、已裁剪入口、multi-sense 兼容。
- [!] 真实服务发现 10 条历史记忆任务处于 `dead`，均为 `OpenViking Session write failed`。根因是服务进程仍执行修复前的 `adapter.py`；历史任务负载已按隐私设计清空，不能重放。
- [x] 已受控重启远端服务：先确认旧 Python 进程与 `6185` 端口退出，再启动新实例；首页恢复 HTTP `200`，最新 Humanize 初始化记录为 `memory state=ready`、`reason=local_identity_secret`，无 OpenViking 初始化错误。
- [x] 新实例语义健康检查：含空格的 AstrBot Agent ID 已归一化为安全 `agent-<hash>`，真实 workspace `format_version=1` 且可访问。
- [~] 重启后无正文队列监控 120 秒：基线仍为 `completed:10, dead:10`，未观测到新的真实会话，因此尚无新任务可用于验证实时写入/召回。
- [x] Headless Edge 验证远端 Dashboard 登录页：桌面 `1440×900`、真实移动 viewport `412×915` 均正常渲染且无水平溢出。
- [~] 服务首页 HTTP `200`，未鉴权管理 API 均返回 `401`；因此尚未以真实登录态完成 T01/T07-T10 的浏览器交互、窄屏和失败态验收。

### 当前待办

- [x] 在远端运行完整 Python、静态 JavaScript 和格式/检查工具。
- [x] 为 T01-T10 各记录至少三组基础/极限场景的命令、结果和缺陷。
- [~] 在新运行实例完成新的真实会话，复测 `Session append -> commit -> memory diff -> L0/L1/L2 -> recall`；历史 `dead` 任务负载已按隐私设计清空，不能重放。
- [ ] 在真实浏览器验证全部 WebUI 视图、空态、失败态和窄屏布局。
- [ ] 在已登录 Dashboard 下验证 settings、memory、jobs、recall debug、reply examples 和 prompt templates 的真实 API/交互；完成后更新 T01、T05-T10 状态。
