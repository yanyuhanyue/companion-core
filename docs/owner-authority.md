# OwnerAuthority 开发接口

OwnerAuthority 保存主人的持久用途授权。付费策略继续使用原 Budget Journal；
工作执行继续使用原 job、model admission 和 Outbox 的有限租约。
授权修订不会续租，也不会重绑旧任务。`owner_runtime.mode` 默认 `OFF`；
`OFF` 与 `SHADOW` 均不能取得执行授权。

本地开发候选已接通签名文字输入、原生聊天任务、模型最终校验、私聊 Outbox 及评测用途。
完整 Bridge、媒体消费者和历史投递责任的迁移尚未结束，当前候选不可部署。

## 授权引用

共享无 Core 依赖的 `companion_delivery_protocol.authority.AuthorityRef` 具有五个字段：
`version=2`、`grant_id`、`revision`、`policy_sha256`、`restore_generation`。
每个任务、模型准入和投递项都保存完整引用。当前用途、原账本和恢复代际必须同时匹配。
历史签名可以证明原始来源，但不能恢复被 STOP、修订或备份恢复撤销的执行权限。

`owner_authority.current(..., purpose=..., authority=...)` 返回
`OwnerAuthorityView`，其中 `policy` 为显式 `OwnerGrantPolicy`，`authority` 为引用。
`bind_job` 只在原生入队事务内首次绑定 `QUEUED` job；
`check_job(..., executing=True)` 还会重读原生运行状态、token 和到期时间。
模型独立检查其用途，并在所有 HTTP 钩子与预算同步之后再次校验。

聊天、历史、记忆、Affect、日记及其他能力分别检查用途。聊天授权不隐含历史或记忆读取。
历史读取还需要明确的 `not_before`、轮数、UTF-8 字节上限和来源保留期，
并验证原始签名、当前擦除状态与原始任务引用。

## HTTP v1 文字私聊边界

所有路径均位于 `/internal/v1`。授权 preview、activate/revisions、stop、status
使用 owner control 凭据。Bridge 凭据不能创建或扩大授权。

