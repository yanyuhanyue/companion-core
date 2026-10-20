# P7 真人 QQ 验收

`NOT_RUN_QQ`，首批 **0/5**，累计 **0/20**。源码候选为
`940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`；以下是待验收项目，没有真人签收记录。
服务器使用许可与私下提供 owner/bot 号码不等于扫码或发送窗口批准。

首批仍缺：专用账号登录批准、Asia/Shanghai 起止时段、最多五条的发送范围、Demo 标签、
手机观察人、停止/核对责任人。开启 delivery 前先核查 Spool/Outbox，
由主人决定允许发出的待处理测试消息；不自动批量发送，也不清队列掩盖历史。

| ID | 首批场景 | 手机发送/收到时间 | 匿名 trace | 预期/实际回复数 | 发送状态 | 自然度1–5 | 私有证据 | 人审 |
|---|---|---|---|---|---|---|---|---|
| QQ-B01 | 问候 | null/null | null | null/null | NOT_RUN | null | null | null |
| QQ-B02 | 普通短问答 | null/null | null | null/null | NOT_RUN | null | null | null |
| QQ-B03 | 连续两轮 | null/null | null | null/null | NOT_RUN | null | null | null |
| QQ-B04 | 同 ID 重连/重复事件 | null/null | null | null/null | NOT_RUN | null | null | null |
| QQ-B05 | 经批准的温和恢复 | null/null | null | null/null | NOT_RUN | null | null | null |

首批通过后再批准扩展窗口。以下沿用既有矩阵，所有实际时间/trace/回复数/发送状态/
原始与缩减来源范围/评分/私有证据/签收人均为 null，状态均 NOT_RUN。
真正执行时每项补齐这些字段与当时的 code SHA/config version；不以空表或 Mock 计入通过数。

| ID | 扩展场景 | 状态 |
|---|---|---|
| QQ01 | 问候与单回复 | NOT_RUN |
| QQ02 | 连续多轮 FIFO | NOT_RUN |
| QQ03 | 跨轮上下文 | NOT_RUN |
| QQ04 | 非主人过滤 | NOT_RUN |
| QQ05 | 群聊/@/临时会话过滤 | NOT_RUN |
| QQ06 | 自回声拒绝 | NOT_RUN |
| QQ07 | 同源重放 | NOT_RUN |
| QQ08 | 同 ID 冲突 | NOT_RUN |
| QQ09 | WS 断线重连 | NOT_RUN |
| QQ10 | Bridge 重启 | NOT_RUN |
| QQ11 | Core HTTP/ACK 故障 | NOT_RUN |
| QQ12 | Worker 失败 | NOT_RUN |
| QQ13 | 平台接受与手机可见 | NOT_RUN |
| QQ14 | 发送/Result 回执丢失 | NOT_RUN |
| QQ15 | UNKNOWN 人工核对与迟到结果 | NOT_RUN |
| QQ16 | 经独立批准的 M1 确认 | NOT_RUN |
| QQ17 | 经独立批准的纠正/遗忘/旧备份恢复 | NOT_RUN |
| QQ18 | 图片/卡片降级边界 | NOT_RUN |
| QQ19 | 长文本/编码/凭据边界 | NOT_RUN |
| QQ20 | 暂停/工具繁忙 | NOT_RUN |

第三人/群聊负例优先 Mock，不主动联系他人；无法安全实测的项目保持 NOT_RUN，不能凑满20。
Demo 的手机可见不证明真实模型质量或费用；端到端计时标注设备误差与采集方法。
异常对象、重复、账号变化、UNKNOWN、冲突/容量、HTTP/WS 异常或超时窗口立即停止新输入与发送，
真人先核对 QQ，不自动重发。QQ ID、正文、Cookie、密钥和私人截图不进入公开仓库。
