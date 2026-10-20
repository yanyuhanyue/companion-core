# P7 真人 QQ 验收

`LIVE_QQ_HUMAN`：**5/5 条 Demo 回复已由主人手机确认，每次1条、无重复。**
这是收发样本数；严格首批场景仍为 PARTIAL，完整20场景签收仍为 **0/20**。
运行源码 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`，配置 `P7-DEMO-1`。
公开报告不含 QQ 号码、原文、账号截图、provider message ID 或凭据。

## 授权与执行方式

主人分别批准专用账号登录和发送窗口；发送仅限已配置主人，Demo短文字、最多5条、付费0。
计划窗口为 2026-10-20 15:13:13–15:43:13（Asia/Shanghai），达到5次后提前关闭。
初始1条待发已单独列出并获主人明确允许；随后4条均由主人按提示发送。
每次仅对一条白名单事件调用现有 deliver_tick，持久配额先预留再进行外部I/O，
deadline/身份/唯一待发/Spool 状态均核验；常驻 Bridge 的 delivery_enabled 始终 false。
每次暂停的仅是本轮新 Bridge，单次进程退出后重新打开同一 Spool；没有改写产品 Bridge。
首个命令因 Compose run 不支持 --no-build 在进程启动前失败，核对配额/attempt/Spool result
均为0后修正；没有发生发送 I/O，也没有把未知投递重新发送。

## 手机收发样本

主人最后确认共5条、每次1条、无重复。第1条可见时间为15:15（分钟精度），评分3/5；
第2条约15:17、误差最多30s。第3–5条逐项时间及第2–5条评分未提供，保持 null。
OneBot ACK 时间不是手机可见时间；没有成对精确客户端时间，不生成真实RTT或P50/P95。

| 样本 | OneBot ACK 时间 | 手机可见时间 | 匿名 trace | 预期/实际回复 | 发送状态 | 自然度1–5 | 私有证据 | 人审 |
|---|---|---|---|---|---|---|---|---|
| 1 | 07:15:17 UTC | 15:15 | `trace_62a7749fbf5d43521cb6592b3ce195b5ec7e79c536e289aceb25c92c0b720805` | 1/1 | SENT | 3 | `P7-private/phone-sample-1` | 主人确认 |
| 2 | 07:17:27 UTC | 15:17 approximately | `trace_b9a2cf3fb50f5f8b962cf176f9c9f0aa2aed974359166b56c774efe54e0f3f97` | 1/1 | SENT | null | `P7-private/phone-sample-2` | 主人确认 |
| 3 | 07:19:03 UTC | null | `trace_4b4e2f44926c78fbf281754a1908f61685cc1208056024931496adaf4b4a1cd4` | 1/1 | SENT | null | `P7-private/phone-sample-3` | 主人确认 |
| 4 | 07:20:06 UTC | null | `trace_20661c4bfb58caa3f2af051b583a113c80bc480f91f1e2dd94177f194c011522` | 1/1 | SENT | null | `P7-private/phone-sample-4` | 主人确认 |
| 5 | 07:21:02 UTC | null | `trace_d97ce7288c1fa5ddcd70c5ffb95cf65fb73b14c5ee0fab626c3cf112c02e9b7a` | 1/1 | SENT | null | `P7-private/phone-sample-5` | 主人确认 |

样本范围：入站连接检查、问候、普通短问答、连续两轮话题输入；均为主人私聊。
Demo固定回复只声明收到消息；没有启用真实语义问答或长期记忆，不能据此声称上下文质量通过。

## 首批五场景与扩展矩阵

| ID | 要求 | 实际证据与状态 |
|---|---|---|
| QQ-B01 | 问候 | 手机单回复已确认；独立时间/评分记录不齐，PARTIAL |
| QQ-B02 | 普通短问答 | 运输链路已确认；Demo固定回复不证明回答质量，PARTIAL |
| QQ-B03 | 连续两轮 | 逐条 FIFO 与数量已确认；语义上下文未验证，PARTIAL |
| QQ-B04 | 同 ID 重放 | 实机未观察到同 ID 重复；duplicate_suppressed=0，NOT_OBSERVED_LIVE |
| QQ-B05 | 温和断线恢复 | 新 Bridge 5次受控停/启、同一Spool重开，无人审重复；未形成独立故障场景完整签收，PARTIAL |

QQ01–QQ20沿用 acceptance.md。QQ01/QQ02/QQ10/QQ13已有部分收发/进程重开证据，
其余场景未运行；所有未齐全的严格签收仍为0/20。来源范围为唯一主人friend私聊，
缩减范围为脱敏text-only Core事件；不主动联系第三人/群聊，不以Mock填真实场景。
追加发送、真实模型费用、M1确认/纠正/遗忘/旧备份恢复分别需要对应范围与批准。
真实重复或故障状态不足时保留 PARTIAL；不人为制造危险故障，不自动重试 UNKNOWN。
