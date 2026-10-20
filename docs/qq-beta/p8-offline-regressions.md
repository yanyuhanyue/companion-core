# P8-A A3/A4 离线回归清单

审计基线：`940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`。
范围为 OneBot → 持久 Spool → Core HTTP v1 → job/model budget → Outbox，以及模型关机状态预接入。
没有第二套发送系统，没有产品代码或运行配置变更。新增测试均使用合成账号、正文、凭据及临时数据库。

本 A3/A4 子任务 `paid_model_requests=0`，新增真实 QQ 发送/手机验收为 0；没有 SSH、供应商模型访问、
新 key、服务启停、部署、真实卷/备份操作。`mode=demo`、`delivery_enabled=false`、
`memory_enabled=false`、`reader_enabled=false` 及其他自主/执行能力保持关闭。
测试内用假的发送函数、MockTransport 或本机 loopback，不代表允许对真实 QQ 发信。主任务另有只读 SSH 元数据观测，见证据索引。

## 证据分级

| P8 报告等级 | 本轮数据/现有字段 | 可以证明的边界 |
| --- | --- | --- |
| `SYNTHETIC` | 测试构造的账号、event ID、正文、模型回包和故障 | 输入来源；不是平台观测 |
| `OFFLINE` | pytest；运行时日志原始 enum 为 `OFFLINE_FIXTURE` | 本地代码/HTTP/WS/SQLite 行为 |
| `ONEBOT_PROTOCOL` | 本轮没有真实平台新证据；旧系统 enum `LIVE_ONEBOT` 需结合来源解释 | 平台协议/发送 ACK，不等于手机已看见 |
| `LIVE_QQ_HUMAN` | 本轮 0；严格首五/20 场景未因离线回归提升 | 需要新批准窗口和真人逐场取证 |

现有日志 schema 没有被改写；表中为 P8 报告的明确映射。即使重放已观察 event ID，
也只能写“离线重放已观察事件”；本轮使用纯合成 ID，不声称平台真的同 ID 重投。
OneBot ACK、同进程组件计时、手机 E2E 分别记录；没有新手机配对时间，因此真实 E2E
`P50=null/P95=null`。此文不重新计数或改写 P7 五条历史样本。

## 既有测试复用与边界

下列文件已阅读并纳入本轮 bounded pytest。表中“覆盖”是离线覆盖，不能升级为 live PASS。

