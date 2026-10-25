# 控制台 UI 与原生服务契约

所有路径位于 `/v1/console`。读取需要主人会话；写入沿用同源 Origin、CSRF、
幂等键和原生操作回执。浏览器只提交主人选择，不执行工作进程。

## 日记与成长

页面并行读取 `life`、`goals` 和 `reflections`；日记正文按选中的日期读取
`diary/{day}`。目标与反思使用各自完整的原生字段。

- `goals.items` 包含 `goal_id`、`topic`、`state`、`version`、`updated_at`、
  `commitment_state`、`next_due_at` 和原始 `steps[]`。步骤包含 `step_id`、
  `position`、`state` 和原始结果引用，不返回目标 URL 或 authority 快照。
- `CANDIDATE` 可接受；非终态目标可按原生取消语义停止后续工作。
  `allowed_actions` 复用 `GoalEngine.plan()` 的续行判断。执行关闭、来源或
  authority 不可用、结果不明时不提供续行操作，历史仍可读取。
- `POST goals/{id}/decision` 提交 `{"decision":"ACCEPT"}` 或 `CANCEL`。
- `POST goals/{id}/continuation` 提交当前 `expected_version` 和 `action`。
  `CONTINUE` 的 `step_order` 是剩余 `WAITING_DECISION` 步骤 ID 的完整排列；
  UI 允许调整顺序。`WAIT` 提交带时区的 `wait_until`，最多等待 7 天；
  `ABANDON` 不带步骤或时间。版本变化时重新读取，不猜测版本、不重建步骤。
- `reflections.items` 包含原始 `suggestion_id`、`content`、`state`、
  `created_at` 和最多 3 条 `evidence[].exact_quote`。
  `PENDING` 可选择 `ACCEPT_AS_HYPOTHESIS`、`REJECT` 或 `NEED_MORE_EVIDENCE`，
  通过 `POST reflections/{id}/decision` 提交。接受仅保存为假设，拒绝或要求
  更多证据沿用原生服务的派生目标撤回行为。

## 思考与决策

页面读取 `cognition?kind=...&limit=30` 的八类原生记录：
`wake/life/goal/diary/reflection/nightly/model/rem`。

- 每条记录的 `id` 只用于身份与选择；`occurred_at` 来自原生记录的保存时间，
  用于排序、显示和按配置时区计算本页今日数量。`as_of` 是读取时间。
- 统计只涵盖已加载记录；服务端 `truncated` 为真时提示有更早记录未展示。
- 每行状态取自该行；Wake 的 `choice` 表示调度决定，不代表执行完成。
- 任一类型读取失败时整体显示同步失败，保留已有成功快照及其读取时间。
  首次读取失败显示错误，不显示“还没有记录”。
- 来源展示 `source_event_ids`，详情展示已返回的有界元数据。
  `reasoning=NOT_EXPOSED`、`candidate_set=NOT_RECORDED` 的边界保持不变。

## 能力授权

`PUT autonomy/{capability}` 提交 `mode`、`allow_pause` 和 `ranges`。
数值保持 JSON 数字类型：次数仍为整数，表情选择概率可为小数。
后端继续依据原生字段类型及主人配置校验上下界，拒绝布尔值、数字字符串、
整数参数的小数及越界值。`wired` 继续说明哪些字段已被运行逻辑读取。

## 后续 UI 覆盖清单

| 优先级 | 页面能力 | 后端与控制台接口情况 |
|---|---|---|
| P1 | 来源授权列表、状态、撤回、遗忘 | `source-grants` 已有读取与写入接口 |
| P1 | 工具任务列表、审批、拒绝、撤回 | `tools` 已有操作接口；审批的具体执行内容需补安全投影，当前仅有范围哈希与任务元数据 |
| P1 | 生活话题、来源、结果、关闭 | `life/topics` 已有预览与关闭接口 |
| P1 | 认知历史分页、单条定位 | `cognition` 已有 `before`、`next_cursor`、`record_id`，UI 当前只加载每类最近 30 条 |
| P2 | 情绪快照、重置与结果反馈 | `affect` 已有快照与重置接口 |
| P2 | 主人偏好与她的兴趣分开展示 | `interests` 已有只读接口 |
| P2 | 表达习惯学习快照与当前偏好 | `expression-learning` 已有只读接口；跨日历史须核对原始数据是否保留 |
| P2 | 目标步骤与执行回执的可读详情 | `cognition` 已有部分元数据，UI 目前以原始字段展示 |

记忆原文下钻、QQ 资料计划审批、表情素材管理和媒体会话控制，需要另外核对并
接出对应的主人控制台投影与操作。音频回放涉及原音频保留机制，不属于单纯 UI 接入。

## 实现依据与验证

- 检查 [DSH schedule 的历史读取实现](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/schedule/schedule/src/delivery-history.ts)：
  复用原始身份与保存时间分别建模、按原始回执读取、明确缺失历史的做法。
- 依照 [MDN Promise.allSettled](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/allSettled)
  的失败语义，整页统计使用 `Promise.all`，由已有加载错误路径处理失败。
  时间解析只使用原始日期字段，遵循 [Date.parse](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Date/parse)。
- 复用 [Pydantic 严格数字类型](https://docs.pydantic.dev/latest/concepts/strict_mode/)
  与原生 `delegation.validate_ranges`；保留整数/小数，不将字符串或布尔值强转为授权数值。
- `tests/test_console_cognition.py`、`test_console_decisions.py`、`test_delegation.py`
  覆盖真实 HTTP 契约、原始时间、版本冲突及幂等性；`test_console_browser.py`
  在浏览器点击实际按钮并检查原生存储，覆盖排序、状态及请求失败提示。
