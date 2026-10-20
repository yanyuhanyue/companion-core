# P5 人工 QQ 验收表

下表保留 P5 的早期验收快照。2026-10-20 的 P7 后续证据已确认手机 Demo 回复 **5/5**（每输入一条、无重复），
但严格首批场景仍 **0/5**、QQ01–QQ20 严格通过仍 **0/20**；可配对真实客户端 E2E 样本为 **0**，P50/P95 为 `null`。
第 1 条历史自然度为 3/5，其余评分不完整；不能把 Demo 收发或 OneBot ACK 当成自然语义/时延验收。
历史来源见 [P7 固定版本真人报告](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-qq-human-acceptance.md)；
当前空验收表与证据等级见 [P8 人工验收](p8-human-acceptance.md)。P7 旧窗口已关闭，本轮没有新增 QQ 真实验收。
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