| 场景 | 具体既有测试/证据 | 核验结论 |
| --- | --- | --- |
| friend-owner 独占、身份/私聊边界 | `test_onebot_direct.py::test_non_owner_friend_events_never_reach_core`；`test_m0_02_reject_unauthorized_group_echo` | 拒绝非主人、group、group 字段、非 friend、自回声类型、错 bot/sender、歧义数字身份 |
| 群 @ 与非文字片段 | 上述 group 负例、`test_cq_and_array_media_are_never_interpreted_or_forwarded_as_urls`；静态 `event_normalizer.normalize/text_only` | group @ 无法绕过 group 前置拒绝；owner 私聊 @/其他非文字段按未解析附件处理，**不是所有 @ 独立拒绝**；不把附件 URL 当文字/权限 |
| 长文本/脱敏膨胀 | `test_expanding_redaction_is_rejected_before_it_can_poison_the_spool`；`test_malformed_message_is_rejected_without_partial_text` | 预处理后再检查 20,000 字符界，拒绝超界事件 |
| 多段/CQ/JSON/编码边界 | direct 中 CQ、segment、duplicate key、嵌套、surrogate、帧大小用例 | 无部分正文投递；不转发附件 URL 或 CQ 参数秘密；新增二进制/继续接收见下表 |
| 稳定 epoch、同 ID 去重/冲突、重启 | `test_owner_friend_message_has_stable_core_identity`；`test_spool_restart_ack_dedupe_and_conflict`；`test_m0_01_duplicate_event_and_content_conflict` | event identity 不依赖连接；同 ID 同内容去重，不同内容报冲突；改变持久身份/epoch 拒绝复用旧 Spool |
| Spool 锁、容量、进程死亡 | `test_spool_enforces_single_instance_and_capacity`；`test_abrupt_process_death_preserves_fsynced_inbox_and_releases_lock` | 单实例 OS 锁；queue FULL 不覆盖旧事件；fsync 后进程 abrupt exit 保留入件；新增磁盘容量覆盖见下表 |
| Core HTTP 500/异常 ACK | `test_spool_never_discards_unconfirmed_acceptance`；runtime 的 upload 路径 | 只凭严格接受回执清除正文；失败保留 pending，恢复后使用同幂等 ID |
| 精确 410 EVENT_ERASED | `test_untrusted_erasure_receipt_cannot_clear_pending_text`；`test_legacy_spool_preserves_non_exact_erasure_receipts` | 额外字段、错误码、无效/超界回包不能清除 pending；direct/legacy 各自保留严格解析 |
| 旧备份+最新 Ledger 防复活 | `test_pre_event_backup_latest_ledger_blocks_pending_spool_resurrection`；`test_complete_ledger_records_rebuild_missing_tombstones`；legacy exact-receipt 测试 | 备份甚至早于事件时仍重建 tombstone；精确 410 消费旧入件，后续新消息继续；不是实机非空/整机恢复证明 |
| WS 断开/重连 | `test_onebot_runtime.py::test_real_websocket_reconnect_and_real_core_http_keep_one_reply` | 真 loopback WS、ASGI Core；重发合成 ID 只入库一次/生成一条 reply；没有真实 QQ 连接 |
| Core worker lease/FIFO | `test_m0_06_fifo_even_with_two_workers`；`test_m0_07_worker_crash_recovered_after_lease`；`test_single_worker_lock_releases` | 同会话任务 FIFO、worker 死亡 lease 恢复、同类 CLI worker 锁；不等于 provider 全局并发门禁 |
| send timeout/无效 provider 回执 | `test_actual_core_send_state_machine_never_replays_after_io` | I/O 后未知转 `DELIVERY_UNCERTAIN`，多次 tick/lease 到期不二次发送 |
| Core begin/result ACK 丢失 | `test_lost_core_ack_never_reissues_onebot_send` | before/after begin 及 after result 三个边界，桥重启后仍不重复 I/O；未知可为 0 次或 1 次实际 stub send |
| send intent 后进程死亡 | `test_restart_after_durable_send_intent_reports_uncertain_without_send` | 持久 intent 后重启主动报告未知，没有重新发送 |
| 人工核对/迟到 ACK | `test_late_provider_receipt_cannot_override_human_reconciliation`；`test_uncertainty_does_not_block_next_reply_or_resend` | UNKNOWN 不转待发；已核对 SENT/CANCELLED 不被迟到回执覆盖；后续普通聊天可继续 |
| 发送前权限/配置复核 | `test_unsafe_delivery_cannot_start_external_send`；`test_unsafe_configuration_fails_closed`；`test_onebot_network.py` | 错 owner/action/content/account/disabled 不开始 send；公网/DNS/redirect/HTTPS-WSS 认证边界有本地测试 |
| 费用失败不可归零、匿名日志 | `test_failed_model_attempt_budget_is_not_lost`；`test_crash_with_unsettled_metric_is_reported_as_incomplete`；`test_core_emits_real_stage_spans_without_private_content` | UNKNOWN 预留进入汇总，未完成估算保持 incomplete/null；日志不含正文/合成账号，不发明首 token 或手机 E2E |
| 预算/错误重试 | `test_m0_04_retry_bounded_and_accounted`；`test_monthly_budget_reservation_is_enforced`；`test_unknown_price_blocks_autonomy_only` | 429/503/timeout 最多三次且各计 UNKNOWN；月限额先于调用；未知价格允许 chat 的现状在预算合同中明确阻断 live 启用 |
| 隐私/记忆合同 | `test_m1.py`；`test_resilience.py` | 无预设人格、证据/主体隔离、冲突不覆盖、纠正/遗忘、旧备份 replay、in-flight privacy epoch、授权检索、retention 和错误脱敏 |
| 开关默认关闭 | `test_onebot_deploy.py` | demo 无 live key，QQ delivery 关闭，自主/记忆/执行域关闭；这不代替本轮运行主机观测 |

未用大量重复参数化测试替代真实故障边界。现有队列容量、异常 HTTP、WS 重连、ACK 丢失、
erasure replay 与 M1 测试直接复用。所有测试均未改写既有合同或增加真实发送入口。

## 新增的有限合成回归

文件：[tests/test_p8_readiness.py](../../tests/test_p8_readiness.py)。共 11 个参数化实例。

