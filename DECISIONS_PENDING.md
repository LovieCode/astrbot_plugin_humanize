# 待用户决断清单（WebUI 全面测试）

> 测试期间发现的「需要用户决策」的问题统一记录在这里。
> 全部测试完成后由用户统一审批。

## 1. BUG-6：卡片「有冲突义项」标签语义混淆

- **现象**：词条有 2 个普通 candidate 义项（无真歧义）时，卡片也显示「有冲突义项」
- **根因**：后端 `has_conflict` 语义 =「>1 个非 rejected 义项 且（有 pending 或 0 个 verified）」——即「多个未确认义项需人工处理」，不是真冲突（歧义）
- **选项**：
  - A. 改前端文案为「多义项待处理」（反映后端真实语义）
  - B. 改后端 `has_conflict` 为「真歧义」（需明确歧义判定规则）
  - C. 保持现状

## 2. 协议日志（protocol-logs）前端无入口

- **现状**：后端已有 `GET protocol-logs`（分页返回协议校验日志，含 success/failure_code/stage），前端 7 个视图**没有对应页面/入口**
- **选项**：
  - A. 新增「协议日志」视图（列表 + 分页 + 状态筛选）
  - B. 挂到现有上下文视图作为子区块
  - C. 暂不做（后台查询足够）

## 3. 分页无法实测

- **现状**：线上词条数 < 11，无法验证翻页内容变化（控件渲染正确、逻辑存在）
- **选项**：
  - A. 构造 ≥11 条临时数据测试后删除
  - B. 信任代码逻辑，不实测

## 4. 黑话侧边统计「细分状态暂无统计接口」

- **现状**：侧边统计卡片写死说明「已验证/歧义/已拒绝 等暂无统计接口，仅展示总数与待审核数」
- **选项**：
  - A. 补后端统计接口 + 前端细分展示
  - B. 保持现状（明确标注）

## 5.（黑话已修复）5 个阻断级 bug——已修复并部署

**状态**：已修复（commit `7f2f813`），无需用户决断
- BUG-1 modal 提交崩溃（closeModal 清 DOM 时序）→ 不清 DOM
- BUG-2 抽屉底部按钮无响应（footEl 选错元素）→ 限定 drawer 作用域
- BUG-3 义项编辑/合并/设为首选无反应（detail 缺 senses）→ detailData 存完整数据
- BUG-4 操作后抽屉不刷新（detail.id 缺失）→ fallback entry.id
- BUG-5 二次进入顶栏失灵（topbar 重建丢绑定）→ document 事件委托
- 额外：modal 与抽屉遮罩冲突（openModal 关遮罩/closeModal 恢复）

**验证**：scripts/_smoke_jargon.py 9 项全过 + smoke_spa.py 回归通过 + 远端热重载

## 6.（上下文已修复）3 个核心 bug + 2 个小问题——已修复并部署

**状态**：已修复（commit `b949023`），无需用户决断
- 点击运行卡片不切换详情 → listEl 事件委托 openDetail
- 分页器点击无效 → pagerEl 事件委托切页
- 作用域筛选无效（seg 无绑定 + private_user vs private 契约不符）→ 绑定 loadList + 改值
- 省略原因英文 code → OMIT_REASON_LABEL 中文映射
- 复制按钮 iframe 下必失败 → textarea + execCommand fallback

## 7.（示例已修复）2 个构建级 id 冲突——已修复并部署

**状态**：已修复（commit `2856394`），无需用户决断
- BUG-1: fill('#recallAgent') 字符串实参漏改名 → 召回 Agent 下拉永远空 → 改 #ex-recallAgent
- BUG-2: examples 弹窗 mScope/mAgent 与 memory 静态弹窗同 id → 全字段加 ex- 前缀
- 根因：build_spa.py rename_js 不改写字符串实参/运行时 id —— 已在前端源码层面规避

## 8. 示例视图无法端到端实测删除/编辑/分页

- **现状**：远端回复样例库 0 条，删除/编辑/翻页链路无法真实触发（代码已审读）
- **选项**：A. 构造临时样例数据测完删除；B. 信任代码逻辑

## 9.（记忆+设置已修复）4 个严重 bug——已修复并部署

**状态**：已修复（commit `f753a2c`），无需用户决断
- 记忆抽屉永远空白 + 空指针 → HTML 补全详情容器结构（dAbstract/dContent/confVal/eviRows 等）
- 抽屉 foot 按钮（编辑/标记 rejected）无绑定 → 绑定 openEditModal/confirmReject
- jobs 分页无效（按钮改 state.page 非 jobPage）→ renderPager 加 stateKey 参数
- 设置保存后读回旧值（内存未同步）→ 保存后重建 in-memory PluginConfig

## 10. 待决断：遗留临时记忆数据

- 补测 agent 创建的候选记忆「探针临时记忆-测完删除」（id `a8024d…`）**无法通过 WebUI 删除**（后端 OpenViking identity immutable，UI 无删除出口）
- **选项**：A. 后端/DB 手工清理（需你授权）；B. 保留（1 条候选不影响）

## 11. 待决断：记忆删除/拒绝的 UI 出口

- 后端 `memory-action` reject 报 `OpenViking memory identity is immutable`——**记忆创建后不可删除/拒绝**（设计约束）
- **选项**：A. 保持（记忆不可变，仅 superseded）；B. 后端加「软删除/隐藏」机制 + UI 出口（需你确认设计意图）
