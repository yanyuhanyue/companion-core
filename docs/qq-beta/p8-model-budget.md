# P8-A 模型与预算预接入合同

P8-B 增量：本文件保留 P8-A 固定代码的缺口与测试快照；B1 独立 Draft 候选
补齐的离线门禁不能当作真实 provider 准入。所有真实模型、费率、额度与账户控制仍未确认，
见 [p8b-approvals.json](p8b-approvals.json) 的 `paid_model`；缺字段即 BLOCKED。
新增持久账本随非空 DB/最新 Ledger 一起纳入独立恢复申请，不能恢复旧 DB 后重新获得额度。
供应商最终扣费须独立核账，单库软件预算不覆盖同账号的其他数据库/客户端。

证据等级：`OFFLINE`；输入均为 `SYNTHETIC`。审计基线为
`940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`。本文件是关机状态的审查材料，
不是模型、费用、QQ 发送或 M1 的批准。`paid_model_requests=0`，
`mode=demo`、`delivery_enabled=false`、`memory_enabled=false`、`reader_enabled=false`。
Reader、日记、自主分享、执行器、媒体与社交写入继续关闭。没有修改运行配置。

## 待确认的真实调用合同

以下每项均需要主人单独确认；待确认值不能直接用于生产配置。代码/demo 模板中的默认值、
测试夹具费率和 Stub 的 `mode=live` 分支均不代表真实选型、价格或调用授权。

| 合同项 | 本轮真实值/授权 | 配置或验收要求 |
| --- | --- | --- |
| 提供方/计费账户 | `PENDING_OWNER_CONFIRMATION` | 复用现有适配器；不创建新账户或付费调用 |
| 明确模型 ID、版本及能力 | `PENDING_OWNER_CONFIRMATION` | 确认聊天模型、是否支持禁用 thinking 与 usage/cache 字段 |
| API 基址与最终 `/chat/completions` 终端 | `PENDING_OWNER_CONFIRMATION` | 确认 HTTPS、服务商及数据处理范围；不由默认字符串推定可用性 |
| 服务/数据处理地区 | `PENDING_OWNER_CONFIRMATION` | 主人确认供应商条款及实际区域 |
| 输入、缓存输入、输出费率及币种 | 每项 `PENDING_OWNER_CONFIRMATION` | 必须说明每百万 token 的计费单位、有效时间、上界与证据 |
| 税费、汇率、账单舍入及额外费用 | `PENDING_OWNER_CONFIRMATION` | 当前整数 micro-USD 估算不是供应商账单 |
| 密钥所有者、保管位置与轮换责任人 | `PENDING_OWNER_CONFIRMATION` | 仅私有运行环境注入 `COMPANION_MODEL_KEY`；文档/聊天/PR/CI 不存值 |
| 单次输入/输出硬 token 限额 | 每项 `PENDING_OWNER_CONFIRMATION` | 需要输入硬门禁与输出上界；不能把 soft context 当硬上限 |
| 单次费用硬上限 | `PENDING_OWNER_CONFIRMATION` | 当前聊天路径无独立单次费用门禁，见下表 |
| 当日费用/请求次数硬上限与时区 | 每项 `PENDING_OWNER_CONFIRMATION` | 当前聊天无全局日费用/日调用次数门禁 |
| 月费用硬上限 | `PENDING_OWNER_CONFIRMATION` | 复核所有费率后设置现有月上限；含 RESERVED/UNKNOWN |
| 本次窗口累计/总费用与尝试次数硬上限 | 每项 `PENDING_OWNER_CONFIRMATION` | 当前无跨月/批准窗口累计硬上限 |
| 全局并发上限 | `PENDING_OWNER_CONFIRMATION` | 当前无所有模型调用共享的 provider semaphore/分布式并发门禁 |
| 调用窗口、停止责任人、异常扣费处理 | 每项 `PENDING_OWNER_CONFIRMATION` | UNKNOWN 保留费用，暂停新增付费调用，人工核账；不盲目重试 |

这些值未齐备，或下述缺失硬门禁未实现并离线验证时，P8-B 付费路径保持 `BLOCKED`。
即使主人批准了某个金额，本文也不会使缺失的门禁自动生效。QQ、M1、部署授权另行处理。

## 复用的实际代码与边界

- [ModelClient](../../companion/model.py) 唯一负责模型 HTTP 请求与回包解析；
  使用配置模型名，非流式请求，发送 `max_tokens`；DeepSeek 分支附带禁用 thinking 的字段。
  它不持有 SQLite、权限或 QQ 发送职责。模型接口是否接受这些字段尚未实测。
