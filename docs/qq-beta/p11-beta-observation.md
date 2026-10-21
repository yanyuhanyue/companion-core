# P11 私有 Beta 观测与接续

`production_ready=false`。本文是离线交付及后续操作说明，模板与配置不代表新的运行授权。
[私有窗口申请模板](p11-beta-window.template.json) 默认所有权限未申请、时间与身份为空。
它是审阅清单；获准后仍须使用现有 Core/Bridge/PaidModel 的实际合同完成绑定，不直接作为运行配置。

## 当前基线

产品基线 `da036dc7e1fcdda6a36cce4ca00457ebc7c0e4f1` 的
[完整 CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39617115888) 已通过。
P11 开发从 `e742e07d11d2e1e245c9073fc28e09c20f4a7d8f` 开始；其变化仅为 P10 无人值守服务器验证记录，
[文档 CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39628723439) 已通过。
真实样本和限制以 [P10 运行记录](p10-runtime-observations.md) 为准。
5 条 QQ 实际签收、应用集隔离恢复、独立合成 M1 生命周期和 Worker 活动均有各自证据，
不能据此宣称 QQ 业务 M1、手机图片或长期日常体验已通过。

## 本地 Context Inspector

在已获准读取的本地 Core 数据目录使用现有配置：

```powershell
.venv/Scripts/python.exe -m companion inspect-context --trace-id '实际 trace_id'
```

命令只打开 SQLite `mode=ro`，使用一次有界读事务查看已提交 WAL。
不会初始化 Core、加载语义模型、迁移、修复、重放账本或调用外部服务。
输出 trace/event/job 的哈希引用、当前隐私 epoch、已冻结的历史和记忆来源、
Job/Outbox 状态、模型尝试及可确认的 Token。
不输出聊天/记忆正文、QQ 号、URL、密钥、文件路径或错误原文。
这些哈希依然属于私有关联数据，原始报告保存在 `.local/`，公开前另行做汇总。

`configured_*` 仅表示查询时的本地配置；运行镜像未知时保持 null。
冻结来源标记 `NOT_REVALIDATED`，epoch 漂移标记 `STALE_EPOCH`，
不能替代 dispatch/delivery 的原生来源复核。
删除后的输入不输出旧上下文来源。Inspector 不生成新的持久派生库；已导出的私有报告需要纳入数据保留/删除范围。

SETTLED 且记录了付费 dispatch 的调用才提供已记录 Token；UNKNOWN/RESERVED 或演示调用保持 null。
`accounted_or_reserved_micro_usd` 保留未知调用的占用，`configured_price_micro_usd` 是配置价估值。
这两者都不是供应商账单。`SENT` 也不是手机可见签收。
手机签收、配对 E2E 和供应商账单在此工具中始终为 null，分别由外部证据补充。
Worker 活动仍使用 `status/observe`，与业务成功分开。

## 主人验收与反馈

将 [反馈模板](p11-beta-feedback.template.json) 复制到当前私有验收目录，每条实际输入一份。
格式由 [schema](../../contracts/p11-beta-feedback.schema.json) 约束。
完成后运行 `.venv/Scripts/python.exe scripts/validate_beta_feedback.py .local/本次反馈.json`；
验证器只输出字段位置、记录哈希和已填写时间对的差值，不回显私人理由，也不认证证据真伪。
记录准确候选、实际镜像、逐项授权引用和 Inspector 报告哈希；不要把旧窗口授权填到新窗口。
未测项保留 null；已检查且无事故才将 `incidents` 填 `[]`。
理由字段可能包含私人内容，不直接提交公共仓库。

1. 先在准确批准的窗口中发送一条自然文字；核对手机只有一次可见回复，再记录自然度和相关度。
2. 引用/卡片/转发按当次批准类型分别试一条，检查“读到的内容/仅取得引用 ID/未读取”与实际一致。
3. 历史与长期事实同轮使用时，同时包含一个近期指代和已确认事实；看是否承认冲突或不知道。
4. R5 单独批准后进行记住、确认、纠正、忘记及恢复闭包；下一窗口再问，不能从旧快照复活。
5. 图片、私人日记、兴趣和主动分享分别验收；跨日质量只随真实自然日追加。

每项可用 1–5 分评价；没有观察机会保持 null。
手机 E2E 只填同设备 UTC 或已校准的双端配对时间，附证据哈希；服务器阶段耗时不能填入手机时间。
账单金额必须有独立凭据。格式校验只能检查结构，不能认证真人、供应商或平台事实。

## 模块开关与波次

| 范围 | 默认/准入 | 本轮可完成的前置 | 需要实际证据 |
|---|---|---|---|
| M0 文本 | 有界 window + R3/R4 | 身份、预算、幂等、停止与 handoff | 新时窗和本人签收 |
| 富消息/图片 | 各 scope 独立，媒体费用另批 | 来源结构、恶意输入、容量与下载负例 | 每种类型 QQ/手机往返 |
| B1 历史+事实 | history/memory 双重许可 | 同 Job 预算、来源删改再验 | 获准事实的自然多轮 |
| B2 写入 | 停止聊天窗口后的独立 R5 | 原生记忆与最新 Ledger 执行包 | 主人 QQ 操作与签收 |
| C1/D1 | affect/diary 默认关闭 | 证据来源、TTL、私人草案、删除 | 真实反馈与翌日接续 |
| D2 | exploration/social 默认关闭 | 低成本筛选与只读候选 | 独立 Reader/主动发送许可 |

现有 `proactive_enabled` 通用默认值为 true；私人 Beta 明确设 false，
不能把新功能默认关闭误读为全局主动发送开关也关闭。
任何值都不替代许可，定时器或模型输出不获得发信权。

## 停止、接续和回退

继续使用 [停止窗口 handoff](p10-stopped-window-handoff.md) 与
[运行观察说明](p10-operations-observation.md)。每次最多五条的上限不扩大，
周期总额度尚未实现，禁止循环建窗口刷新额度。
新窗口必须在最新 Core/Spool 状态上衔接 Budget Journal、Window Journal 和 ErasureLedger。
保留历史费用、UNKNOWN、发送不确定与删除记录；不清库、不重置旧窗口、不覆盖最新账本。

发生错误收件人、隐私泄漏、遗忘复活、越权/越费、重复可见回复、UNKNOWN 自动重试或账本不一致时，
停止该窗口并保留原始证据。分别核对 Core 与 Bridge 停止回执；Core 不可联系时记 STOP_UNCONFIRMED。
回退关闭对应 scope/模块并换回已验证镜像；不得回滚事实库或独立日志到旧快照。
恢复沿用已经验证的应用集流程；QQ 登录卷、原服务及整机恢复仍未纳入此前 PASS。