Bridge 可通过 `POST /owner-authority/check` 提交精确的 `authority` 和 `purpose`，
查询该用途当前是否允许执行。成功响应包含 `approved=true`、原引用、原用途、可空的
`expires_at`、原 `budget_journal_id`、捕获身份 `capture` 和当前已验证策略中的
`capability_policies` 哈希字典，并设置 `Cache-Control: no-store`。
Bridge 只接受已知能力名与小写 SHA-256；请求具体能力时必须存在对应哈希。
这些元数据复用已认证的 introspection 边界（参见
[RFC 7662 §2.2](https://www.rfc-editor.org/rfc/rfc7662.html#section-2.2)），
用于核对冻结输入和构造已有 lane 请求；每条 lane 仍需独立用途与最终发送检查。
它不创建执行租约，也不消耗预算；
STOP、恢复代际变化、修订不符或用途不符均拒绝请求。发送仍必须通过下面的有限租约检查。

`POST /owner-authority/inputs/state` 返回原事件的 `reply_revision`；事件尚未入库时为 0。
这是只读状态，Meme 等执行边界仍须检查当前原始来源和用途。该字段不进入原
`work_sha256` 的事件投影。历史交接日志的终态回执可以没有此字段；包含时必须是
非负整数，新执行消费者必须显式取得并验证它。账号动作的成功 ACK 使用原
`account_action_receipt` 摘要完成停止交接，原尝试和回执保持不变。

同时允许 memory 与 history 时，情景接续可以选择超过七天但仍在来源保留期内的
未完成 Hot 事项。Core 先为接续来源保留历史条数和 UTF-8 字节额度，再放入较新的
普通对话；满额近期历史不会自动挤掉 Hot 事项。原签名、当前历史许可和完整情景来源
仍逐一复核，最终模型请求继续受既有 token 与付费上限约束。

1. `POST /owner-authority/inputs/approve` 接收 `OwnerCapture`。
   字段为 `authority`、`kind=authenticated_onebot_ws`、`event_id`、`source_event_id`、
   `input_sha256`、`frame_sha256`、`evidence_sha256`、`identity_sha256`、`protocol_epoch`、
   `occurred_at`、`received_at` 和 Ed25519 `signature`。
   签名使用共享 `capture_signing_payload` 生成的规范字节，域为
   `companion-core/owner-capture/v2` 加 NUL。时间戳须带时区。
   静态 Meme 目录可在同一请求附加可选 `meme_offer`，沿用严格 `MemeOffer`。
   旁车独立于原始签名保存，`offer_key` 必须绑定本事件；重试同时比较原签名和
   已冻结候选，包含有／无候选的变化。审批与后续选择要求当前 `meme` 和
   `expression` 用途；选择还复核原始来源、job 和回复版本。
   Meme capability 摘要包含 expression 开关、候选概率、冷却时间和类别，允许
   discovery 与 semantics 均关闭的静态目录。语义候选继续走原查询绑定接口。
2. `POST /inbox/events` 沿用原事件协议与幂等键。实际事件必须匹配已批准签名的
   主人私聊来源、时间和完整输入摘要；原事务保存事件及 job 授权引用。
3. `POST /deliveries/lease` 的文字私聊请求包含 `worker_id`、`limit` 和 `authority`。
   返回项携带相同 `authority`、`origin_event_id`、`content_sha256`、原生
   `lease_token`/`lease_until` 与主人回复 `reply_revision`。
4. 原 `begin-send` 取得 `SENDING` 后，`POST /owner-authority/send-check` 检查
   `authority`、`event_id`、`message_id`、`content_sha256`、`lease_token`、
   `reply_revision`，以及存在语音时的原 `voice` binding。
   返回有限 `lease_until`。共享发送器拒绝没有匹配授权 guard 的新投递项。
   内容包含图片时，在同一事务内额外复核当前 `meme` 用途及 capability 摘要。

输入和最终发送校验响应均为 `Cache-Control: no-store`。签名有效、Bridge 登录成功或
持有一个旧引用，均不替代最终授权、来源和有限租约校验。

原生 Outbox 凭证沿用既有隐私版本机制，固定生成时的 `privacy_epoch`。
领取租约与最终发送校验均比较当前版本；隐私版本变化后阻止旧回复发送，保留原模型费用。
尚未开始发送的旧回复取消；已进入发送阶段则沿用既有取消证明与交付不确定性规则。

当前执行授权还复用既有 `_ledger_custody` 比对外部擦除账本与数据库已应用记录。
外部擦除已落盘而数据库应用中断时，新执行等待原擦除重放；读取旧授权凭证、结算与 STOP
保留原路径。授权读取本身不重放或修复数据，所有磁盘读取完成后再核对授权期限。

原生回复还固定 SDK 上下文快照摘要。领取与最终发送时重用 `agent_images.inputs`，
核对原图签名、字节摘要、来源、用途、租约绑定与原有效期；此处读取已完成任务的原证据，
不会重开执行租约、下载或模型调用。复用 DSH
[固定版本的附件保存与本地消费职责划分](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/attachment.ts)，
以及 [Pydantic AI 的会话消息保存](https://ai.pydantic.dev/message-history/)；
授权、有效期和来源校验继续由 Core 的既有证据机制负责。

记忆工具使用的原事实与完整来源快照，在领取和最终发送时复用 `MemoryContext.revalidate`
检查内容、有效期与擦除状态；快照本身的增删受同一冻结摘要约束。该校验只读取原记录，
不进行向量检索或额外付费复核，取消旧回复时仍保留原模型费用。

原生分段回复沿用 `continuous.segment_pacing_pending` 的本地 ACK 计时原则：
RESERVED 保存既有 `reply_segments.journal_value`，下一次领取租约前读取原 ACK
journal 时间并等待该段间隔，重启后仍使用同一证据。缺少分段元数据的历史 ACK
保守等待现有协议允许的最大 3000 ms；新非分段记录显式保存空值，不增加等待。
新主人输入或召回不受旧轮次间隔阻挡，实际发送仍复核 Core 当前来源和租约。
最终本地校验还在 journal 读取返回后重读 ACTIVE/STOP 标志，避免 STOP 后追加
IO_STARTED。实现复用了已检查的 DSH QQ
[串行出站发送](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/outbound-buffer.ts)
的发送完成顺序，间隔沿用本项目原有 journal 与 envelope，不增加外部协议。

`POST /owner-authority/inputs/recall` 接收原签名 transport identity 绑定的
`OwnerRecall`，使用 `recall_signing_payload` 和独立 `owner-recall/v2` 签名域。
它沿用原 ErasureLedger 的 PENDING_TARGET / RECALLED、幂等及恢复重放。
已 STOP 或恢复后的原授权仅用于验证撤回证据；原始捕获与出站 provider ID
冲突时不推断消息方向。先收到撤回的输入在实际 inbox 准入前完成擦除并返回 410。

后台消费者使用 `owner_input.source(..., purpose=...)` 验证本次执行用途；
`history=True` 另外检查显式历史范围。原始签名与任务引用始终按原版本验证。

## HTTP v1 账户操作边界

账户投递沿用 `/deliveries/lease`，请求明确指定 `delivery_lane=qq_account`、
`authority` 与 `account_actions_authorization_sha256`，返回项包含原授权引用。
租用和 `begin-send` 校验账户 Outbox 的原始授权及 Social 回执。
`POST /account-actions/send-check`、`profile-baselines`、`cancel-before-send`
使用各自原有字段与 `authority`。`GET /account-actions/profile-baselines/state`
接收 JSON 编码的 `authority` 查询参数及 `authorization_sha256`。
账户通道要求当前 `social` 和 `account_actions` 用途及冻结策略摘要匹配。

`tests/test_account_owner_transport.py` 使用真实 TCP 与独立 Bridge 测试进程覆盖
租用、开始发送、最终账户校验以及校验前 STOP；模型传输仅使用 synthetic MockTransport，
测试进程不调用 QQ 或 QZone provider。

## 历史 v1 证据读取

`legacy_owner_input` 复用原 v1 Ed25519 捕获签名、归档审批 binding、原独立预算账本、
原模型结算及投递回执。只接受实际 opened 的原审批，历史窗口和租约可以已经停止或过期。
读取仍要求当前明确的 `history` 或 `memory` 主人私有来源范围，并遵守当前保留期限、
撤回和擦除状态。历史任务、模型 admission 和 Outbox 的空授权字段保持原值。
新输入的接收和执行使用 v2 授权，历史读取不提供新的执行许可。

历史对话只纳入经过原模型结算、原回执和原生 SENT 状态验证的连续发送前缀；
未发送、取消或不确定的尾段不会成为 assistant 文本。原签名、归档、账本、内容摘要、
结算或发送证明缺失/不一致时拒绝该历史项，原财务责任和审计记录保留。
`tests/test_legacy_owner_input.py` 覆盖原严格窗口、主人私有窗口、部分发送、
当前原生历史选择器、原始授权不回填及证据缺失/篡改拒绝。

## 恢复与验证

STOP 取消尚可撤销的原生任务和待发项，保留 `SENDING`、`DELIVERY_UNCERTAIN` 及模型
`UNKNOWN` 责任。模型钩子期间的拒绝沿用原账务结算合同，不凭新授权清零费用责任。
旧固定 timer 问候仅在 `QUEUED`/`RETRY_WAIT` 且没有任何 model usage 或 Outbox 时退休；
原生审计记录 `LEGACY_TIMER_RETIRED`。有执行或交付责任的旧工作保留对账依据。

`tests/test_owner_runtime_process.py` 通过真实 TCP API 与独立 Core worker 验证正常文字链和
HTTP 钩子等待期间的 STOP；所有外部 provider 使用强制 synthetic `MockTransport`。
`tests/test_owner_wire.py` 验证共享编解码与无 guard 宿主拒绝，
`tests/test_owner_input.py` 和 `tests/test_owner_runtime.py` 验证来源、用途、租约与退休边界。


### 原生撤回的读取与发送边界

2026-10-25 检查 DSH QQ 插件固定版本
[c8b9158 出站缓冲](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/outbound-buffer.ts)
的串行发送与取消，以及同版本
[入站处理](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/inbound.ts)
的传输层引用装配。复用其职责划分；撤回真实性与持久取消仍由本项目原
`owner_notices` 签名队列、Core recall、`cancel-before-send` 回执负责。

[HTTPX request hook](https://www.python-httpx.org/advanced/event-hooks/) 在请求准备后、
网络发送前运行，且允许修改请求。本项目沿用 `checked_http_client` 的最终传输检查：
引用读取前和每次异步授权后重新检查原消息的已签名撤回；撤回后的读取结果不进入捕获。
原生发送门禁复用旧 `WindowOneBot.before_dispatch` 的撤回同步步骤；最终检查发现本地撤回时，
先将原通知送交 Core，再请求既有未委托取消回执。没有 Core 证明不能记录 NOT_SENT。
已委托后的超时继续保留 UNKNOWN、原始费用与停止状态。

`tests/test_owner_recall_bridge.py` 使用真实认证 WebSocket、Core HTTP 与独立 Core API/worker
实例，覆盖通知先到、引用请求前及响应后到达、发送请求前和 Core send-check 响应后到达，
以及外部委托后超时；仅外部供应商 HTTP 使用合成响应。


### 停止后的原输入材料校验

2026-10-25 检查 DSH jobs-local
[原始 producer 与终态投影](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/jobs/jobs-local/src/index.ts)：
终态由所属 producer 的已提交记录产生，读者获得保留投影。本项目沿用原责任与终态分离的原则，
在 `owner_input_state` 只读回执中复用已有签名 capture、model admission、冻结 delivery 材料。
[SQLite 外键](https://www.sqlite.org/foreignkeys.html)保证声明方向的存在关系；
`model_usage` 仍须显式检查对应 admission，不能仅凭 SETTLED 字符串关闭责任。

输入正文、来源身份和发生时间必须匹配原签名；模型保留原 admission 的 authority/job 关联，
投递终态保留原 `owner_delivery` 身份与内容摘要。真实撤回或擦除沿既有 tombstone 和清空内容分支读回，
不要求旧输入 epoch 等于当前 epoch，也不获取当前执行许可。回执仍分别报告 work/model/delivery
未决状态；CHAT 停止不等于全部责任关闭。校验只读，不重开任务、不重发、不退还或改写历史费用。
