# 控制台 UI 与原生服务契约

所有路径位于 `/v1/console`。读取需要主人会话；写入沿用同源 Origin、CSRF、
幂等键和原生操作回执。浏览器只提交主人选择，不执行工作进程。

## 日记与成长

页面并行读取 `life`、`goals` 和 `reflections`；日记正文按选中的日期读取
`diary/{day}`。目标与反思使用各自完整的原生字段。
`life/topics` 展示有效来源和草稿；`CLOSED/CONSUMED` 的话题仍可关闭，以撤回尚有效的草稿。

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
- 按类型使用 `next_cursor` 读取更早记录；`#thinking/<类型>:<编号>` 使用
  `record_id` 定位并选中原始记录。翻页结果受当前加载代次限制，隐私版本改变时
  重新读取，刷新后的记录不会被先前发出的分页请求覆盖。

### 缓存用量后端契约（前端待接入）

本节是后端交付的前端对接约定。缓存三态尚未接入前端，前端实现与验收待独立开展。
现有 Console 使用方式见上文；本次复用 `GET /v1/console/cognition?kind=model`，
定位单次请求可加 `record_id=<request_id>`，分页沿用 `before` 和 `limit`（1–50，默认 30）。
请求 ID 来自原模型记录，前端不构造新的模型请求，也不从当前模型配置补写历史。

每个 `items[]` 的原 `usage` 对象新增 `cache_usage`；只读 Inspector 的
`trace.jobs[].model_calls[]` 返回同一个投影。Inspector 同时令原
`prompt_cache_hit_tokens` 等于该投影的 `cached_input_tokens`，未知时为 null。

| 字段/组合 | 后端含义与前端接入约定 |
|---|---|
| `cache_usage.state="RECORDED"` 且 `cached_input_tokens=0` | 有效原回执明确返回受支持缓存字段且为 0；可显示明确的 0 |
| `cache_usage.state="RECORDED"` 且 `cached_input_tokens>0` | 同一有效原回执的缓存输入 Token；可显示该非负整数 |
| `cache_usage.state="NOT_RECORDED"` 且 `cached_input_tokens=null` | 缓存观察未知；应表示未记录，不把 null 转为 0 |
| 老响应缺少 `cache_usage`、未知状态或无效状态/值组合 | 按未记录处理，不据原数值列推断缓存命中 |
| `usage.cached_input_tokens` | 保留原财务数值列与结算默认值；0 本身不能证明 provider 明确返回 0 |
| `usage.observed`、`input_tokens`、`output_tokens` | 原有效实际用量判定和计数；缓存未知不影响它们 |
| `usage.billed_micro_usd`、`is_estimate` | 原已计/预留责任金额与估算身份；UNKNOWN/RESERVED 的责任金额不冒称实际账单 |

`RECORDED` 要求原已结算用量及已发送 admission 有效、恰好一条原请求诊断、
诊断 `actual_usage_known` 与 `cache_usage_present` 严格为 true，且输入/输出/缓存/金额
与原记录一致。缺失、损坏、超长、重复或不匹配诊断只使缓存观察未知，不隐藏已知的
输入、输出或配置费率金额。前端直接消费这一个后端投影，不从最新诊断、SDK 计数、
原财务 0 或当前 provider 设置另行推断。

内部 `model.usage_diagnostic.safe_detail_json.cache_usage_present` 仅是有界观察：
收到原 usage 对象且含受支持的 `prompt_cache_hit_tokens` 为 true，收到该对象但缺字段
为 false；无 usage 对象、旧数据或人工响应未记录为 null。仅严格 bool/None 被保留，
数字或字符串标记变为 null。存在标记不证明数值有效；前端无需读取此内部诊断。
本字段不证明缓存隔离、缓存策略或供应商真实账单。

下列为合成后端 fixture 的 `items[].usage` 响应片段；省略其余记录与分页字段。
三例的有效回执输入/输出均为 80/3，金额按测试配置费率计算（微美元）。

```json
{
  "input_tokens": 80, "output_tokens": 3, "cached_input_tokens": 0,
  "billed_micro_usd": 86, "is_estimate": 0, "observed": true,
  "cache_usage": {"state": "NOT_RECORDED", "cached_input_tokens": null}
}
```

```json
{
  "input_tokens": 80, "output_tokens": 3, "cached_input_tokens": 0,
  "billed_micro_usd": 86, "is_estimate": 0, "observed": true,
  "cache_usage": {"state": "RECORDED", "cached_input_tokens": 0}
}
```

