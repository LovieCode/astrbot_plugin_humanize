# AstrBot Bot 拟人化插件

AstrBot 拟人化插件。让 Bot 在长期交互中表现出稳定、连贯、可解释的人格，而不是只靠一段固定的 system prompt 模仿角色。

## 当前状态

`v0.1.0` 已实现首个可运行 MVP：

- 使用轻量控制头驱动 `Reply` / `No Reply` 决策，最终展示内容仍是普通文本。
- 将当前用户消息包装为 `<Msg>`，把命中的可信黑话放入 `<KnownTerms>`。
- 从合法回复的 `UnknownTerms` JSON 学习陌生词，并按会话作用域写入 SQLite。
- 下一次同一作用域再次出现该词时，自动注入已推测含义。
- 控制头缺失或非法时拦截最终文本；工具本身可继续执行，但工具阶段夹带的普通文本也必须经过 `Action` 闸门。
- 提供浅色淡粉 WebUI，用于管理 Persona、State、Behavior、Expression、Control 和黑话词库。
- 所有功能共用数据目录中的唯一 `humanize.db`，通过同一 Repository 和迁移链维护。

Persona、State、Behavior 和 Expression 的管理面已完成，运行时联动仍在后续里程碑中。Relationship Memory 尚未实现，需要先研究作用域、隐私、衰减和冲突策略。

## 运行方式

插件放在 AstrBot 的 `data/plugins/astrbot_plugin_humanize/` 后，由 AstrBot 自动加载。配置在 AstrBot 插件配置页生成，管理页面由插件 `pages/humanize/` 自动注册。

默认情况下，运行数据保存在 AstrBot 插件数据目录下的 `astrbot_plugin_humanize/humanize.db`，不会写入插件安装目录。

严格协议启用时，本轮流式输出会被关闭，避免未校验文本提前发送。

## 回复控制协议

最终非工具文本以三行控制头开头，分隔线后是普通正文：

```text
Action: Reply
UnknownTerms: [{"word":"黑话","guess":"根据当前上下文推测的含义","confidence":0.86,"reason":"简短上下文依据"}]
---
这里开始就是用户实际看到的普通文本。
```

`No Reply` 使用 `Action: No Reply`，并保持 `---` 后正文为空。它会静默清空待发送结果，但不中断 AstrBot 的历史保存路径。日常闲聊长度只是模型表达偏好，不是程序硬限制；代码、日志、命令、教程和结构化数据等长内容会保持完整。

实际工具调用不要求生成控制头，避免破坏 Agent 工具循环；但工具调用前后的附带 Plain 文本如果要展示，也必须携带同样的控制头。插件剥离控制头后只发送正文。

## 工程结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期适配、状态机和最终发送闸门 |
| `humanize/domain/` | 领域模型和协议错误 |
| `humanize/protocol/` | 输入信封构建、控制头解析和回复决策校验 |
| `humanize/jargon/` | 黑话归一化、候选过滤和匹配策略 |
| `humanize/repositories/` | SQLite Repository 与审计日志 |
| `humanize/services/` | 请求准备和最终响应应用服务 |
| `humanize/web/` | Dashboard Web API |
| `pages/humanize/` | 插件管理 WebUI |
| `tests/` | 协议、匹配、存储和闭环测试 |

## 核心目标

- **稳定身份**：维持明确的身份、价值倾向、边界和表达习惯。
- **动态状态**：根据事件更新情绪、精力、关注点和短期意图。
- **关系连续性**：记录与不同用户、群聊的关系阶段和关键互动。
- **社交决策**：判断何时回复、沉默、追问、主动发起话题或结束话题。
- **行为一致性**：让决策、内容、语气和历史状态保持一致。
- **可控可解释**：状态可查看、可修正、可重置，避免不可逆的人格漂移。

## 边界

- 不修改 AstrBot 核心代码。
- 不重复实现表达风格学习；后续优先与 `astrbot_plugin_style_learner` 协作。
- 不训练或微调模型，主要通过状态管理、记忆、决策和提示词编排实现。
- 不以欺骗用户“Bot 是真人”为目标；拟人化指交互连贯性，不是身份伪装。

## 当前流程

```text
输入事件
  -> 包装 <Msg> 并注入 Rule / KnownTerms / 协议
  -> LLM 或工具循环
  -> 严格解析最终 Action / UnknownTerms 控制头
  -> 记录 UnknownTerms 与协议审计日志
  -> Reply: 剥离控制头后发送普通正文
  -> No Reply / 非法控制头: 终止最终回复
```

## 计划模块

| 模块 | 职责 |
| --- | --- |
| Persona | 定义稳定身份、价值倾向、行为边界和长期特征 |
| State | 管理情绪、精力、兴趣、压力和短期目标 |
| Relationship | 研究中，暂不实现；未来管理用户熟悉度、信任、互动偏好和群体角色 |
| Perception | 从消息和上下文提取事件、意图及社交信号 |
| Decision | 决定回复、等待、沉默、追问或主动互动 |
| Expression | 生成表达约束，并对接现有风格学习能力 |
| Memory | 保存可追踪、可衰减、可修正的长期信息 |
| Control | 提供状态查看、重置、审计和管理员干预能力 |

## 里程碑

1. 已完成轻量回复决策、黑话学习、统一数据库、管理页面和测试闭环。
2. 下一步把 Persona、State、Behavior 和 Expression 配置接入实际请求与决策流程。
3. 接入表达风格学习插件，并补充主动行为、衰减机制和管理员控制。
4. 单独研究 Relationship Memory，设计通过后再进入实现。

## 设计原则

- 稳定人格优先于即时模仿。
- 状态变化必须有来源、有范围、有衰减。
- 长期记忆只保存对未来互动有价值的信息。
- 决策层与表达层解耦，避免“说得像人”等同于“行为像人”。
- 所有自动学习结果都应支持查看、修正和删除。
