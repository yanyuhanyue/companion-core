# P6 真人 QQ 验收

状态 `NOT_RUN_QQ`，首轮 **0/5**，累计 **0/20**。
没有扫码、真 OneBot 握手或实际 QQ 发收；所有离线用例归 `OFFLINE_FIXTURE`，不占真人名额。

## 待批准窗口

许可证明、目标主机快照/备份确认、机器人与唯一 owner 的私有配置、Asia/Shanghai 起止时段、
首轮最多 5 条、停止与回退负责人均需落实。QQ 身份、Token 与正文不得写在本表。
先 delivery_enabled=false 验证入站与唯一接管，再由主人批准开启当轮发送。

## 首轮五条

| 场景 | 人工客户端唯一回复/送达 | 发送/收到时间 | 结果 |
|---|---|---|---|
| 问候 | 未核对 | null / null | NOT_RUN |
| 短问答 | 未核对 | null / null | NOT_RUN |
| 连续两轮 | 未核对 | null / null | NOT_RUN |
| 重复防线 | 未核对 | null / null | NOT_RUN |
| 正常恢复 | 未核对 | null / null | NOT_RUN |

累计 20 场景依 [既有验收矩阵](acceptance.md) 执行；仅经真人签收的真实场景计数。
故障、越权和负例优先隔离 Mock，单独分栏。跨轮记忆/纠正/遗忘及真模型预算另行批准。
任何重复、错误对象、UNKNOWN 或账号异常都停止当轮并人工核查；不得重发未知消息。
