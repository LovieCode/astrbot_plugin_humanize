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

- 修复或替换已配置的长期记忆提取 Provider。它在后台预算内未返回；确定性规则仍会写入 OpenViking，Provider 仅负责补充非结构化语义记忆，失败不得阻断 Session、Memory 或 recall。
- 在合法 Dashboard 登录态下完成各页面的真实浏览器交互、空态、失败态和窄屏验收；不绕过认证或读取 JWT/config secret。

### 已完成

- 固定 OpenViking `v0.4.9` commit、AGPL-3.0、私有命名空间和 vendor 白名单。
- 内置 Message、Session、Memory、merge、Memory Link 和 L0/L1/L2 最小内核。
- 实现 workspace 原子写、跨实例锁、损坏恢复和重试幂等。
- 实现 AstrBot Provider Bridge、分层召回和读取后二次过滤。
- 当没有命中长期记忆时，从同一 Agent、作用域、主体和会话的 OpenViking L0/L1 commit 提供受限连续对话兜底；长期记忆命中时保持优先，L2 原文不直接注入。
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
- 远端 `cmd_config.json` 由 root 持有时，AstrBot Python 进程必须以 sudo 身份运行；tmux server 和会话仍由 `lovie` 持有，禁止创建 root tmux server。
- 部署前先检查远端 `data`、插件、配置和 `plugin_data` 的属主；若普通 SSH 用户无写权限，只能在 `/home/lovie` 暂存验收，必须取得有效 sudo 授权后才能替换正式目录或重启，不得猜测 sudo 密码。
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
4. 整包发布时将正式插件短暂移动为同级 `.replacing`，再把已验收的暂存目录移动到 `/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize`；`.replacing` 仅用于本次替换，验收后立即删除。小型热修复可只上传明确改动的文件，逐个校验 SHA-256 后用 `sudo install -m 0644` 原位替换，禁止扩大覆盖范围。
5. 先向当前 `lovie` tmux pane 发送受控停止信号并确认 `6185` 已退出；再创建普通用户 session `astrbot-service-<commit>`，在其中通过 sudo 启动 `/home/lovie/AstrBot/.venv/bin/python main.py`。不得使用 `sudo tmux`，不得把密码写入命令、here-doc、日志或发布包。
6. 打开 `http://192.168.3.74:6185`，在插件管理中重载 Humanize。也可以使用带鉴权的 reload API，但不得读取或记录无权限访问的 `cmd_config.json`、JWT secret 或 token。

### 远端验收

