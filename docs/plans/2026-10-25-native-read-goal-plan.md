# 原生有限多步 read-goal：复用提案

状态：2026-10-25 完成只读研究，提交 Root 决定；尚未修改 Goals/Reader 域。
基线包含 Root `77687e7`。迁移号 0045 由 R1 预留，本文不占用或提交迁移。

## 确认的缺口

任务书 P3.2 要求分步规划并根据结果调整后续步骤。目前：

- `migrations/versions/0008_goals.py` 对 `goal_steps.goal_id` 设置 UNIQUE，数据库只能存一步。
- `GoalEngine.accept()` 只创建一条 step；investigate 没有具体 URL 时进入 WAITING_INPUT。
- `GoalEngine.finish()` 校验真实 observation/URL/来源后同时把 step 和整个 Goal 标为 DONE。
- `ExplorationQueue.schedule_goal()` 已把原 step 快照交给 Reader，具备真实回执、租约和权限边界。
- `MemoryProjections._goals()`、cognition Goal 读取与 Life 结果消费都有单步读取假设。
- `ExplorationQueue.recover()` 对已开始请求记录 UNKNOWN，却仍可能调用可重试的 Goals.fail；
  `reading.py` 的失败路径也传 retryable=True。多步实现不能把这些未知执行转换成新 step 重读。
  应先在原恢复/失败边界阻止未知责任重放，不另设“不确定但可继续”的规划规则。

## 一手实现比较

已实际阅读以下源码/官方文档（2026-10-25，DSH master）：