```json
{
  "input_tokens": 80, "output_tokens": 3, "cached_input_tokens": 40,
  "billed_micro_usd": 50, "is_estimate": 0, "observed": true,
  "cache_usage": {"state": "RECORDED", "cached_input_tokens": 40}
}
```

来源：`tests/test_cache_usage_presence.py` 的实际 ModelClient → budget.complete → 两个读取入口
断言；`tests/test_cache_usage_process.py` 另覆盖签名 HTTP 入站、独立 worker、真实 TLS 主人
会话读取及 ACK。外部模型和 OneBot 传输使用 Mock；这些是后端证据，前端三态验收尚未完成。

认证与错误沿用原契约：

- HTTPS 主人会话使用 Secure/HttpOnly/SameSite=Strict cookie；业务读取沿用会话，
  不新增控制 Token 下发或持久化。Console 读取不新增写操作，现有写入的
  Origin/CSRF/幂等约束继续生效。
- 响应保持 `Cache-Control: no-store`。失效来源可使原记录从 `items[]` 消失；不可用记录
  不回填内容。请求前后仍校验 privacy epoch 与最新 ErasureLedger custody。
- `privacy_epoch` 变化时丢弃旧分页与旧详情，按当前加载代次重读；保留 `as_of` 以标识
  快照时间。不得拼接不同代次的来源，也不得将读取失败显示成未知缓存的成功响应。
- 401 `CONSOLE_LOGIN_REQUIRED` 重新认证；404 `CONSOLE_DISABLED`、503
  `CONSOLE_OWNER_CREDENTIAL_REQUIRED` 按现有不可用状态处理。
- 400 `INVALID_COGNITION_QUERY` / `INVALID_COGNITION_SELECTION` 修正查询；
  `before` 与 `record_id` 不同时提交。409 `COGNITION_CURSOR_UNAVAILABLE` 重新读取首页；
  409 `PRIVACY_CHANGED` 丢弃旧结果并重读。其余失败沿用现有同步错误处理。
- `NOT_RECORDED`/null 是成功读取内的字段状态，不是 HTTP 错误；成功空 `items[]`
  也不证明模型从未被调用。

后续前端验收需分别覆盖上述三个响应、缺字段/无效组合、已知输入输出费用持续可见、
认证失效与版本变化；本次保留现有前端原实现，不交付界面改动或前端验收结论。

## 能力授权

`PUT autonomy/{capability}` 提交 `mode`、`allow_pause` 和 `ranges`。
数值保持 JSON 数字类型：次数仍为整数，表情选择概率可为小数。
后端继续依据原生字段类型及主人配置校验上下界，拒绝布尔值、数字字符串、
整数参数的小数及越界值。`wired` 继续说明哪些字段已被运行逻辑读取。

## 已接入的 UI 覆盖清单

| 优先级 | 页面能力 | 后端与控制台接口情况 |
|---|---|---|
| P1 | 来源授权列表、状态、撤回、遗忘 | 阅读与文件页的来源授权标签，复用 `source-grants` 操作回执 |
| P1 | 工具任务列表、审批、拒绝、撤回 | 执行任务页展示与 `scope_sha256` 绑定的范围；必要参数被遮蔽时禁止批准，保留拒绝和撤回 |
| P1 | 生活话题、来源、结果、关闭 | 日记与成长页复用 `life/topics`；关闭可使已有草稿失效 |
| P1 | 认知历史分页、单条定位 | 按类型翻页及 `record_id` 定位，保留读取失败与隐私版本校验 |
| P2 | 情绪快照、重置与结果反馈 | 情绪与偏好页展示开启状态、证据和重置结果 |
| P2 | 主人偏好与她的兴趣分开展示 | 分区展示 `interests` 返回的两类原生资料 |
| P2 | 表达习惯学习快照与当前偏好 | 展示当前结果及证据日期；原生服务未单独保存逐日快照 |
| P2 | 目标步骤与执行回执的可读详情 | 认知记录展示原始步骤、尝试次数和执行回执，明确结果不明 |

工具范围只在来源非空且有效时返回，读取前后复用原生隐私版本检查。
失效来源不返回参数正文，读取期间发生遗忘返回 `PRIVACY_CHANGED`。

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
- `tests/test_console_supplement_browser.py` 通过真实 HTTPS 验证来源撤回与遗忘、
  草稿关闭、情绪重置、工具批准/拒绝/撤回及遮蔽内容禁止批准。