1. 在 `/home/lovie/AstrBot/data/plugins/astrbot_plugin_humanize` 执行 `/home/lovie/AstrBot/.venv/bin/python -m pytest -q`、Ruff、Ruff format check 和所有 WebUI JavaScript `node --check`。
2. 检查加载日志：Humanize 无导入异常，OpenViking memory 状态为 `ready`；未配置 Provider 时允许按设计降级，但不得阻断基础聊天。
3. 确认 AstrBot 服务端口正常、首页返回 HTTP `200`、未鉴权管理 API 拒绝访问、插件 reload 返回成功。
4. 在真实会话完成一次记忆写入和召回，确认 `Session append -> commit -> memory diff -> L0/L1/L2 -> recall` 可用且重试不重复写入；若不适合发送真实消息，使用独立的临时 synthetic workspace、固定假身份和假消息完成同一链路，结束后只删除该临时 workspace。
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
| T05 | OpenViking workspace | Session commit | 非法 Agent/路径 | 中断或损坏恢复 | 幂等和并发 commit | 运行状态接口 | [x] |
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
- [x] 修复后远端 OpenViking 专项回归：adapter、workspace、files、merge、management、provider、recall、runtime 与 vendor 共 `44 passed`；覆盖真实规则提取后的 Session 写入、Memory 写入、分层召回、作用域/Agent/过期过滤及 Provider 降级。
- [x] 修复后远端 WebUI 专项回归：管理 API 与静态视图共 `14 passed, 1 warning`，全部 `pages/humanize/*.js` 的 `node --check` 通过；覆盖 settings、memory、jobs、recall debug、reply examples、prompt templates 的受控接口与 DOM 行为，但不替代已登录 Dashboard 的真实浏览器验收。
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
- [x] 重启后真实会话链路：新任务 `#21` 于 `18:07` 入队，在单条空闲批处理约 180 秒后于 `18:10` 完成；无错误，OpenViking Session 的 L0/L1/L2 文件和 commit 已写入。全程仅查询任务和文件计数，未读取聊天正文。
- [!] 该真实会话未产生可接受的长期记忆候选：workspace 当前 `memory_files=0`、`memory_diffs=0`，后续请求的 `memory_context` 为 `no_match`。这不是触发或写入失败；需要用已选择的提取 Provider 或命中保守规则的事实型消息，继续完成语义记忆与召回验收。
- [x] 修复 Session 连续对话召回空洞并部署：没有命中长期记忆时，只读取当前同一 Agent/作用域/主体/会话的 L0/L1 commit；语义长期记忆仍优先，L2 原文不直接注入。新增 5 个基础/隔离/损坏/优先级极限测试，本地 `238 passed, 1 warning`，远端定向 `11 passed`、Ruff 与 JavaScript 检查通过。
- [x] 部署后服务已受控重启：新 Python 进程于 `18:47` 启动，Humanize `memory state=ready`，首页 HTTP `200`。远端完整插件回归为 `238 passed, 1 warning`，Ruff `69 files already formatted`；父仓库 `git diff --check` 的失败仅来自未触及 Dashboard 文件的既有 CRLF/trailing-whitespace 差异，未修改该范围。
- [x] 后续修复在远端用户暂存包完成回归：`239 passed, 1 warning`，Ruff format/check 通过（94 files），13 个 `pages/humanize/*.js` 的 `node --check` 通过；该包未替换正式插件目录。
- [x] 远端用户暂存包按 T01-T10 分组的功能矩阵全部通过（分组存在有意重叠）：T01 `8`、T02 `91`、T03 `23`、T04 `26`、T05 `31`、T06 `25`、T07 `4`、T08 `18`、T09 `17`、T10 `14` 项。每组包含基础路径及至少三类边界/异常/并发或隔离场景；真实运行实例与已登录 Dashboard 场景仍按当前待办验收。
- [x] 已将 Session fallback 阈值修复正式就地部署：目标文件 checksum 与本地一致；正式插件回归 `240 passed, 1 warning`、Ruff format/check 和 13 个 WebUI JS 检查均通过。受控停止旧 `6185` listener 后，经普通用户 tmux 启动新进程；首页 HTTP `200`，Humanize `memory state=ready`，无初始化或 recall 异常。
- [x] 新进程使用真实 OpenViking workspace 的无正文探针：同一 Agent/作用域/主体/会话在阈值 `0.85` 下命中 5 条 Session L0/L1 candidate，错误 conversation hash 为 `no_match`；未读取或输出聊天正文。
- [x] Headless Edge 验证远端 Dashboard 登录页：桌面 `1440×900`、真实移动 viewport `412×915` 均正常渲染且无水平溢出。
- [~] 服务首页 HTTP `200`；未鉴权的插件 memory GET、未知插件路由 GET、插件 memory POST 和 Dashboard plugins GET 均返回 `401`，未发生副作用；因此尚未以真实登录态完成 T01/T07-T10 的浏览器交互、窄屏和失败态验收。
- [x] 部署 `0aa28d4`：将提取 Provider 超时降级为非阻断路径，保留 15 秒后台预算；本地 `242 passed, 1 warning`，远端 pytest、Ruff、Ruff format 和 13 个 WebUI JavaScript 检查通过，服务首页 `200` 且 OpenViking `ready`。
- [x] 隔离 synthetic 验收：真实配置的提取 Provider 在 15 秒预算内未返回，但假消息仍完成 `1` 个 Session commit、`2` 个 memory diff、`2` 个 memory file，并以 `matched` 召回 `1` 条；临时 workspace 已删除，未读写真实会话内容。

### 当前待办

- [x] 在远端运行完整 Python、静态 JavaScript 和格式/检查工具。
- [x] 为 T01-T10 各记录至少三组基础/极限场景的命令、结果和缺陷。
- [~] 长期记忆链路已由隔离 synthetic 数据验收；继续处理已配置提取 Provider 的超时，使非规则的语义记忆也能生成。历史 `dead` 任务负载已按隐私设计清空，不能重放。
- [ ] 在真实浏览器验证全部 WebUI 视图、空态、失败态和窄屏布局。
- [ ] 在已登录 Dashboard 下验证 settings、memory、jobs、recall debug、reply examples 和 prompt templates 的真实 API/交互；完成后更新 T01、T05-T10 状态。
