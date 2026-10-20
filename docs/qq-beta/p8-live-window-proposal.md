# P8 新 QQ 窗口审批提案（未授权执行）

状态：`PROPOSAL_ONLY / LIVE_BLOCKED`。本轮 P8-A 没有新增 QQ 登录、发送、撤回或付费调用。
P7 的五次发送额度已经用完，旧窗口已关闭，**可复用的旧发送许可为 0**。
本提案不把代码合并、分支清理或 P8-A 离线通过解释为部署、登录、发送、付费或 M1 授权。
来源为 [P7 固定窗口记录](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-onebot-live.json)；
下一轮结果仅追加到 [P8 人工表](p8-human-acceptance.md)，不改写 P7。

## 四项相互独立的决定

| 决定 | 本轮状态 | 主人需要批准的具体对象 |
|---|---|---|
| QQ 新窗口 | NOT_APPROVED | 固定 owner/bot、操作者、新起止时间、最多 5 次尝试、新输入范围、停止动作 |
| 真实付费模型 | NOT_APPROVED | [预算合同](p8-model-budget.md)中的实际 provider/model/endpoint/地区/费率、费用上限、密钥保管 |
| M1 基础记忆 | NOT_APPROVED | 明确写入/确认/纠正/遗忘/旧备份恢复范围与证据；不继承模型或 QQ 许可 |
| 部署/备份/恢复 | NOT_APPROVED | 每次固定候选 SHA/摘要、准确变更、卷、数据去向、费用、服务影响与回退责任 |

QQ 窗口即使获批，付费未批时仍只用 Demo/Stub；Demo不能签收语义问答/上下文质量。
M1、Reader、日记、探索/自主分享、DSH、Windows/Android、媒体处理与公开社交写入继续关闭。
许可/EULA、QQ 登录会话与账号操作者权限必须另有适用证据；不自动重新扫码或扩大账号范围。

## 建议窗口：最多五条全新输入

下列 `null/待填` 均为执行阻断项，不能由“默认值”或旧窗口自动补齐。
公开文件只保留配置引用/哈希与匿名角色，真实 QQ IDs、令牌、主机地址与配置路径只留私密审批记录。

