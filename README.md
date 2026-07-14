# AstrBot Bot 拟人化插件

让 Bot 在长期交互中表现出稳定、连贯、可解释的人格，而不是只靠一段固定的 system prompt 模仿角色。

## 当前状态

`v0.1.0` 已实现首个可运行 MVP：

- 使用严格 XML 协议驱动 `Reply` / `No Reply` 决策。
- 将当前用户消息包装为 `<Msg>`，把命中的可信黑话放入 `<KnownTerms>`。
- 从合法回复的 `<UnknownTerms>` 学习陌生词，并按会话作用域写入 SQLite。
- 下一次同一作用域再次出现该词时，自动注入已推测含义。
- XML 缺失、损坏或不符合 Schema 时拦截最终文本；工具本身可继续执行，但工具阶段夹带的普通文本也不会绕过 `<Action>` 显示。
- 提供浅色淡粉 WebUI，用于查看、筛选、确认、拒绝和修正黑话，以及查看协议日志。

人格状态、关系记忆、主动发起话题和风格插件联动仍在后续里程碑中。

## 运行方式

插件放在 AstrBot 的 `data/plugins/astrbot_plugin_humanize/` 后，由 AstrBot 自动加载。配置在 AstrBot 插件配置页生成，管理页面由插件 `pages/humanize/` 自动注册。

默认情况下，运行数据保存在 AstrBot 插件数据目录下的 `astrbot_plugin_humanize/humanize.db`，不会写入插件安装目录。

严格协议启用时，本轮流式输出会被关闭，避免未校验文本提前发送。

## XML 协议

最终非工具文本必须是一个完整文档：

```xml
<AgentResponse version="1">
  <Action>Reply</Action>
  <UnknownTerms>
    <UnknownTerm>
      <Word>黑话</Word>
      <Guess>根据当前上下文推测的含义</Guess>
      <Confidence>0.86</Confidence>
      <Reason>简短上下文依据</Reason>
    </UnknownTerm>
  </UnknownTerms>
  <Reply>
    <Message>第一条消息</Message>
    <Message>第二条消息</Message>
  </Reply>
</AgentResponse>
```

`No Reply` 必须配合空的 `<Reply />`。它会静默清空待发送结果，但不中断 AstrBot 的历史保存路径。回复超过配置的单条字符限制时，会优先按标点拆分；拆分后超过单次消息数上限则整次拦截。

实际工具调用不要求先生成 XML，避免破坏 Agent 工具循环；但工具调用前后的附带 Plain 文本会被清空。只有最终完整 `AgentResponse` 中通过校验的 `<Action>Reply</Action>` 内容才会展示。

## 工程结构

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期适配、状态机和最终发送闸门 |
| `humanize/domain/` | 领域模型和协议错误 |
| `humanize/protocol/` | XML 构建、严格解析和消息拆分 |
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
  -> 严格解析最终 AgentResponse
  -> 记录 UnknownTerms 与协议审计日志
  -> Reply: 清洗 XML 后分条发送
  -> No Reply / 非法 XML: 终止最终回复
```

## 计划模块

| 模块 | 职责 |
| --- | --- |
| Persona | 定义稳定身份、价值倾向、行为边界和长期特征 |
| State | 管理情绪、精力、兴趣、压力和短期目标 |
| Relationship | 管理用户熟悉度、信任、互动偏好和群体角色 |
| Perception | 从消息和上下文提取事件、意图及社交信号 |
| Decision | 决定回复、等待、沉默、追问或主动互动 |
| Expression | 生成表达约束，并对接现有风格学习能力 |
| Memory | 保存可追踪、可衰减、可修正的长期信息 |
| Control | 提供状态查看、重置、审计和管理员干预能力 |

## 里程碑

1. 明确人格、状态、关系和记忆的数据模型。
2. 定义事件处理流程以及与 AstrBot 生命周期的接入点。
3. 已完成严格回复决策、黑话学习、管理页面和测试闭环。
4. 接入表达风格学习、人格状态与关系记忆。
5. 增加主动行为、衰减机制和更完整的管理员控制。

## 设计原则

- 稳定人格优先于即时模仿。
- 状态变化必须有来源、有范围、有衰减。
- 长期记忆只保存对未来互动有价值的信息。
- 决策层与表达层解耦，避免“说得像人”等同于“行为像人”。
- 所有自动学习结果都应支持查看、修正和删除。
