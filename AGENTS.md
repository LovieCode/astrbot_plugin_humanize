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

## 执行与部署（用户固定偏好）

- 父目录 `AstrBot/AGENTS.md` 的“执行、验证与部署流程”是本插件的完整且优先的操作指南；本节只补充插件特例，不以文件数量替代风险判断。
- 未明确要求部署时，插件改动完成定向测试、改动文件的 Ruff format/check、`git diff --check` 和本插件 commit 即结束；不打包、不 SSH、不重启、不做无关全量检查。
- 明确要求部署且不涉及依赖、配置 schema、API、迁移、vendor、许可证、前端构建或跨模块契约时，调用 `bash scripts/deploy_hotfix.sh --pytest <相关测试> -- <改动文件...>`；先用 `--dry-run` 检查清单。
- 部署脚本自动完成：本地定向检查 → 上传已提交文件 → SHA-256 校验 → 远端定向检查 → **插件热重载**（`POST /api/v1/plugins/reload`）。后端 Python 与前端页面均通过热重载生效，**不需要重启 AstrBot**；仅 `--restart` 显式指定或服务不健康时才重启。
- 前端改动必须跑 `scripts/build_spa.py` 构建并提交 `pages/` 产物；只改 `webui/` 源码线上不生效（脚本会自动构建并校验产物已提交）。
- 远端重启若报 PermissionError，先 `sudo chown -R lovie:lovie /home/lovie/AstrBot/data /home/lovie/AstrBot/.venv /home/lovie/AstrBot/uv.lock`。

## 调试备忘（通用可复用）

### WebUI
- 点击无响应优先查：事件绑定丢失（动态重建元素 → 用 document 委托）、选择器作用域（限定容器）、id 冲突（多个弹窗字段需前缀）、渲染后未刷新。
- 遮罩层会拦截后续点击；检查元素是否被 overlay 遮挡。
- DOM 动态内容用 textContent，禁 innerHTML 拼接持久化数据。
- Playwright 验证：登录 `#/auth/login` 拿 token → hash 导航 → 在插件 iframe 内操作；欢迎 overlay 会拦截点击需先关闭。

### 后端
- 先看日志堆栈定位，不猜；改前用最小复现脚本验证根因，改后跑定向测试。
- 校验失败先确认请求体与现有数据一致（revision/版本号等），再查服务层是否擅自改字段。
- API 测试：POST `/api/auth/login` 拿 JWT，带 `Authorization: Bearer` 调接口。
- Windows 下 Node 直接 spawn npm 全局 .cmd 会 ENOENT/EINVAL：用 `process.execPath` 调 bin 脚本或 `shell:true`。

### 通用
- 仓库可能是 CRLF 行尾：`git diff --check` 报 trailing whitespace 时用 `git -c core.whitespace=cr-at-eol diff --check` 区分真空格与 CR。
- ruff 不在 PATH 时用 `uv run ruff`；环境缺依赖（如 apscheduler）导致测试收集失败时，跑不依赖它的定向测试。
- 权限类报错先查属主（sudo chown），不反复试错。
