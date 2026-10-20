# P8 人工 QQ 验收记录

本轮范围为 P8-A 离线准备。**本轮新增真人记录为空；QQ01–QQ20 严格通过仍为 0/20，首批严格场景为 0/5。**
P7 已有的手机 Demo 收发样本为 5/5，每个输入恰好一条回复、主人未见重复；这一事实保留为历史收发证据。
新窗口、付费模型、M1、部署和真实备份/恢复分别审批，合并代码或删除开发分支不授予这些权限。

## 基线与证据边界

| 字段 | 值 / 来源 |
|---|---|
| P8 离线代码基线 C19 | `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a` |
| P7 报告固定版本 | `904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf`，是独立报告支线，未合入本表 |
| P7 实际运行源码 / 配置 | C19 / `P7-DEMO-1`；属于历史证据，不能声称本轮实机重新观测 |
| P7 手机回复样本 | `5/5 LIVE_QQ_HUMAN`；不能转换为五个严格场景 PASS |
| 严格首批场景 / 完整矩阵 | `0/5` / `0/20` |
| 新增真人输入 / 回复 / 尝试 | `0 / 0 / 0` |
| 可配对真实客户端 E2E 样本 | `0`，`P50_ms=null`、`P95_ms=null` |
| P7 OneBot 组件跨度 | `n=5`，不是手机往返 SLA |
| 新增费用 / 记忆 | 真实付费调用 `0`，M1 仍关闭 |

历史来源按固定版本引用：[P7 gates](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-gates.json)、
[P7 手机签收](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-qq-human-acceptance.md)、
[P7 OneBot](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-onebot-live.json)。
P8 机器门禁见 [p8-gates.json](p8-gates.json)，证据索引见 [p8-evidence-index.md](p8-evidence-index.md)；机器门禁与主人签收独立。

```json
{
  "new_human_records": [],
  "new_live_inputs": 0,
  "new_live_replies": 0,
  "new_live_attempts": 0,
  "strict_first5_accepted": 0,
  "strict_20_accepted": 0,
  "live_e2e_samples": 0,
  "p50_ms": null,
  "p95_ms": null
}
```

`get_login_info/get_status/get_version_info` 是只读协议身份测试，归入 `ONEBOT_PROTOCOL`。
`send_private_msg` 的成功 ACK 仅表示平台 API 返回，不证明手机已经看见；手机签收归入 `LIVE_QQ_HUMAN`。
P7 原字段 `probe.private_message_delivery_verified=false` 只描述该只读 probe 的能力，保留不改；
另一个 `phone_receipt_evidence.reply_samples_confirmed=5` 承载真人事实。两者不互相覆盖。
`SYNTHETIC/OFFLINE/ONEBOT_PROTOCOL/LIVE_QQ_HUMAN` 是不同证据等级；引用历史字段 `LIVE_ONEBOT` 时保留其原名称与来源。

## 首批五场景：新记录独立于历史样本

| 场景 | 严格要求 | P7 保留结论 | P8 新输入 / 回复 / 尝试 | P8 状态 |
|---|---|---|---|---|
| QQ-B01 | 问候、单回复、成对客户端时间、人审 | 手机单回复已确认，独立记录不齐，PARTIAL | 0 / 0 / 0 | NOT_RUN |
| QQ-B02 | 普通短问答实际回答输入问题 | Demo 只证明运输，PARTIAL | 0 / 0 / 0 | NOT_RUN |
| QQ-B03 | 连续两轮语义上下文与顺序 | FIFO/数量有证据，语义未验证，PARTIAL | 0 / 0 / 0 | NOT_RUN |
| QQ-B04 | 平台实际同 ID 重投被去重 | NOT_OBSERVED_LIVE，不能用不同 ID 同文替代 | 0 / 0 / 0 | NOT_RUN |
| QQ-B05 | 温和断线/重启恢复、无重复、独立签收 | 同 Spool 受控重开有部分证据，PARTIAL | 0 / 0 / 0 | NOT_RUN |

历史第 1 条评分 `3/5`，手机时间为分钟精度；第 2 条手机时间约有 `±30s` 误差；
其余逐条时间和第 2–5 条评分未齐全，继续为 `null`。本轮不反填 P7 时间、评分或场景结论。

