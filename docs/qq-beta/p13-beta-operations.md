# P13-E：有界私人 Beta、反馈关联与恢复准备

本包复用 [P12 运营工具](p12-beta-operations.md)、原 `status/observe`、Inspector 与反馈汇总。
P13 的服务器回读、部署、QQ、账户调用、恢复、真实日记与自然日观察均未执行。
`private_beta_ready=false`，`production_ready=false`；原 P11 停止记录只证明当时状态。

## E1：先通过不可逆安全检查，再进入获批的真实体验

普通聊天继续使用原 OneBot Bridge → Core HTTP v1 → Worker/Model → Outbox。
每个新窗口仍最多五次模型尝试，memory_review、content_vision 与 chat 合并计数；
UNKNOWN、DELIVERY_UNCERTAIN、未确认 STOP 不退款、不自动重试或续签。
启动材料沿用 [默认关闭配置](p12-private-beta.example.toml)，显式关闭 proactive 和所有
未批准的 Memory/Diary/Affect/Reader/探索/社交/设备功能；模板不等于运行配置。

获准前需核对准确候选、部署镜像/配置/身份、恢复对象、最新 Ledger、Budget、完整
Window/Spool/epoch、唯一活跃 Bridge 和当前 STOP。仅离线通过不提供真实账户能力或
手机收到的证明。A 一图、B 每条记忆控制、后续只读召回、C/D 日记与 Reader/发送各自
有独立范围；图片不混用历史或事实范围，记忆管理入口不调用模型也不发送 QQ 回复。

一旦身份、收件人、授权、来源、删除闭包、责任日志或停止状态不一致，应停止新增动作，
保留原责任与受鉴权的只读诊断。具体实际操作需填写新批准，不能把历史权限重新使用。

## E2：累计限额独立实现与审查

账户/日/月/窗口累计额度由独立 E2 分支扩展原 Budget Journal、完整 Window Journal
和 Spool。实现、故障回归、准确候选 CI 与后续运行批准分别记录在 P13 最终交付和门禁。
本分支不声称 E2 已部署或已获准；与累计合同组合时仍保留现有五次窗口限制。
账户其他程序的费用不受本地计数保护，配置价预留不是供应商硬扣费上限。
PAUSE、STOP、到期、时钟倒退、账户归属不明或日志不连续不能用新窗口清零；
续签必须是新批准追加，旧 UNKNOWN 和已发生发送责任持续存在。

## E3：连续观察与真人评价

| 层次 | 原工具记录 | 不可代替的证据 |
| --- | --- | --- |
| API | 独立实例、可读状态、绝对时间与采集错误 | Worker 推进、任务完成、QQ 协议或手机可见 |
| Worker | 原心跳 sequence、循环推进与重启/间隔 | 模型成功、消息已发或手机收到 |
| 业务 | 原 job、模型尝试、实际 usage/预留、Outbox 状态 | QQ 手机签收、第三方账单、来源真实性 |
| 端到端 | 主人同设备 UTC 或校准时间对及独立依据 | 未配对的服务阶段时延不能补为手机耗时 |
| 健康与资源 | monotonic 时长、采样间隔、缺口、磁盘/账本大小、UNKNOWN | 缺口期间无观测，不计作连续健康 |

`observe` 已将 SAMPLE_GAP、COLLECTION_GAP、时钟跳变、API 重启、采集失败及 blockers
切断观察段。`clear_segment_observed_seconds` 只是最后一个无已知缺口段的观测跨度，
`worker_health_proven=false`；不能把它改名为端到端连续健康时长。
告警通道尚未获准或未发送时记录 NOT_RUN，发送失败记失败；告警缺失不能记为零故障。

[P13 反馈模板](p13-beta-feedback.template.json) 复用 P11 schema 与原验证器，新增可选
隐私舒适度、重复/打扰、日记真实性、兴趣自发性评分。所有评分 1–5，5 表示满意/
真实/无打扰，未观察填 null；原自然度、相关性、来源准确性、记忆正确性和主动性评分保留。
费用、Token、覆盖、手机时间对与事故各用原专属字段，不折算成一个总分。
`reason_private` 写实际原因，不进入公开汇总；真人未评价不能从模型自述代填。

