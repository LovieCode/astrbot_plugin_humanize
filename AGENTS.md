# astrbot_plugin_humanize

## Scope

- 只修改本插件目录，不修改 AstrBot 核心代码或其他插件。
- 本插件目标是通过人格、状态、关系、记忆和行为决策提升 Bot 的长期交互连贯性。
- `astrbot_plugin_style_learner` 负责表达风格学习，本插件优先集成而不是复制其能力。

## Current phase

- 当前处于 MVP 实现与稳定化阶段。
- 首要能力是轻量回复控制协议、黑话学习、作用域隔离、审计日志和管理 WebUI。
- Persona、State、Behavior、Expression 和 Control 已有统一管理与持久化入口，运行时联动按 `PLAN.md` 后续阶段推进。
- Relationship Memory 尚未实现，必须先完成作用域、隐私、衰减和冲突策略研究。

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
- 明确要求部署且不涉及依赖、配置 schema、API、迁移、vendor、许可证、前端构建或跨模块契约时，必须调用 `bash scripts/deploy_hotfix.sh --pytest <相关测试> -- <改动文件...>`；先用 `--dry-run` 检查清单。禁止手工拼 SSH、SCP、sudo、tmux、校验和重启命令。
- 仅当上述条件命中或用户明确要求完整发布时，才读取 `PLAN.md` 的完整部署指南；`PLAN.md` 只维护可复用流程，不追加每次部署流水账。