## QQ01–QQ20 严格矩阵

下表是**新窗口的空验收表**。输入/输出是本轮真实数量，未运行填 `0/0`；
时间栏顺序为 `client_sent_at / phone_visible_at / clock_error_ms`，未测均为 `null`。
自然度仅由主人按 1–5 分填写；未测填 `null`。`C19` 在每一行均指本页顶部完整 SHA，
未来运行前须将实际新候选 SHA、镜像摘要与配置引用固定到窗口记录，不能仅沿用此基线。
离线覆盖表示源码中存在的测试，运行结果另见 P8 证据索引；它不是本表的真人通过证据。

| ID / 要求 | 必须真人 | 离线覆盖 | 本轮观察等级 / 历史线索 | 输入 / 输出 | 成对时间 / 误差 | 自然度 | 候选 SHA | 可复现性 | 严格状态 / 缺失原因 |
|---|---|---|---|---|---|---|---|---|---|
| QQ01 问候：主人私聊、单回复、不虚构 | 是 | O1 / O2 | NONE_NEW；P7 手机 PARTIAL | 0 / 0 | null / null / null | null | C19 | 离线可重放；真人未运行 | NOT_RUN；缺新窗口与逐条签收 |
| QQ02 连续多轮：FIFO、每输入恰好一回复 | 是 | O2 / O3 | NONE_NEW；P7 数量/FIFO PARTIAL | 0 / 0 | null / null / null | null | C19 | 离线可重放；真人未运行 | NOT_RUN；缺语义多轮及完整人审 |
| QQ03 跨轮上下文：正确引用前轮、无假记忆 | 是 | O3 / O4 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | Stub 可重放；真人未运行 | NOT_RUN；Demo 固定回复不能证明上下文 |
| QQ04 非主人：拒绝进入 Core/私密检索/回复 | 不要求；只做合成负例 | O1 / O4 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；禁止主动联系第三人；离线结果单列 |
| QQ05 群聊/@：拒绝且不扩大收件人 | 不要求；只做合成负例 | O1 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；不加入/骚扰群聊取证 |
| QQ06 自回声：不再入件、不循环回复 | 不要求；合成事件优先 | O1 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；未观察新平台自回声 |
| QQ07 同 ID 重放：持久去重、无重复回复 | 若宣称 LIVE_PASS 则是 | O1 / O2 | NONE_NEW；P7 NOT_OBSERVED_LIVE | 0 / 0 | null / null / null | null | C19 | 合成可重放；真实重投不可强制 | NOT_RUN；平台未实际重投，禁止危险制造 |
| QQ08 同 ID 不同内容：冲突停止、原记录保全 | 不要求；离线故障注入 | O1 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；不向平台制造冲突 |
| QQ09 WS 断开/重连：有界恢复、不双回 | 是；需故障窗口批准 | O2 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 本地 WS 可重放；实机未运行 | NOT_RUN；缺独立恢复与手机签收 |
| QQ10 Bridge 重启：同 epoch/Spool、无双回 | 是；需启停批准 | O1 / O2 / O5 | NONE_NEW；P7 重开 PARTIAL | 0 / 0 | null / null / null | null | C19 | 离线可重放；实机未运行 | NOT_RUN；旧五次重开不能补新场景 |
| QQ11 Core HTTP 故障：入件/回执保全、不双回 | 不要求；离线故障注入 | O2 / O5 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；无新故障窗口 |
| QQ12 Worker 死亡：lease 恢复、费用保留 | 不要求；离线故障注入 | O3 / O6 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；未实机故障验证 |
| QQ13 平台接受：ACK、手机可见与单回复分列 | 是 | O5 / O7 | NONE_NEW；P7 手机/ACK PARTIAL | 0 / 0 | null / null / null | null | C19 | 协议 Stub 可重放；手机未运行 | NOT_RUN；新 ACK/手机成对证据均缺 |
| QQ14 发送回执丢失：不确定、停止自动重发 | 不要求；离线故障注入 | O5 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；不得主动制造危险丢回执 |
| QQ15 人工核对未知：本人检查后 SENT/CANCELLED | 若做实机核对则是 | O5 / O6 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 合成核对可重放；真人未运行 | NOT_RUN；无真实 UNKNOWN，不制造补发 |
| QQ16 M1 确认记忆：授权、来源、确认可追溯 | 是；另批 M1 | O4 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放；M1 实机关闭 | NOT_RUN；M1 授权与运行验收缺失 |
| QQ17 纠正/遗忘/旧备份：闭包与 tombstone 不复活 | 是；另批 M1/恢复 | O4 / O8 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 合成可重放；真实恢复未运行 | NOT_RUN；非空真实备份恢复未验证 |
| QQ18 图片/卡片：安全文字降级、不下载敏感地址 | 是；仅获批安全样本 | O1 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN；未获新媒体输入窗口 |
| QQ19 长文/编码/凭据：拒绝或脱敏、不堵 FIFO | 不要求；只用合成凭据 | O1 / O6 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | 离线可重放 | NOT_RUN_LIVE；不发送真实凭据作测试 |
| QQ20 暂停/工具忙：聊天独立、克制且不越权 | 是；工具继续禁用 | O3 / O6 | NONE_NEW | 0 / 0 | null / null / null | null | C19 | Stub 可重放；真人未运行 | NOT_RUN；无自然聊天与忙态人审 |

