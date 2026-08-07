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

## 调试经验（2026-08 沉淀）

### 部署链路
- 提交 → push origin main → 远端 `git pull --ff-only` → 静态页面立即生效（无需重启）；**后端 Python 改动必须重启 AstrBot**（tmux 窗口 `astrbot-service:1`，C-c 后 `cd /home/lovie/AstrBot && uv run main.py`）。
- 重启前检查权限：`sudo chown -R lovie:lovie /home/lovie/AstrBot/data /home/lovie/AstrBot/.venv /home/lovie/AstrBot/uv.lock`（root 残留会致启动失败）。
- 页面路径是 **SPA 单页 `humanize`**（`#/plugin-page/astrbot_plugin_humanize/humanize`），不是旧的多页名（memory/jargon…）。组件注册名见 `/api/v1/plugins/.../pages`。
- 前端产物 `pages/humanize/` 由 `webui/` 源码经 `scripts/build_spa.py` 构建；**只改 webui 不构建不提交产物，线上永远不生效**（踩过坑）。
- API 双路径：iframe 内 `/api/plug/astrbot_plugin_humanize/`（父页面代理），独立访问 `/api/v1/plugins/extensions/astrbot_plugin_humanize/`。

### 记忆系统
- WebUI 的 reject/update/activate 只传 `{action,id,revision,reason}`，**不能带 agent_id**：memory.py 曾强制 `agent_id="default"` 导致与真实 `agent-<sha>` 身份不符 → `OpenViking memory identity is immutable`。只在 create 时默认 default，其余由 management 从 existing 补全。
- 已拒绝/已废弃记忆可 `action=activate` 恢复（前端有恢复按钮）；revision 必须用当前 version（reject 一次 +1）。
- 任务列表接口 `memory-jobs` 明确不暴露 payload；completed/dead 时 payload 清空。

### WebUI 调试
- 点击无响应优先查：事件绑定（topbar 重建丢绑定 → document 委托）、选择器作用域（`$(".drawer-foot", drawer)`）、id 冲突（弹窗字段需前缀）、渲染后未刷新。
- 抽屉/弹窗打开时遮罩层会拦截后续点击；`get_app_state` 若报 `Exception.ToString() failed` 是 Edge/WinUI 应用特例，换 explorer/终端验证。
- Playwright 验证需登录 `#/auth/login` 后 hash 导航，再在 iframe（`plugin/page/content`）里操作；overlay 欢迎弹窗会拦截点击。

### 协议 v2
- 标签位置不限、UnknownTerms 缺省=[]、单条消息也包 `<Messages>`、Message 内标签不解析、ImageCache 纯文本；No Reply 带正文报 `no_reply_has_text` 且永不 repair。
- 协议注入经 `extra_user_content_parts`；原始上下文页「只看到 Rule」多是前端截断（收起限高 190px 可滚动、展开不限高）。
- 修复成功路径直接 `_record_final_protocol_success`（幂等 `_FINAL_LOG_PENDING`），不依赖 dispatch 钩子。

### 远端环境
- napcat：`astrbot-service` tmux 窗口 0，目录属主 lovie:lovie；HTTP 3002 临时端点 token `hisApi2026` 仅 127.0.0.1；历史消息依赖 QQ 本地缓存，离线/大群拉不全。
- AstrBot API 测试：先 POST `/api/auth/login` 拿 JWT，再带 `Authorization: Bearer` 调插件接口。