| 测试函数 | 新覆盖及失败可检测点 |
| --- | --- |
| `test_stub_multiturn_context_keeps_sent_reply_and_excludes_uncertain_reply` | 三轮 owner 对话中只加入 SENT assistant 回复；未知回复不作既成对话；M1 仍零写入 |
| `test_stub_context_overflow_drops_history_but_preserves_complete_current_input` | 超 soft context 时删除旧资料，完整保留当前输入/规则；明确证明 soft 不等于硬输入 token cap |
| `test_stub_sensitive_ingress_and_egress_are_redacted_before_storage` | Stub 调用前已有 RESERVED；输入/输出 synthetic secret 在 messages、events、Outbox、status 中被遮盖 |
| `test_unknown_reservation_is_retained_and_blocks_next_model_attempt`（2） | 模型 retryable 或 worker crash 后非零估价留为 UNKNOWN；已耗尽的月额度阻止下一次模型调用，不增造零费用 usage |
| `test_model_adapter_payload_and_missing_usage_are_checked_with_stub_http`（2） | 复用 ModelClient payload；非流式/输出上限/thinking 字段；cache 不超过 prompt；usage 缺失保留 UNKNOWN 非零预留 |
| `test_model_adapter_errors_do_not_retry_or_expose_provider_body`（2） | 401 和无效 choices 不自动重试，不暴露 provider body，仍保留 UNKNOWN 费用 |
| `test_rejected_binary_and_expanding_text_do_not_block_following_frame` | 接收循环依次处理非法 UTF-8、脱敏膨胀长文本、合法 bytes；前两条拒绝，后一条持久入队，FIFO 无毒头 |
| `test_spool_disk_capacity_rejection_keeps_existing_inbox_atomic` | 实际文件大小达到磁盘上限时新入件拒绝，先前入件和去重状态保留 |

`finish_reply` 仅调用既有 Core delivery 状态机且不发网络请求；它不是另造 sender。
模型测试临时改变测试对象的 mode，但 HTTP 全部被 `MockTransport` 截获，无真实模型请求。

## 实际验证

平台：Windows PowerShell，P8 worktree 自有 `.venv`。源码、合成数据库、缓存和证据均在工作区内。
本页只声明下列 A3/A4 bounded 验证；全仓验证、发布包与 CI 的最终结果由
[P8 readiness review](p8-readiness-review.md) / [证据索引](p8-evidence-index.md) 另行记录。

| 命令/范围 | 结果 | 证据 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -m ruff check tests/test_p8_readiness.py --no-cache` | exit 0，All checks passed | 工具执行输出；父任务全仓 lint 另收敛 |
| `.venv/Scripts/python.exe -m pytest -q tests/test_p8_readiness.py --basetemp=.local/test-temp/p8-transport-audit -o cache_dir=.local/test-temp/p8-transport-audit-cache` | exit 0，11 passed in 12.68s | 本地 `.local/p8/evidence/a3-a4-new-regressions.log` |
| OneBot 全部现有测试 + `test_m0.py test_m1.py test_resilience.py test_p8_readiness.py`，独立临时目录 | exit 0，146 passed，1 dependency deprecation warning，140.91s | 本地 `.local/p8/evidence/a3-a4-bounded-regressions.log` |

bounded 命令（直接复用仓库测试，不调用模型/QQ）：

```powershell
$targetFiles = @(rg --files tests -g 'test_onebot*.py')
& .venv/Scripts/python.exe -m pytest -q @targetFiles `
  tests/test_m0.py tests/test_m1.py tests/test_resilience.py tests/test_p8_readiness.py `
  --basetemp=.local/test-temp/p8-transport-audit-bounded `
  -o cache_dir=.local/test-temp/p8-transport-audit-bounded-cache
```

独立新测试与 bounded suite 使用不同临时目录，源码与日志已保留。本轮曾尝试仅清理上述四个
自有合成测试目录；命令先核对绝对路径在 `.local/test-temp` 内，但自动审批仍返回
`blocked by policy`，未提供更细原因。清理未执行，目录保留为诊断材料；未换工具或重试绕过。
没有清理业务 Spool/DB、旧备份或独立 Ledger，没有运行 broad git clean。

## 风险与未证明事项

没有发现需要本轮产品修补的 A3 安全回归。测试发现并明确了 A4 上线前必须补齐的合同缺口：
chat 的日费用、单次费用/输入硬上限、批准窗口累计总费用和统一模型并发门禁不存在。
详见 [模型预算合同](p8-model-budget.md)，其状态是 `BLOCKS_PAID_ACTIVATION`，不是已实现。
未知费率 chat 与 demo 零费率同样不能当真实调用费用保护。

本地故障注入不证明平台真实同 ID 重投、手机自然聊天、真实服务恢复、非空实机/整机恢复、
供应商计费/区域/模型能力或线上隐私行为。上述缺口继续保持原门禁，不用 offline PASS 填 live PASS。
发现 `DELIVERY_UNCERTAIN` 时保留其状态和证据，等待主人核对，不自行重发；本轮没有该真实核对操作。
