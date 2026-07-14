# astrbot_plugin_humanize

## Scope

- 只修改本插件目录，不修改 AstrBot 核心代码或其他插件。
- 本插件目标是通过人格、状态、关系、记忆和行为决策提升 Bot 的长期交互连贯性。
- `astrbot_plugin_style_learner` 负责表达风格学习，本插件优先集成而不是复制其能力。

## Current phase

- 当前仅允许完善需求、架构和数据模型。
- 在用户明确要求进入实现阶段前，不新增 `main.py` 或业务代码。

## Engineering rules

- 使用 Python 3.12，保持异步调用链，不引入不必要依赖。
- 持久化数据使用 AstrBot 数据目录，不写入插件安装目录。
- 状态变化必须可解释、可限制、可衰减、可重置。
- 用户关系与长期记忆必须按会话或用户作用域隔离。
- 提交使用 Conventional Commits，并在本插件独立 Git 仓库内完成。
