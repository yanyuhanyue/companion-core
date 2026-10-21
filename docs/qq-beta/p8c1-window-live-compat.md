# P8-C.1：默认关闭的有界文本聊天合同

本页保存 P8-C.1 的实现与验证记录；当前预算使用 [用量估算与校准](p8b-budget.md)，显式批准的多轮能力见 [窗口内历史](p9-approved-window-continuity.md)。

本实现的验收对象是离线兼容性。真实供应商合同、平台事件来源、恢复、部署、QQ 窗口和费用
仍需各自批准；`production_ready=false`，真实严格场景保持 `0/5`、`0/20`，手机 E2E 和
真实 token/cache/cost/latency 均为 `null`。合成用例不属于 QQ 或厂商实测。

## 两个入口

原 WindowPlan 的 Demo 格式和 `mode=demo`、全部功能关闭检查保持原样。仅把 Core 改为
live 仍得到 `DEMO_CLOSED_FEATURES_REQUIRED`。

新计划必须显式增加 `mode="live_text"` 和 `live_text` 批准对象。Core 的
`Settings.live_text_window` 默认 `None`，其批准模型的 `live_approved` 默认 `false`。
两端都显式批准、计划及运行状态完全相符，才可进入新路径。

一期只接受固定 owner/bot 的 friend/private 普通文字。所有以 ASCII `/` 开头的保留命令
均在创建模型工作前拒绝，包括未知命令；不复制 Core 的命令表。裸文字走普通聊天，不执行
旧控制命令。M1、Reader/观察适配器、Diary、Affect、Consolidation、Reflection、Goals、
Wake、探索、社交、主动消息、表达图片、媒体、DSH 和设备执行全部关闭。
`max_recent_messages=0`：本批准仅覆盖静态规则和当前准确输入，不将旧聊天上下文送给模型。

## 固定批准

原计划继续绑定身份摘要、protocol epoch、Bridge 全配置摘要、配置版本、三项
Docker `image_ids`（core/bridge/protocol 三角色的真实 `sha256:64` ID）、运行清单、独立授权摘要、独立 Window Journal 路径、绝对上海时间及
最多五输入/五次发送/一并发。常驻 Bridge 的 `delivery_enabled` 必须保持 `false`。

新增 `live_text` 对象与 Core 批准的字段相同，唯独 Core 另保存整个计划的 `plan_sha256`，
避免摘要循环。字段为：

| 字段 | 约束与用途 |
| --- | --- |
| `live_approved` | 严格布尔值，默认 false |
| `execution` | 与计划一致；默认 local_synthetic，只能使用明确的 MockTransport 模型传输，真实 HTTP 传输在调用前阻断 |
| `window_id`、`starts_at`、`ends_at` | 与原计划完全相符；不能超过 PaidModelApproval 时间范围 |
| `core_config_sha256` | Core 配置的 HMAC 摘要，排除该批准对象；绑定实际模型凭据和 Bridge 凭据，公开状态不返回原值 |
| `core_code_sha256`、`bridge_code_sha256` | 每次准入/发送实际读取相应 Python 源码计算的摘要 |
| `candidate_sha` | 固定候选 Git SHA，必须由独立私密部署证明关联到镜像和源文件摘要 |
| `paid_contract_sha256` | 既有 paid_budget snapshot，包含实际凭据绑定、精确 framing/tokenizer/费率和费用上限 |
| `budget_journal_id` | 已由 Core 初始化并核实的独立 Budget Journal 身份；Bridge 不读取该文件 |
| `custodian_sha256`、`approval_sha256` | 费用保管责任人和独立私密批准的证据引用摘要 |
| `capture_public_key` | approved_live 固定独立接收器 Ed25519 公钥；私钥只在 Bridge 受限配置 |
| `max_inputs`、`max_model_attempts` | 各自 1–5；输入数与原计划一致；PaidModelApproval 的 window_calls 不得更大 |
| `max_input_chars`、`max_reply_chars` | 各自不超过 500，与原计划一致 |