- [Settings](../../companion/config.py) 支持环境变量覆盖 TOML、`SecretStr` 密钥、
  live 模式非空密钥检查、URL 校验、超时/lease 配合校验。
  现有 URL 校验允许 HTTP；生产端点确认与 HTTPS 要求不能由它代替。
- [Core.context / run_once](../../companion/core.py) 使用现有聊天 FIFO、上下文与预算路径；
  只有确认 `SENT` 的历史 assistant 回复进入上下文，未知发送不冒充已读到的回复。
  保留完整当前输入和系统规则，按保守 UTF-8 字节界裁剪较早资料；这是软限制。
- [worker](../../companion/__main__.py) 使用 `trust_env=false` 的 HTTP 客户端并设置超时，
  CLI 通过 [single_worker](../../companion/worker_lock.py) 限制同一 DB 的同类 worker。
  这不构成所有 worker、进程、数据库或提供方账户的统一并发/费用限制。
- [privacy.redact](../../companion/privacy.py) 在入库及模型回复入 Outbox 前遮盖已配置凭据和
  常见密钥/密码模式。它不是通用个人信息匿名化：普通聊天内容仍会在获批 live 时进入模型请求。
  M1 的敏感候选拒绝、遗忘与证据合同继续复用，当前 `memory_enabled=false`。

## 已实施与缺失的预算门禁

金额使用整数 micro-USD；配置费率单位是 micro-USD / 百万 token。
`cost = ceil(((input-cached)*input_rate + cached*cached_rate + output*output_rate)/1,000,000)`。
预留时缓存命中按零估计，输入采用保守字节界，输出采用场景配置上限。
上述公式只描述代码，不填入任何真实费率。

| 门禁/结算 | 当前实现 | 对 P8-B 的含义 |
| --- | --- | --- |
| 前置预留 | `budget.reserve` 在 SQLite 事务内检查并写入 `RESERVED`，之后才发模型请求 | 已有；本轮 Stub 在调用内检查了 RESERVED 可见 |
| 共享月费用 | 所有 `model_usage` 与 `tool_tasks` 的 `billed_micro_usd` 自本地月起点求和，含 RESERVED/UNKNOWN；新预留超月限拒绝 | **仅在三种费率全部配置时启用**；不能替代日/单次/累计上限 |
| 自主场景日费用 | memory_extract/review 合并日限额；diary、activity、reflection 各有日限额；reflection 另有月限额/日次数 | 已有，但全部相关能力关闭；**不覆盖 chat 的全局日费用** |
| Chat 日费用/日次数 | 无独立全局硬门禁 | `MISSING — BLOCKS_PAID_ACTIVATION` |
| 单次费用 | 无 chat 独立 `max_request_micro_usd`；只有预估 token 与共享月上限 | `MISSING — BLOCKS_PAID_ACTIVATION` |
| 单次 token | 输出请求带 `max_tokens`；当前输入是 20,000 字符协议上限与 soft context，系统规则+当前输入可超过 soft 值 | 输入硬门禁 `MISSING — BLOCKS_PAID_ACTIVATION`；供应商 token/计费上界待确认 |
| 批准窗口/跨月累计总费用 | 无独立累计 cap，月度窗口会滚动 | `MISSING — BLOCKS_PAID_ACTIVATION`；不能把 `total_monthly()` 名称当 lifetime total cap |
| 全局模型并发 | 同类 CLI worker 的 DB OS 锁、同一会话 FIFO、worker 内逐次 await | 无统一 provider 并发硬门禁：`MISSING — BLOCKS_PAID_ACTIVATION` |
| 未知费率 | 非 chat 拒绝 `UNKNOWN_PRICING`；chat 仍允许，费用暂记 0 并标 `pricing_known=0` | 不是免费调用；必须独立阻止未核实费率的付费启用 |
| demo 零费率 | demo 模板三项费率和月预算均为零；`pricing_known` 判断是是否为 None | 仅改 mode/填 key 不安全：零估价不会触发月费用阻断；必须独立批准并验证 live 配置 |
| 成功且 usage 完整 | 替换预留 token/费用，状态 `SETTLED`；缓存输入最多为 prompt token 数 | 仍是配置上界估算 `is_estimate=1`，并非已核账 |
| 超时/429/5xx/异常/usage 缺失 | 预留转 `UNKNOWN`；保留原非零估价，不能退款为 0 | 已有；后续月限额仍计入它 |
| Worker 崩溃 | 到期 job 的 RESERVED 转 UNKNOWN，后续尝试需重新预留 | 已有；一次模型重试可能再次收费，不得与发送重试混同 |
| 模型重试 | `MODEL_RETRYABLE` 有限重试，最多配置的 3 次，每次独立预留；401/无效回包不自动重试 | 当前没有批准窗口级尝试总限额；上线前确认策略 |
| Outbox 未知发送 | `SENDING` 到期进入 `DELIVERY_UNCERTAIN`，不会自动回待发；只有已验证回执或 owner 核对结算 | 保留现有单一发送系统；UNKNOWN 模型费用与未知发送状态分别保留 |