| 来源 | 观察 | 本项目采用范围 |
| --- | --- | --- |
| [DSH todo_write](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/todo/tool-todo/src/index.ts) | 按会话保存完整清单替换事件，校验唯一条目和单个 in_progress；completed 是模型填写状态 | 采用有限清单、单活动项的结构原则；不把模型 completed 当执行证明，也不复制其清单存储 |
| [DSH plan-mode](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/plan/plan-mode/src/index.ts) | 规划状态可恢复，但只是提示；执行权限另行约束 | 计划和执行权限分离；每一步仍验证原 OwnerAuthority，不因计划文本得到权限 |
| [DSH workflow-ptc](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/workflow/workflow-ptc/src/index.ts) 与 [host](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/master/packages/workflow/workflow-ptc/src/host.ts) | 有总量/并发上限、持有者取消及子任务清理；运行控制包含进程内 Map/Set，持久记录由外层消费者负责 | 采用有限执行与终态责任原则；不引入 JS 编排、子 agent 或第二个工作流引擎 |
| [Temporal Workflow Execution](https://docs.temporal.io/workflow-execution) | 持久历史驱动工作流恢复 | 复用本项目已有 SQLite step/request/receipt 和检查点身份；不部署第二个协调服务 |
| [Temporal Activity Execution](https://docs.temporal.io/activity-execution) | 超时可能发生于已执行但未取得结果；重试策略决定是否再次尝试 | 已知未发送才允许重试；已派发未知保留原责任，本项目不套用默认自动重试 |

上述比较支持复用现有 Goal/Reader 状态机。它们不替代本项目的来源擦除、范围授权或费用合同。

## 推荐的最窄范围

首版只允许 self read-goal，1–3 个具体、已授权 HTTPS 目标；串行执行。
每个目标均为原生 `read_content`，使用已有适配器、请求计数/字节预算和 Reader 回执。
不接受任意工具、代码、网页提取的“新指令”、无限搜索或递归子目标。

保留原 `read_content` 与 `investigate` 范围身份；新增 `read_plan` 范围只用于新 self Goal，
其内容为 1–3 个去重的准确目标 URL。单目标与多目标统一到同一 step 引擎。
这保留已有承诺的原 scope JSON/hash，避免为迁移重新解释主人接受的合同。
每个 step 的执行快照仍使用现有 `read_content + url` 结构，外部 Reader 协议不变。

模型提供有限候选计划或续行建议；原生代码验证、保存和决定是否可执行。
建议通过已有 Life 结构化决策的消费者调用 Goal 方法，Social 负责该契约生产端。
不增加独立 planner 轮询、后台模型场景或新的付费计数器。
Root 需要协调这项小型契约扩展；本文不编辑 LifeTopics/Social。

## 数据与迁移必要性

0045 必须移除每 Goal 一步的 UNIQUE 并补有界 step 身份，不能只改 Python 循环。
建议 R1 采用原表迁移：

- `goal_steps` 增加顺序位置与每步 `scope_json`，唯一 `(goal_id, position)`；每 Goal 最多 3 步由事务验证。
- 增加用于未获续行决策的等待状态；第一步可排队，后续默认等待，不能被通用 claim 同时领走。
- 每步实际使用的来源用规范化 `goal_step_evidence(step_id,event_id)` 关联。
  原 `goal_evidence` 继续保留创建时的原始依据；结果来源不会改写创建授权。
- 复用 `goals.version` 做计划/续行操作的并发条件；每个 step_id 一经执行不变。
  不另建 plan-run 表、队列或模型会话。
- 旧 step 迁为位置 1，从父 Goal 原范围填入单步 scope，复制原证据关联。
  保存原 step_id、租约、次数、结果、UNKNOWN、Goal authority 和承诺 hash。
  非空多步状态不得静默降级为旧单步 schema；降级失败应明确。

新范围和 step 状态的精确字段名由 Root/R1 在实施前冻结；这是必要的持久结构变化，
不是把任意新协议称为适配器。可行备选是串联多个独立 Goal，但会丢失父目标总体完成条件、
准确取消与原授权范围，因此不推荐。

## 每步执行与调整合同

1. 创建时保存原 authority、原来源、去重且有序的具体目标集；同一原 proposal 复用身份。
   每次 claim、创建 Reader request、实际派发、结果提交都核验原引用仍有效。
2. 每步绑定原 step_id、目标、原始来源及必要的已完成前步 observation；
   exploration request 仍保存原 `goal_step_json`。来源关联随原 ErasureLedger 一同失效。
3. 只有原 Reader 的 FETCHED/WATCHED、有匹配目标与来源闭包、对应原 request 的真实结果才能完成一步。
   模型结论、Diary、REM、DISCOVERED 和其他 step 的结果都不能代替回执。
4. 步骤完成先原子持久化回执，再进入等待续行。允许建议 CONTINUE、调整尚未开始步骤的顺序、
   WAIT（有限 next_due_at）或 ABANDON。调整限定于原目标集合，且不能改已领取/已执行步骤。
   新 URL 需要新的范围与原生授权判定，不借旧 Goal 扩大执行面。
5. 全部计划步骤有合格完成回执才允许整个 Goal DONE；单步成功不提前完成父目标。
   放弃使 Goal CANCELLED，已完成步骤仍保留；不得把取消尾部包装成全部任务完成。
6. UNKNOWN/EXECUTION_UNCERTAIN 使续行停止。恢复不能创建新的 step/request 冒充重试，
   只能通过已有合法对账路径消除未知责任；若该域没有可证明解决路径，就维持不可继续。
7. scope、授权版本、来源撤回或 STOP 使未来工作退出；已发生调用与未知责任保留。
   过期 lease 在确认未派发时才可重新领取。
8. commitment 维持现有单目标合同：首版拒绝自主多步/续行修改 commitment，
   反馈和 SENT 仍独立于 Goal/step 完成，不由模型调整承诺内容、截止期或接受范围。

## 精确改动边界

| 文件/域 | 责任与必要改动 |
| --- | --- |
| `companion/goals.py` | 本片：有限计划验证、原子 accept/claim/finish/续行、等待/取消与 UNKNOWN 防重放；不创建第二执行器 |
| `companion/exploration.py` | 本片：按每步 scope 建原请求；恢复和失败不重放 UNKNOWN；原请求身份不变 |
| `companion/reading.py` | 与 Tools 冻结内部回调合同后：把明确失败与执行未知交回不同原生状态；外部传输协议不变 |
| `companion/memory_projections.py` | 本片：遍历有界全部步骤，汇总实际完成/UNKNOWN，不能 `.one()` 选首步 |
| `companion/cognition_read.py` | Workflow：有界返回全部步骤与原回执；父 Goal 完成语义同步 |
| `companion/life_topics.py` | Social：更新单步 JOIN/结果关联及计划/续行结构化决策消费者，保留反馈所有权 |
| `companion/interest.py` | Social：多步成功回执与已有兴趣证据去重，不把逐步完成次数当独立偏好票数 |
| `migrations/versions/0045_*.py`、`companion/store.py` | R1：上述原表迁移、schema 能力检查、外键/索引及非空降级保护 |
| Core/API/config/budget/authority | R1：仅在冻结的范围/续行调用契约需要时修改；本片不抢改 |
| `tests/test_goal_read_plan.py`、`tests/test_goal_read_plan_process.py` | 本片：新域与真实入口/独立 worker 回归；原 Goal、Reader、投影与擦除相关测试按变动补充 |

## 实施门槛与验收

待 Root 确认有限 read_plan 范围、schema shape、Social 决策入口及 Tools 内部结果回调合同后实施。
单独“把一个 Goal 变成多个 step”不足以完成任务。

最少验收：真实入口创建两目标计划；第一步真实回执后仍未 DONE；有界续行选择后第二步完成；
顺序调整、等待、放弃；错误/跨步回执拒绝；重启不重做已完成步骤；派发后退出保留 UNKNOWN；
换授权/STOP/撤源及旧备份恢复；单步历史迁移与既有承诺不变；每个 selector turn 仍至多一个原生单元。
合成模型只证明上述合同。规划是否有用、依据结果调整是否合理以及长期任务体验需另做模型和真实验收。
