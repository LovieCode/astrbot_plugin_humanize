# astrbot_plugin_humanize

## Scope

- 只改本插件目录；目标是提升 Bot 长期交互连贯性（人格、记忆、关系、行为决策）。

## Engineering rules

- Python 3.12，异步调用链，不引入不必要依赖。
- 持久化数据用 AstrBot 数据目录，不写插件安装目录；全插件共用唯一 `humanize.db`。
- 状态变化可解释、可限制、可衰减、可重置；记忆按会话/用户作用域隔离。
- 非工具最终文本必须通过协议校验，禁止绕过发送闸门；LLM 提供的内容一律不可信，验证、限长、留证据。
- WebUI 动态内容用 textContent，禁 innerHTML 拼接持久化数据。
- 提交用 Conventional Commits，在本插件独立 Git 仓库内完成。

## 执行与部署

- 父目录 `AstrBot/AGENTS.md` 的流程优先；本节只补充插件特例。
- 未要求部署：定向测试 + Ruff + `git diff --check` + commit 即止，不打包不 SSH 不重启。
- 要求部署：`bash scripts/deploy_hotfix.sh --pytest <测试> -- <改动文件...>`，先 `--dry-run`。脚本自动检查、上传、校验、**热重载**（后端与前端均无需重启 AstrBot；`--restart` 才重启）。
- 前端改动必须 `scripts/build_spa.py` 构建并提交 `pages/` 产物，只改 `webui/` 线上不生效。

## 调试备忘

- 点击无响应：事件绑定丢失（用 document 委托）、选择器作用域、id 冲突、遮罩拦截。
- 后端问题先看日志堆栈 + 最小复现，不猜；改后跑定向测试。
- API 测试：`POST /api/auth/login` 拿 JWT，带 `Authorization: Bearer`。
- Windows 下 Node spawn npm 全局 .cmd 报 ENOENT/EINVAL：用 `process.execPath` 调 bin 或 `shell:true`。
- `git diff --check` 报 trailing whitespace 时用 `git -c core.whitespace=cr-at-eol diff --check` 区分 CR。
- ruff 不在 PATH 用 `uv run ruff`。
- 权限报错先查属主 `sudo chown -R lovie:lovie`，不反复试错。