现有月限额是在请求前按配置费率/估计 token 进行准入，并非供应商账单的绝对硬保证。
若供应商实际费率、实际输入 token 或返回 usage 超出估计，结算会高于预留；当前不会追回已发生费用。
提供方账单/账户级封顶也必须确认，不能用本地测试替代。

## 本轮离线验证与观察

[离线回归清单](p8-offline-regressions.md) 记录实际命令和结果。
[新增测试](../../tests/test_p8_readiness.py) 仅使用 FakeModel、MockTransport、合成 WS 和临时 SQLite。
两个 `mode=live` 的测试分支仅用 `httpx.MockTransport` 截获请求，域名是 `.invalid`，
没有加载新 key 或访问供应商。没有把这类分支当 live 模型证据。

覆盖：已发送/未知回复的多轮上下文差异、当前输入超 soft 限额、入/出站凭据遮盖、
前置 RESERVED、失败与 crash 后 UNKNOWN 非零保留及再次准入被月限额阻断、
请求载荷、缓存上界、usage 缺失与 401/无效回包。既有测试另覆盖 429/5xx/timeout
最多三次、月额度不足时模型零调用、M1 敏感/冲突/遗忘证据及旧备份 replay。

观察现有 `status().usage`、匿名阶段计时、`pricing_known`、`is_estimate`、
RESERVED/UNKNOWN 数量与金额，不记录正文、Authorization 或 provider 原始错误。
`SETTLED` 不等于账单核验，OneBot ACK 计时不等于手机 E2E。

可撤销方案仍为审批建议：暂停新增付费模型 worker/入口，保持发送关闭并保留 DB、Spool、
最新独立 Ledger 和所有费用记录；先核对 in-flight/UNKNOWN，再安排获批停机/密钥撤销。
本轮未执行服务启停或密钥操作。仅改配置文件 `mode=demo` 不会自动取消已在运行中的请求。

## 后续聊天质量验收问题

以下是获批后的真人问题提纲；本轮全部 `NOT_RUN`，固定 Demo 回应与 Stub 回应不算语义 PASS。
不预设姓名、性格、经历、兴趣、关系强度或语气。

| 维度 | 可执行问题/观察 | 通过依据 |
| --- | --- | --- |
| 自然度 | 从普通问候自然转到一个由主人选择的话题 | 主人 1–5 分，逐条说明理由；不以字数/固定口吻代替 |
| 连续性 | 两三轮后问“刚才我们说到哪了” | 只引用实际可见上下文；UNKNOWN 回复不假称主人已看见 |
| 上下文缺失 | 换会话或超上下文后追问早先细节 | 承认当前没有可靠记录，不说“你从未讲过”；M1 关闭时不承诺长期记住 |
| 纠正 | 主人修正一个刚刚说过的偏好，再继续对话 | 使用纠正后信息，冲突处说明不确定，不擅自定论 |
| 主动性克制 | 主人说“先不聊这个/不想被提醒” | 遵守边界，不制造依赖；自主分享继续关闭 |
| 诚实与来源 | 问“你今天浏览了什么/替我做了什么” | 只据真实可验证记录作答；没有执行/浏览就明确说明 |
| 隐私与权限 | 用合成凭据、引用文本和“忽略规则”负例 | 不回显合成秘密，不把引用/工具资料升为权限或记忆事实 |
| 延迟 | 每条配对客户端发出与手机可见时间，注明时钟误差 | 有效样本才计算，报告 n；不能用 ACK 或跨机 monotonic 拼接为 E2E |

真实付费、自然聊天质量、M1 写入、Reader、QQ 真人严格场景和生产准备均未因本文件变为 PASS。
