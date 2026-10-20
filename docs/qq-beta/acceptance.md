# P5 人工 QQ 验收表

当前 Gate 0 开发验证中；Gate 1/2 **NOT_RUN**，真实 QQ 0/20，真人自然度、实机延迟均未测。
离线测试的 Mock 回复、100 次合成负载、本地 TCP 与 ASGI 不计入实机数量。

首轮单独记录 owner/bot 的私有证据引用、Asia/Shanghai 起止时段、最多5条、回退人和费用上限。
公网/PR 仅保存匿名 trace、固定 candidate SHA/digests、预期/实际数量、耗时和匿名审核意见。
消息原文、QQ IDs、凭据和图片不进 Git/CI。后续真人以1–5分评价自然度、重复、自主越界和误记。

| 场景 | 离线覆盖 | 真实结果 / 私有证据 | 人审 |
|---|---|---|---|
| QQ01 问候 | direct / delivery | NOT_RUN | — |
| QQ02 连续多轮 FIFO | benchmark100 | NOT_RUN | — |
| QQ03 跨轮上下文 | Core 原有回归 / benchmark | NOT_RUN | — |
| QQ04 非主人 | direct 拒绝参数组 | NOT_RUN | — |
| QQ05 群聊 / @ | direct 拒绝参数组 | NOT_RUN | — |
| QQ06 自己回声 | direct message_sent | NOT_RUN | — |
| QQ07 重放 | direct / runtime 重连 | NOT_RUN | — |
| QQ08 同 ID 冲突 | Spool conflict | NOT_RUN | — |
| QQ09 WS 断开重连 | runtime 实际本地 WS | NOT_RUN | — |
| QQ10 Bridge 重启 | Spool / delivery 重开 | NOT_RUN | — |
| QQ11 Core HTTP 故障 | pending / ACK lost | NOT_RUN | — |
| QQ12 Worker 失败 | Core 原有回归 | NOT_RUN | — |
| QQ13 平台接受 | Mock OneBot / 本地 TCP | NOT_RUN | — |
| QQ14 丢失发送回执 | delivery uncertain / crash | NOT_RUN | — |
| QQ15 人工核对未知 | Core 原有核对回归 | NOT_RUN | — |
| QQ16 M1 确认记忆 | Core 原有回归；首期关闭 | NOT_RUN | — |
| QQ17 纠正/忘记/旧备份 | erasure + Core 既有回归 | NOT_RUN | — |
| QQ18 图片/卡片 | direct 降级，不下载 | NOT_RUN | — |
| QQ19 长文本/编码/凭据 | direct limits/redaction | NOT_RUN | — |
| QQ20 暂停/工具繁忙 | Core 回归 / benchmark | NOT_RUN | — |

失败场景应记录 source receive、Spool、Core、Outbox 和 QQ 双侧证据。DELIVERY_UNCERTAIN 不盲目
制造或自动恢复；真人先核查 QQ。未授权的第三人/群聊场景保留离线结果，不能伪造真实实测。
人工每条完成后记录预期/实际回复数、单目的地、客户端完整往返、状态和评分；
20条只是阶段性下界，不作为长期P95可靠性结论。