| 字段 | 拟定值 / 必填证据 |
|---|---|
| window_id | 新值待主人确认；不能复用 P7 ID |
| fixed_identity | 私密配置内唯一 owner/bot；引用/内容哈希 `null`，运行时再次精确比对 |
| authorized_account/operator | 主人指定账号及操作者；公开只记匿名角色，当前 `null` |
| starts_at / ends_at | `null / null`，必须带 `Asia/Shanghai` 与实际日期；不自动续期 |
| code candidate | 基线 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`；实际新候选完整 SHA 待固定与验证 |
| runtime images/config | 每个 image@sha256、配置版本/哈希、实际 Compose 项目标识待固定；不得直接以 main/latest 替代 |
| max_new_inputs | **5**，只限新 owner friend 私聊；达到任一上限即停 |
| max_reply_attempts | **5 总尝试**，I/O 前持久预留；失败、超时、UNKNOWN 均消耗次数，不退款、不换 epoch |
| max_visible_replies | **5**，每条获准输入最多一条；意外第二回复立即停止 |
| concurrency | **1**，一次只放行一条白名单新事件 |
| initial pending allowed | **0**；旧 PENDING/LEASED/SENDING/RETRY_WAIT/DELIVERY_UNCERTAIN、Spool 未确认项必须逐项清点；有任一项先阻断 |
| scope of content | 主人自发的短文字：问候、普通问答、连续两轮；不含第三人、群聊、真实凭据或媒体 |
| allowed restart/fault | 默认 **不批准**；B05 若需受控 Bridge 重启，必须列入同次部署/启停范围与窗口 |
| persistent daemon sending | `delivery_enabled=false`；不能把它改为 true 来代替五次硬上限 |
| paid requests / real data restore | **0 / 不执行**；分别批准后才可改变 |
| rollback owner | 待指定；窗口期间可立即停止本项目服务的操作者与主人核对人必须明确 |
| initial/final evidence | 匿名队列/Spool/尝试计数、固定版本、许可、审批引用、截止原因与停止后的差异均需记录 |

建议五条输入分配为：B01 问候 1 条、B02 普通短问答 1 条、B03 连续两轮 2 条、
B05 在已单独批准受控重启后 1 条。B05 未获启停许可时该额度不自动改作其他发送。
B04 真实同 ID 重投仅在平台自然发生且落入获准事件范围时观察；没有发生就为 `NOT_OBSERVED_LIVE`。
合成重放另记 `OFFLINE_PASS`，不消耗真人样本名额来伪造平台重复。五条并不覆盖 QQ01–QQ20 全部要求；
未完成、M1、负例、危险故障和语义受限项继续明确保留 `PARTIAL/NOT_RUN`。

## 发送执行器仍是执行前阻断项

已核对 [Bridge CLI](../../companion_onebot_bridge/app.py)：公开 CLI 只有 `--config`，
`run()` 创建接收、上报和持续 delivery task；没有仓库内的 `--max-attempts`、`--deadline`、`pause` 或 `drain` 子命令。
P7 报告中的逐条受控 `deliver_tick` 操作辅助进程不是本基线已提交的限额 CLI，不能直接假定其仍存在、
可重用或已获新窗口许可。

执行前必须提供可审计、经合成故障验证的具体受控执行方案：固定身份/新窗口/白名单事件、
I/O 前持久预留总尝试、截止时间与单实例约束、唯一待发/Spool 校验、未知投递不重发，
以及该进程的精确停止方法和匿名结果计数。新实现或复用私密辅助工具均需固定版本并审查，
不得把人工“数到五”或常驻 delivery=true 当作硬上限。
**本提案未补齐此执行器证据，因此现在不能运行窗口；也不虚构发送命令。**

## 实施前与停止时只用实际存在的命令

以下是未来获批后的停止模板，本轮未对任何主机执行。操作者先从私密部署清单填入
`$P8ComposeArgs`：准确 `--project-name`、同一次部署使用的全部 `-f` 文件、已存在的私密 env/config。
不能凭默认项目名、当前目录或公共示例文件猜测在线项目；命令解析到的容器/卷必须与审批一致。
`config --services` 仅核对服务名称；不将展开配置、环境变量或私密路径上传公开证据。

```powershell
# $P8ComposeArgs must identify the approved deployed project and its exact files.
docker compose @P8ComposeArgs config --services
docker compose @P8ComposeArgs stop onebot-bridge
docker compose @P8ComposeArgs stop companion-worker
docker compose @P8ComposeArgs stop companion-api
```

这些服务确实定义于 [compose.yaml](../../compose.yaml) 与
[compose.onebot-direct.yaml](../../deploy/compose.onebot-direct.yaml)。停止顺序为 Bridge → chat Worker → API；
Core 有 60s、Bridge 有 30s 的仓库 `stop_grace_period`。另有受控一次性发送进程时必须先按其已审批
停止方法终止该进程，确认它不会继续 I/O；上述 Compose 命令不能冒充能停止未归属该服务的进程。
若无法证明独立发送进程已停，保留停止状态并上报，不能重启发送组件。
SnowLuma 是否需要 `docker compose @P8ComposeArgs stop snowluma` 应在本窗口紧急停止范围单列批准；
不删除登录卷、不登出/撤销会话来假装清理完成。1Panel/OpenResty/redroid 不在以上停止范围。

停止不能撤回已经发给平台的请求，不能以停止退出码断言“用户绝不会收到”；
中途取消、超时或缺回执时保留 `DELIVERY_UNCERTAIN`，等待主人检查手机。
本地前台 `python -m companion_onebot_bridge --config ...` 可用其原终端 Ctrl+C 结束，
但它同样不是有次数上限的真人窗口发送入口。

## 立即停止条件与保全

- owner/bot/版本不符、出现第二条回复、账号风控、正文/凭据泄漏、陌生收件人或意外群聊。
- `DELIVERY_UNCERTAIN/UNKNOWN`、Spool 冲突/满/锁、HTTP/WS 连续异常、配额或期限到达。
- 费用硬上限耗尽、无法解释的扣费、多个 Worker/执行器争用、真实配置与获批版本不一致。
- 原服务状态/监听/挂载异常，恢复目标碰到在线卷，或无法证明最新独立 Ledger 完整。

先停止新增输入和本项目发送，再按批准范围停止进程；保留 Core DB/WAL、Spool、稳定 epoch、
最新独立 ErasureLedger、QQ 三卷、审批与匿名证据。禁止 `down -v`、全局 prune、清队列、
将 UNKNOWN/DELIVERY_UNCERTAIN 改回 PENDING、重置费用/次数或新 epoch 解锁重发。
恢复/回退见 [P8 恢复审批包](p8-host-recovery-review.md)。

每条签收同时记录客户端发出与手机可见的可配对时间/误差、匿名 trace、实际回复/尝试数、
发送状态、主人自然度 1–5 和缺失项。时间不完整时 RTT/P50/P95 仍为 `null`；
任何新窗口结束后重新关闭并记录原因，不以“还有剩余额度”为由继续发送。
