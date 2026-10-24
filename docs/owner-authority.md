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

1. `POST /owner-authority/inputs/approve` 接收 `OwnerCapture`。
   字段为 `authority`、`kind=authenticated_onebot_ws`、`event_id`、`source_event_id`、
   `input_sha256`、`frame_sha256`、`evidence_sha256`、`identity_sha256`、`protocol_epoch`、
   `occurred_at`、`received_at` 和 Ed25519 `signature`。
   签名使用共享 `capture_signing_payload` 生成的规范字节，域为
   `companion-core/owner-capture/v2` 加 NUL。时间戳须带时区。
2. `POST /inbox/events` 沿用原事件协议与幂等键。实际事件必须匹配已批准签名的
   主人私聊来源、时间和完整输入摘要；原事务保存事件及 job 授权引用。
3. `POST /deliveries/lease` 的文字私聊请求包含 `worker_id`、`limit` 和 `authority`。
   返回项携带相同 `authority`、`origin_event_id`、`content_sha256`、原生
   `lease_token`/`lease_until` 与主人回复 `reply_revision`。
4. 原 `begin-send` 取得 `SENDING` 后，`POST /owner-authority/send-check` 检查
   `authority`、`event_id`、`message_id`、`content_sha256`、`lease_token`、
   `reply_revision`，以及存在语音时的原 `voice` binding。
   返回有限 `lease_until`。共享发送器拒绝没有匹配授权 guard 的新投递项。

输入和最终发送校验响应均为 `Cache-Control: no-store`。签名有效、Bridge 登录成功或
持有一个旧引用，均不替代最终授权、来源和有限租约校验。

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