P13 收紧 `authorized_runtime` 的记录准入：必须有非空 trace、准确候选、Inspector
文件摘要和 R3 引用。汇总还要求 Inspector 配置候选及 R3 与反馈一致，并从该事件的
原输入记录追溯原 window 历史批准及其摘要；当前配置不能代替旧事件批准。实际付费 I/O
需要 R4 引用，冻结记忆/图像或持久 memory_review/content_vision 尝试需要 R5/media 引用，
即使最终上下文缺失或引用已隐藏，也不能丢失相应批准关联。正文/图片已获取的 coverage 需要 media，
日记审阅和已收到主动分享分别需要 diary/autonomous_send 引用。只有人工留言、缺少
关联 Inspector 的记录可保留作待补材料，不能进入真实运行统计。
既有历史文件不改写；旧 `authorized_runtime` 如缺上述关联，重新汇总时需补齐原真实
材料，不能填写虚构摘要。`local_synthetic` 仍单列，允许无实际运行批准。

这些只是结构和文件关联检查，不验证授权原件、账户或本人签名。汇总始终保留
`evidence_authenticated=false`、`runtime_authorized=false`、`supplier_invoice_verified=false`。
按 [原清单模板](p12-beta-observations.template.json) 明确列本地反馈和 Inspector 文件：

```powershell
.venv/Scripts/python.exe scripts/validate_beta_feedback.py .local/p13/first.feedback.json
.venv/Scripts/python.exe -m scripts.summarize_beta_feedback .local/p13/observations.json
```

以上是获准材料存在后的使用示例，本轮未创建或读取真实 `.local/p13/` 内容。
输入/发送计数需要原协议与账本；账面 SENT 不补手机确认；实际 usage × 配置价与独立
账单分别记录，未对账保持未知。至少两对才出 p50/p95，同时显示 n；小样本不证明 SLO。
真实第 1/3/7/14/30 天的阈值须事前约定，日期取事件发生时间；模拟跨日不增加真人天数。

## E4：按对象补足恢复范围

沿用 [P12 恢复申请](p12-recovery-request.template.json) 的九类对象槽位，逐项填写读取、
备份、复制、新隔离目标、验证、费用、时限、停写范围与独立钥匙保管人。
P10 已验证的八个应用对象只覆盖当时列出的应用集；QQ 登录/配置/应用数据、宿主原服务、
整机快照仍无 P13 新批准或恢复结果，不能概括为整机容灾。

新恢复目标在启动前技术性禁止 QQ、模型、来源服务出站，确认隔离后才打开恢复数据。
核对当前 schema、所有者/机器人身份、protocol/privacy epoch、最新 ErasureLedger、
原 Budget 全链和 Window/Spool 全历史，再做 integrity/FK、对象计数、对照数据仍在、
已忘内容不复活、UNKNOWN 不退款和消息不重发检查。完成也不能自动上线或 QQ 登录。
不得覆盖原在线卷、原服务、原数据库或用旧快照恢复额度；迁移后的候选回退需验证兼容，
不能丢掉新责任字段后运行旧二进制。每个对象单独保存 STOP 与验证回执，RPO/RTO 未测为 null。

## 当前证据

新增 F3/E3 原反例已固定：旧 Inspector 不展示选择差异，旧反馈允许缺失关联或不同 R3。
最终包含原生记忆召回链、原事件窗口绑定与隐藏上下文能力引用的五文件定向检查为
Ruff + 99 passed（47.21 秒）。原先 12 项选择/反馈反例与后续 2 项窗口绑定反例均转绿。
组合全量与准确 PR HEAD CI 在最终交付记录；合成样本只证明本地逻辑。
本轮真实输入、模型调用、手机确认、真实日记和恢复样本为 0；自然日、账单和手机延迟为 null。
[Context Inspector 的记录语义](p13-context-inspector.md) 与 [P13 门禁](p13-gates.json)
独立展示实现/离线/协议/供应商/手机/自然日/恢复/发布状态。
