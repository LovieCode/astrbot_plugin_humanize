# astrbot_plugin_humanize

## Scope

- 只修改本插件目录，不修改 AstrBot 核心代码或其他插件。
- 本插件目标是通过人格、状态、关系、记忆和行为决策提升 Bot 的长期交互连贯性。

## Engineering rules

- 使用 Python 3.12，保持异步调用链，不引入不必要依赖。
- 持久化数据使用 AstrBot 数据目录，不写入插件安装目录。
- 所有模块共用唯一的 `humanize.db`、Repository、迁移和连接路径，禁止为单个 Agent 或模块创建独立数据库。
- 状态变化必须可解释、可限制、可衰减、可重置。
- 用户关系与长期记忆必须按会话或用户作用域隔离。
- 非工具最终文本必须先通过严格协议校验，禁止绕过最终发送闸门。
- LLM 提供的未知词和释义一律视为不可信输入，必须验证、限长并保留证据。
- WebUI 动态内容使用 DOM 文本 API，禁止把持久化内容直接拼进 `innerHTML`。
- 提交使用 Conventional Commits，并在本插件独立 Git 仓库内完成。
- 禁止使用批量替换类脚本及类似操作

## LLM 调用代理（全链路用量追踪，新增调用必须遵守）

- 插件通过 `_install_provider_hooks`（main.py）给所有 AstrBot Provider 子类的 `text_chat`/`text_chat_stream` 打了统一补丁：任何 LLM 调用都会先经过 `plugin._proxied_provider_call` / `_aiter_proxied_stream`（代理层，实现在 main.py + `humanize/llm_proxy.py`）。
- **新增直接调用 Provider 的代码路径必须用 `humanize/llm_proxy.py` 的 `llm_call_context(call_type, request_id=…, scope_type=…, scope_id=…, conversation_id=…)` 异步上下文包裹 `text_chat` 调用**，否则该调用不会进入用量日志（代理只在上下文存在时记录，避免把其他插件/核心的调用计入本插件用量、也避免与主管线重复计数）。
- 记录内容全部来自 Provider 回报的真实 usage（`response.usage`，缺失时回退 `raw_completion.usage`；都没有则 `usage_observed=0`、token 记 0，**禁止估算**），落到 `humanize_llm_call_log`（schema v30，含 call_type/scope/request 关联、duration_ms、status、截断 error）。主管线（tool/final/repair）已有 `_record_llm_usage_sample`，继续走 `humanize_llm_usage_samples`，两者在 `get_usage_overview` 合并。
- 现有 call_type 约定：`final`/`tool`/`repair`（主管线，走 usage_samples）、`transcribe_image`、`transcribe_sticker`（转述，trace 带 request/scope）、`extract`（记忆抽取）、`openviking`。新增调用类型沿用小写下划线命名，并在 WebUI `dashboard.js` 的 `CALL_TYPE_LABEL` 补中文标签。
- 代理记录必须 fail-open：记录失败只打 warning，绝不影响 LLM 调用本身；不要在记录路径里引入新的必备依赖。
- 首页用量面板数据来自 `GET usage-overview`（routes.py），不要从别的表"推算"用量。

## 执行与部署（用户固定偏好）

- 父目录 `AstrBot/AGENTS.md` 的“执行、验证与部署流程”是本插件的完整且优先的操作指南；本节只补充插件特例，不以文件数量替代风险判断。
- 未明确要求部署时，插件改动完成定向测试、改动文件的 Ruff format/check、`git diff --check` 和本插件 commit 即结束；不打包、不 SSH、不重启、不做无关全量检查。
- 部署统一走 git：`bash scripts/deploy_git.sh`（先 `--dry-run`）。脚本自动：本地检查（pytest/ruff/SPA 构建一致性）→ push origin main → 远端 `git pull --ff-only` → **插件热重载**（`POST /api/v1/plugins/reload`）。后端与前端均通过热重载生效，**不需要重启 AstrBot**。
- 远端必须是同一仓库的 git clone；`data/` 等运行时数据被 gitignore，不受影响。
- 前端改动必须跑 `scripts/build_spa.py` 构建并提交 `pages/` 产物；只改 `webui/` 源码线上不生效（脚本会自动构建并校验产物已提交）。
- `pages/` 是纯生成物，**禁止直接编辑或手工提交**；前端改动只落在 `webui/` 源码。若 `pytest tests/test_webui_static.py` 的构建一致性用例失败，说明产物与源码漂移（曾有直接改产物提交）：必须先把产物中的功能逆向回移植进源码、验证重建后与线上产物字节一致，再做任何新的前端改动；否则直接重建会静默抹掉已上线功能。
- 远端重启若报 PermissionError，先 `sudo chown -R lovie:lovie /home/lovie/AstrBot/data /home/lovie/AstrBot/.venv /home/lovie/AstrBot/uv.lock`。

## 环境与访问信息（本地专用，勿上传）

- 远端部署凭据、Dashboard 地址、API key、SSH 端口等全部在 **`.deploy.local.md`**（未被 git 跟踪，`.git/info/exclude` 忽略，禁止提交/打包）。
- 需要远端访问时读 `.deploy.local.md` 的 Target/SSH port/Password/Dashboard/API key 字段，不要在 AGENTS.md 或代码里重复写。
- 插件 WebUI 地址：`<Dashboard>/#/plugin-page/astrbot_plugin_humanize/humanize`（SPA 单页）。
- napcat WebUI 与 QQ 机器人服务跑在同一台远端（tmux `astrbot-service`），凭据见 `.deploy.local.md` 或远端 napcat 配置。

## 调试备忘（通用可复用）

### WebUI
- 点击无响应优先查：事件绑定丢失（动态重建元素 → 用 document 委托）、选择器作用域（限定容器）、id 冲突（多个弹窗字段需前缀）、渲染后未刷新。
- 遮罩层会拦截后续点击；检查元素是否被 overlay 遮挡。
- DOM 动态内容用 textContent，禁 innerHTML 拼接持久化数据。
- Playwright 验证：登录 `#/auth/login` 拿 token → hash 导航 → 在插件 iframe 内操作；欢迎 overlay 会拦截点击需先关闭。
- 产物/源码漂移回移植（2026-08 事故）：`pages/` 曾被直接编辑提交而 `webui/` 源码未同步。要点：pages 产物 blob 是 CRLF、`webui/` 源码是 LF（`git ls-files --eol` 确认）；构建对 JS 做 IIFE→`HZ.views["<view>"]` 包装、删除 `HZ.renderSidebar` 行，CSS 仅做跨视图 id 重命名——无重命名的视图产物=源码逐字节（仅行尾不同），可按此逆向重建后用"重建产物与 HEAD 字节一致"自验。

### 后端
- 先看日志堆栈定位，不猜；改前用最小复现脚本验证根因，改后跑定向测试。
- 校验失败先确认请求体与现有数据一致（revision/版本号等），再查服务层是否擅自改字段。
- API 测试：POST `/api/auth/login` 拿 JWT，带 `Authorization: Bearer` 调接口。
- Windows 下 Node 直接 spawn npm 全局 .cmd 会 ENOENT/EINVAL：用 `process.execPath` 调 bin 脚本或 `shell:true`。

### 通用
- 仓库可能是 CRLF 行尾：`git diff --check` 报 trailing whitespace 时用 `git -c core.whitespace=cr-at-eol diff --check` 区分真空格与 CR。
- ruff 不在 PATH 时用 `uv run ruff`；环境缺依赖（如 apscheduler）导致测试收集失败时，跑不依赖它的定向测试。
- 权限类报错先查属主（sudo chown），不反复试错。
