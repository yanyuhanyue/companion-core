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

## 恢复与验证

STOP 取消尚可撤销的原生任务和待发项，保留 `SENDING`、`DELIVERY_UNCERTAIN` 及模型
`UNKNOWN` 责任。模型钩子期间的拒绝沿用原账务结算合同，不凭新授权清零费用责任。
旧固定 timer 问候仅在 `QUEUED`/`RETRY_WAIT` 且没有任何 model usage 或 Outbox 时退休；
原生审计记录 `LEGACY_TIMER_RETIRED`。有执行或交付责任的旧工作保留对账依据。

`tests/test_owner_runtime_process.py` 通过真实 TCP API 与独立 Core worker 验证正常文字链和
HTTP 钩子等待期间的 STOP；所有外部 provider 使用强制 synthetic `MockTransport`。
`tests/test_owner_wire.py` 验证共享编解码与无 guard 宿主拒绝，
`tests/test_owner_input.py` 和 `tests/test_owner_runtime.py` 验证来源、用途、租约与退休边界。