PaidModelApproval 不变：实际 HTTPS provider/model、USD、地区、有效费率、准确 tokenizer
文件与供应商 framing 证明、单次/逻辑请求/日/月/窗口费用及次数、单账户隔离仍需有效。
本阶段进一步要求 `per_request_calls=1`、`concurrency=1`。无法证明真实厂商 framing 的
账户继续 BLOCKED；合成 ByteLevel/chatml-v1 合同只证明测试路径，不能用于实际付费。

宿主操作者须核对 container.Image 等于批准的 docker image inspect Id；Bridge 只核对固定清单，不读取 Docker。
本地 Docker image ID 是配置摘要，不能冒充 registry manifest digest；registry pull ref 另存部署 metadata，不要求公开发布镜像。
源码摘要是运行文件的测量，Git SHA/实际镜像 ID 是独立部署证明，二者不能混称。wheel 和
源码目录的打包内容可能不同，应在最终固定镜像内计算摘要；不能直接照抄开发工作树摘要。
填写一个 digest 或 `live_approved=true` 不是自动取得人的授权。

## 单一工作与投递链

Core 新增四个受原 HTTP v1 Bearer 认证保护的严格 JSON 端点：

| 端点 | 资料 |
| --- | --- |
| `POST /internal/v1/live-text/open` | `window_sha256`；登记不可静默替换的批准、隐私 epoch 和窗口身份 |
| `POST /internal/v1/live-text/inputs/approve` | `window_sha256,event_id,input_sha256,evidence_sha256,capture`；approved_live 要求独立接收器签名 capture，保存最多五条准确输入批准凭据 |
| `POST /internal/v1/live-text/send-check` | `window_sha256,event_id,message_id,reply_sha256,lease_token`；核对原 SENDING 租约和已结算回复凭据 |
| `POST /internal/v1/live-text/stop` | `window_sha256`；持久撤销该 Core 窗口，过期/撤销配置后仍能停止 |

`ops/status.live_text_window` 返回批准状态、阻断码、计划/批准摘要、隐私 epoch 和持久模型
尝试计数，不返回凭据、DB/账本路径或消息正文。Bridge 仅调用 HTTP，不导入 Core 包，
不持有模型 Key、Core DB 或 ErasureLedger 权限。

Core 在既有 runtime_meta 中保存少量批准、输入及回复哈希凭据。回复凭据与现有 Outbox
在同一事务提交，绑定准确输入、job、model request、正文摘要、隐私 epoch 和计划。
即使有人重新批准被替换的 Outbox 正文，也不能绕过原回复凭据。这里没有新的工作队列、
投递状态机或财务账本；原 events/jobs/Outbox、Spool 和三个独立 journals 继续负责各自状态。

预算 reserve/complete 仍是唯一模型准入与结算链。每个输入只允许一次 chat 模型尝试；
模型失败不转为可重试任务，也不产生一条未经单独确认的错误回复。最终 HTTP 请求钩子重新
检查批准、租约、隐私、费用身份、实际 URL、Authorization 和完整 payload。费用先按原
保管责任结算，再决定是否允许迟到结果入 Outbox；撤销不丢弃已知费用。
所有 journal、源码和 SQLite 校验完成后，还会执行不含文件/DB I/O、锁或 await 的最终
dispatch fence，重新采样 live/paid/费率截止及 job/admission 租约。账本校验跨过截止时
不会发出模型请求，保守预留仍记 UNKNOWN；不能只在模型晚回包时拒绝 Outbox。

发送继续调用原 `deliver_tick`：先在独立 Window Journal 预留一次额度，再走原 lease /
begin-send、Spool 的不确定结果回执及一次 HTTP。网络/DNS 钩子之后核对原 authority、
Host、SNI 和已验证的实际私网 IP，再请求 Core 的准确消息检查，最后同步重查本地截止和
fence。禁止仅比较“原域名 URL 等于被固定后的 IP URL”，也禁止取消目标检查。
local_synthetic 对 Core、三项 OneBot 探测及发送的实际固定 IP 都要求 loopback；仅配置
localhost 域名而解析到非 loopback 私网地址也会在任何该目标的 transport dispatch 前拒绝。