覆盖索引（均为仓库内真实测试文件，不表示已在实机执行）：

- O1：[test_onebot_direct.py](../../tests/test_onebot_direct.py)，身份/群聊/回声/长文/媒体/稳定 epoch/冲突/容量/重开。
- O2：[test_onebot_runtime.py](../../tests/test_onebot_runtime.py)，本地真实 WS/HTTP 重连与单回复。
- O3：[test_m0.py](../../tests/test_m0.py)，FIFO、Worker lease、预算、越权与聊天路径。
- O4：[test_m1.py](../../tests/test_m1.py)，上下文、来源、记忆纠正与遗忘；不授权实机 M1。
- O5：[test_onebot_delivery.py](../../tests/test_onebot_delivery.py)，发送意图、丢 ACK、迟到回执与人工核对。
- O6：[test_resilience.py](../../tests/test_resilience.py)，不确定状态、预算、脱敏、Worker 锁与纠正。
- O7：[test_onebot_network.py](../../tests/test_onebot_network.py)，合成服务的身份 probe 和发送边界。
- O8：[test_onebot_erasure.py](../../tests/test_onebot_erasure.py)、[test_release_migration.py](../../tests/test_release_migration.py)，最新 Ledger/旧库/tombstone/恢复后不重发。

负例在本地通过时只能记 `OFFLINE_PASS`，本表真人统计保持独立。
离线重放已观察 event ID 必须标注“离线重放已观察事件”；不能写作“平台新发生同 ID 重投”。
无法安全取得的真人场景保留 `NOT_RUN_LIVE/NOT_OBSERVED_LIVE`，严格总数不补足为 20/20。

## 时间与后续逐条记录合同

同一进程内部阶段使用可信单调时钟计算区间，并记录阶段边界、进程/时钟身份和证据等级。
不得相减不同进程或机器的 monotonic 值。跨进程/手机 E2E 的边界是“主人客户端发出该条输入”至
“主人手机可见该条回复”；两端必须能以匿名 trace 配对，记录时区、精度、时钟同步来源与最大误差。
仅有服务端 ACK、分钟时间或不完整配对时，E2E/RTT/P50/P95 均为 `null`。
有完整采样后仍需记录 `n` 与误差；少量样本的分位数不代表长期 SLO。

未来每条新真人记录至少包含：`window_id`、`scenario_id`、匿名 `trace_id`、候选完整 SHA/镜像摘要、
私密身份配置引用的哈希、单次发送许可、输入/预期输出/实际输出/尝试数、两端时间与误差、
`SENT/CANCELLED/DELIVERY_UNCERTAIN`、主人自然度 1–5、可复现步骤与脱敏证据引用。
原文、QQ IDs、平台 message ID、截图、令牌与私密拓扑不进入公开文件。
新记录只在 [新窗口提案](p8-live-window-proposal.md) 的独立授权与实施门禁闭合后追加。
