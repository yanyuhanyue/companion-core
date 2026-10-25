# P1–P4 控制台 UI 补充（2026-10-25）

此文件补充 console-v2 控制台自 `0b1d70c` 加入的 cognition/life/affect 路由与决策页面。

## 新增后端路由（companion/console_cognition.py）

已注册到 `/v1/console`，复用原有会话、CSRF、幂等键与操作回执：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/cognition?kind=...&limit=...` | 读取认知记录（wake/life/goal/diary/reflection/nightly/model/rem），按 `before` 或 `record_id` 分页 |
| GET | `/affect` | 情绪观察快照 |
| POST | `/affect/reset` | 重置情绪状态（需确认） |
| GET | `/goals` | 目标列表（仅可见字段，步骤 `steps` 含完成计数与原始回执） |
| POST | `/goals/{goal_id}/decision` | 接受 / 取消目标（ACCEPT / CANCEL） |
| POST | `/goals/{goal_id}/continuation` | 续行目标：CONTINUE 指定剩余步骤、WAIT 设置到期时间、ABANDON 放弃 |
| GET | `/reflections` | 反思建议列表（含 3 条证据摘录的 `exact_quote`） |
| POST | `/reflections/{suggestion_id}/decision` | 决定反思建议（ACCEPT / REJECT） |
| GET | `/life/topics` | 生活话题预览（含来源事件列表与执行结果） |
| POST | `/life/topics/{topic_id}/close` | 关闭话题（需提供来源事件 ID） |
| GET | `/interests` | 兴趣档案 |
| GET | `/expression-learning` | 表达习惯学习快照 |
| GET | `/tools` | 工具任务列表（最近 30 条） |
| POST | `/tools/{task_id}/decision` | 批准 / 拒绝工具任务 |
| POST | `/tools/{task_id}/revoke` | 撤回工具任务 |
| GET | `/source-grants` | 来源授权列表 |
| POST | `/source-grants/{grant_id}` | 撤回 / 遗忘来源授权 |

## 新增控制台页面（companion/console_static/app.js）

### 1. 日记与成长（PAGES.life）

- **左栏**：月历选择器，显示已有日记的日期。
- **右栏**：当天日记内容（懒加载），纠正、遗忘按钮。下方显示目标与承诺、最近反思建议列表。
- **数据源**：`GET /life`（`life.items`）、`GET /diary/{day}`（按需）。

**目标与承诺子区**：
- 显示 `kind === "goal"` 的条目，展示 `title`、`status`、承诺状态（`detail.commitment_state`）。
- **当前 UI 缺口**：无接受 / 取消 / 续行操作按钮；`steps` 字段已返回但页面未展开显示。

**反思子区**：
- 显示 `kind === "reflection"` 的条目，`title`、`status`、更新时间。
- **当前 UI 缺口**：无接受 / 拒绝按钮，也未展开证据 `evidence`（后端返回前 3 条 `exact_quote`）。

### 2. 思考与决策（PAGES.thinking）

- **顶部统计**：今天的决策总数、已执行、暂缓或跳过、失败。
- **工具栏**：按决策类型筛选（全部、自主活动、主动分享、QQ 资料、每日反思）。
- **左侧列表**：决策条目，点击后右侧显示详情。
- **右侧详情**：三栏分页（决策记录 / 上下文 / 原始推理）。
  - **决策记录**：触发原因、可选行动、她的决定、决策说明、执行结果。
  - **上下文**：本轮实际可用的信息（近期会话、项目进展、自主兴趣记录、本次工具调用），标注"已采用 / 未采用 / 无匹配配置"。
  - **原始推理**：显示思考模式内容（当前配置关闭 DeepSeek 思考模式，页面注明"未生成"）。
- **数据源**：`GET /decisions`。

**决策类型映射**：
```javascript
const DK = {
  wake: { name: "自主活动", fields: ["trigger", "available_actions", "decision", "reason", "result"] },
  sharing: { name: "主动分享", fields: ["source", "available_actions", "decision", "reason", "result"] },
  account: { name: "QQ 资料", fields: ["plan_type", "proposed_change", "decision", "reason", "scheduled_at"] },
  nightly: { name: "每日反思", fields: ["suggestions", "decision", "evidence", "result"] }
};
```

**上下文投影字段**：
- `recent_messages`：近期会话（已采用 / 未采用）
- `project_progress`：项目进展
- `autonomous_observations`：自主兴趣记录
- `tool_calls`：本次工具调用

### 3. 能力详情增强（capDetail）

每项能力详情顶部新增"由谁决定"一行，显示当前授权模式（只由我控制 / 她提议我批准 / 她在范围内自己调整）。列表里的能力名称旁会显示火花图标（已授权给她）和黄色圆点（有待批准的提议）。

## 数据结构示例

### GET /goals 返回示例

```json
{
  "items": [
    {
      "goal_id": "goal-abc123",
      "kind": "habit",
      "topic": "每周读一本书",
      "state": "ACTIVE",
      "priority": 2,
      "next_due_at": "2026-10-26T09:00:00Z",
      "last_error_code": null,
      "version": 3,
      "commitment_state": "COMMITTED",
      "deadline": "2026-12-31T23:59:59Z",
      "mode": "AUTOMATIC",
      "created_at": "2026-10-01T08:00:00Z",
      "updated_at": "2026-10-24T18:30:00Z",
      "reflection_suggestion_id": "refl-xyz789",
      "steps": {
        "remaining": ["选书", "安排阅读时间"],
        "completed_count": 1,
        "last_result": {
          "step": "确认兴趣方向",
          "outcome": "SUCCESS",
          "event_id": "evt-def456"
        }
      }
    }
  ]
}
```

### GET /reflections 返回示例

```json
{
  "items": [
    {
      "suggestion_id": "refl-xyz789",
      "run_id": "run-20261024",
      "kind": "habit",
      "content": "建议将读书目标的每周时长从 10 小时减少到 5 小时",
      "state": "PROPOSED",
      "created_at": "2026-10-24T06:00:00Z",
      "updated_at": "2026-10-24T06:00:00Z",
      "local_date": "2026-10-24",
      "evidence": [
        {"exact_quote": "最近工作太忙了，每天只能抽出 30 分钟"},
        {"exact_quote": "这周只读了 20 页，进度有点慢"},
        {"exact_quote": "要不要暂时放松一下目标？"}
      ]
    }
  ]
}
```

### GET /decisions 返回示例

```json
{
  "items": [
    {
      "id": "dec-20261025-wake-01",
      "kind": "wake",
      "occurred_at": "2026-10-25T15:42:00Z",
      "status": "COMMITTED",
      "detail": {
        "trigger": "昨天的项目 Bug 尚未解决",
        "available_actions": ["私聊 / 延后 / 跳过"],
        "decision": "DEFER",
        "reason": "缺少新的进展，当前主动问询可能造成打扰",
        "model_decision": "DEFER：查时不发",
        "execution_result": "未发送，保留待评估线索",
        "context": {
          "recent_messages": {"status": "USED", "count": 12},
          "project_progress": {"status": "USED", "items": ["Bug 讨论", "方案草稿"]},
          "autonomous_observations": {"status": "UNUSED"},
          "tool_calls": {"status": "NONE"}
        },
        "reasoning": null
      }
    }
  ]
}
```

## 当前 UI 缺口汇总

基于后端已返回字段，以下操作在 UI 上仍缺少交互入口：

| 能力 | 已返回字段 | UI 仍缺少的操作 |
|---|---|---|
| Goals | `steps.remaining`、`steps.completed_count`、`steps.last_result` | 接受、取消、续行（CONTINUE 指定剩余步骤、WAIT 设置到期、ABANDON 放弃） |
| Reflections | `evidence[].exact_quote`（前 3 条） | 接受、拒绝按钮 |
| Life topics | `sources`、`results` | 关闭话题按钮（需选择来源事件） |
| Tools | 任务列表 | 批准、拒绝、撤回按钮 |
| Source grants | 授权列表 | 撤回、遗忘按钮 |
| Affect | 当前快照 | 重置按钮 |

这些缺口不影响后端接口可用性，也不影响已有能力的配置和自主调整授权保存。接口已就绪，UI 交互可按需补全。

## 类型标注（已加入 app.js）

```javascript
/**
 * @typedef {Object} GoalSteps
 * @property {string[]} remaining
 * @property {number} completed_count
 * @property {{step: string, outcome: string, event_id: string}=} last_result
 */

/**
 * @typedef {Object} ReflectionEvidence
 * @property {string} exact_quote
 */

/**
 * @typedef {Object} DecisionContext
 * @property {{status: string, count?: number, items?: any[]}=} recent_messages
 * @property {{status: string, items?: any[]}=} project_progress
 * @property {{status: string}=} autonomous_observations
 * @property {{status: string}=} tool_calls
 */
```

## 实现状态标注

当前 `app.js` 中 `SHOW_IMPLEMENTATION_STATUS` 设为 `true`，在能力列表和详情中显示"尚未接入"、"部分接入"提示。设为 `false` 可移除全部实现标注；自主调整的接入状态（`wired` 字段）和火花图标、黄点仍保留。

---

**更新范围**：本文档记录 console-v2 在 `0b1d70c` 后新增的后端路由与 UI 投影。目标、反思、话题、工具、来源授权和情绪观察的接口均已实现，UI 交互按钮仍缺少。思考与决策页面已完整展示决策记录、上下文与推理占位，按后端实际返回字段渲染。