Core 的检查事务是跨服务发送批准的线性化点。随后发生的撤销无法撤回已经交给外部平台的
I/O，必须保留不确定结果，不能声称跨网络具有瞬时撤回能力。

## 停止、崩溃及恢复

- 模型尝试和 QQ 外部尝试是两个独立计数。五个输入不授权任何额外模型重试或第六次发送。
- 任意模型 UNKNOWN、其他 RESERVED、DELIVERY_UNCERTAIN、来源/隐私/配置/源码漂移或
  独立账本不一致都会阻断新增准入。已花额度、金额上界及独立 journal 不退款、不重置。
- SQLite 锁等待、输入证明/落盘和异步网络钩子之后重读截止。HTTP ACK 只代表协议接受，
  不代表手机已收到；所有手机回执与 E2E 字段保持 null。
- `window-stop` 对 live 窗口先持久停止本地执行器，再请求 Core 停止确认。Core 不可达时
  命令返回失败和 `CORE_STOP_UNCONFIRMED / LIVE_TEXT_CORE_STOP_UNCONFIRMED`；持久本地
  status 同时返回 `local_state=STOPPED,core_stop_confirmed=false`，不能声称全局付费已停止。
  成功确认会 fsync `CORE_STOP_CONFIRMED` 到原 Window Journal，重启后仍可区分。
  程序调用请用 `await window.stop_live(core_client)`；同步 stop 只表示本地停止，未确认时
  返回的全局 state 仍为 CORE_STOP_UNCONFIRMED。自动错误停止同样只会尽力通知 Core。应使用
  独立操作通道撤销 Core 批准并保留已开始请求的费用状态，禁止自动重试发信。
- Core 确认停止之前，会先在原 Budget Journal 中 fsync 一条 `window_stop` 控制记录，再
  提交 DB 停止标记。它不含费用数量，也没有新建财务状态机；读取该链会永久阻断新准入，
  因而恢复丢失 DB 标记的旧库也不能重新取得调用许可。既有在途费用结算继续使用原保管
  责任和 allow_settlement 路径。带该记录的账本不可交给旧版本降级或删掉链尾继续使用；
  新窗口需要另外审查账户恢复/轮换，不能自行忽略控制记录。
- 本地 status 不访问 Core，因此重启后模型计数可能为 null；不能将它解释成零。模型计数
  以 Core 持久账本为准。撤销配置、改 ID/epoch 或删除批准不能绕过已经登记的 Core 绑定。
- 沿用恢复门禁：最新 ErasureLedger、全部相关 Budget Journals、Window Journal、Core
  DB/WAL、Spool/epoch 和外部登录/原服务必须一致。恢复旧 DB 不会返还额度；若独立账本
  也全部被一起回滚，本地代码不能凭空识别外部最新事实，真实恢复仍需要独立核对。

## 认证 OneBot 接收与真实准入

[P8-C.2 接收合同](p10-authenticated-capture.md) 规定独占认证 WS 捕获、原 Spool/Window
Journal 中的 CAPTURED→APPROVED 阶段，以及固定公钥验证的独立接收器签名。
approved_live 不接受 staged JSON 或只有普通 HTTP bridge token 的手写来源证明。
真实恢复、部署、QQ 和付费仍需各项准入；本地合成成功不属于实机验收。

## 验证位置

`tests/test_live_text_window.py` 使用合成身份与模型/平台传输，覆盖闭合默认值、原 Demo
拒绝 live、新模式五次重启投递、Core 独立批准、替换正文、保留命令、非主人/非文字、
迟到费用/隐私/撤销、UNKNOWN、实际 DNS pin 钩子、异步请求漂移、锁等待和协议 ACK 丢失。
原预算、Demo 窗口、M0 与 Reader 隔离回归继续保留。具体候选 SHA、实际检查计数及各项
CI 结果在 PR 与私密执行清单关联；此文档不以自身内容自证 CI 或真实运行态。
