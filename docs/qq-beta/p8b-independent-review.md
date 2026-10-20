# P8-B 独立安全审查

基线 `a33d3cb77f348828b17e4bb48c621c53cfcdc017`；B1 最终候选
`5cf997a801f1ab3826456b9264dcf67e71f3eba4`，B2 最终候选
`1a452d287185e969cff7ecb52a469e218b194d57`。
主代理独立审查 B1 初稿及增量，非作者子代理再审 B1 资金保全；另一非作者子代理审查 B2。
读取原始 diff、新增文件和调用路径，使用临时 SQLite、MockTransport/FakeOneBot 复现。
没有真实提供方、QQ、VPS、私密数据操作。

结论：下列可复现 P1 均已修复，限定审查范围内没有剩余可操作的 P0/P1/P2。
这不是全仓库渗透测试或生产安全证明。完整组合检查及同 HEAD CI 的实际结果另见
[执行记录](p8b-execution.md) 与最终 PR 外部证明。

## 独立复现与修复

| 编号 / 级别 | 可复现触发与后果 | 修复与证据 |
|---|---|---|
| R-B1-01 / P1 | 预留后、I/O 前取得 DB 快照；请求开始后仅回滚 DB。中间补丁只核金额/请求集合，同一 request 可再次 HTTP。 | 独立 journal 核对持久 I/O-start fence，较新日志阻断旧 DB；独立红例→绿。 |
| R-B1-02 / P1 | 旧 model_usage 的 UNKNOWN/RESERVED 没有 admission；中间 JOIN 忽略它，仍可抢占提供方并发。 | budget.reserve 先阻断旧未决账目；同提供方/模型旧记录独立红例→绿。 |
| R-B1-03 / P1 | 一次取时后，tokenizer/锁等待/fsync 跨过审批截止仍开始 HTTP。 | budget.complete 要求新鲜 clock callback，取锁、输入证明及 fsync 后重查；独立延时红例→绿，HTTP=0。 |
| R-B1-04 / P1 | 已开始的 Mock HTTP 返回已知超额费用时撤销审批或旋转 journal 路径；依赖当前审批的结算失败，账目仍只有小额 RESERVED，恢复旧配置后可继续预留。晚到发票同样丢差额。 | admission 保存不可变原始 journal path/id，结算独立于当前启用审批。原始 journal 损坏也提交已知较高费用、UNKNOWN 与停止；四种撤销/路径变更独立红例→绿，另覆盖损坏日志及并发结算。 |
| R-B2-01 / P1 | 旧头 `7e8d23033d4f63b12172da3780de036fc27e56e9` 最后检查后才 await 实际 DNS pin hook；解析延迟至截止仍 dispatch 并 ACK。 | window.send_once 在 DNS hook 后添加最终 request hook，复核 journal/fence/IO_STARTED/期限；过期保留已预留名额并停止。自定义 preflight、实际 shipped DNS 两种独立红例→绿。 |

最终候选的修复位置：R-B1-01 `companion/paid_budget.py:92`（journal 核对在142–154），
R-B1-02 `companion/budget.py:179`，R-B1-03 `companion/budget.py:392`，
R-B1-04 `companion/budget.py:277` 与 `companion/budget.py:348`；
R-B2-01 `companion_onebot_bridge/window.py:568` 与 `companion_onebot_bridge/window.py:651`。
行号分别绑定本报告开头的 B1/B2 完整 SHA，旧红例代码与最终修复树不能混为一谈。

B1 第一轮三个独立红例及不可能 cache usage 负例：早期 `510735c` 上 **4 passed / 8.95s**，
`e1c8fc67b5819e0da8f7d03c8446302a4e479677` 上重新运行 **4 passed / 7.01s**。
之后对增量逐行复核；最终 `5cf997a` 重跑上述四项 **4 passed / 7.41s**。
最终 B1 固定头独立资金矩阵 **7 passed / 14.94s**：
撤销审批/旋转路径各自的在途费用和晚到发票、两个 Store 并发结算守恒、
最新 UNKNOWN journal 对旧 RESERVED DB 的零 HTTP 阻断、日志损坏仍保留超额费用及停止。
合成费用 900004 + 34 microUSD 守恒；这不是真实扣费。

B2 精确最终头独立对抗集 **7 passed / 16.87s**，公开窗口集 **47 passed / 92.63s**。
除两个截止红例外，检查了探测期间回滚 SQLite 但保留较新 journal、第五次 UNKNOWN 后重启、
篡改 lease 内容、旧未确认 result 禁止顺带 drain、journal 的 .. 路径别名；这些负例被拒绝。
私密红绿日志和源文件 SHA256 保存在最终执行清单所引用的受限诊断目录，不随公共包发布。

## 作者补充复现及增量复核

以下进入最终 62 项预算测试，独立审查读取修复增量，未将作者测试冒称为独立发现：

- 提取和 reconciliation review 使用不同 logical ID，绕过每请求一次上限：统一 attempt_key，红1→绿1。
- 四个身份/证据位置可为空：配置非空校验，红4→绿4。
- 不同 Settings 的 ModelClient 可消费原 admission：绑定实际 adapter Settings 身份，红1→绿1。
- 同端点轮换凭据后复用已批准窗口：私密 per-window HMAC 绑定，分别拒绝在途/下次调用前换 Key。
- chat reservation 不能支付 activity；拒绝场景不一致，普通聊天不增加新的付费路由调用。
- 停止新准入不能丢弃另一已在途请求的费用；停止后继续保全记账证据。
- UNKNOWN 在尚未 I/O 时结算后，旧 DB 不能恢复为可执行 RESERVED：journal 同时绑定状态、金额与 fence。
  最后 custody/状态组作者 **5 failed → 8 passed**；最终独立矩阵确认修复。

既有月限额使用 BEGIN IMMEDIATE 的原始多进程超额竞态为 **NOT_REPRODUCED**；
不能把缺少新的日/单请求/窗口/提供方并发语义写成旧月事务已经发生竞态。

## 检查边界与限制

- 所有实际 text 调用通过同一 budget 事务准入；ModelClient 不持有存储、owner 权限或投递职责。
  不证明 tokenizer/framing、JSON 附加模板、工具或媒体计价时拒绝实际 HTTP。
- 预留不期待缓存命中；UNKNOWN 不自动释放金额、次数或并发，不自动重试/清零。
  异常 usage、价格/发票差异、配置身份变化和账本不一致均停止新准入。
- Bridge 复用 HTTP v1、原 Outbox、Spool、deliver_tick 和 OS 锁；常驻 delivery=false。
  身份/epoch/库存/配置/逐项内容固定；预留发生在 I/O 前，输入/尝试各最多5、并发1。
- 审批、费率、提供方 framing 和镜像库存哈希是受信任操作者的证据引用，不是软件认证邮件或
  提供方内部实现的证明。实际证明缺失，真实收费和 QQ 仍 BLOCKED。
- 单 DB 只限制经过该入口的调用；其他数据库/客户端共用账户必须由唯一入口或账户级控制处理。
  软件估价不能绝对控制提供方最终扣款；真实账户封顶、币种/优惠/账单需另验。
- 独立较新 journal 能检测只回滚 DB 或日志；同时回滚/丢失全部独立记录不能本地识别。
  最新预算/窗口 journal 与 ErasureLedger 必须在独立故障域保全，恢复不自动获得新额度。
- 截止检查不能撤回已许可、在途请求。停止或 ACK 不等于手机可见，未知保留，不计算伪 E2E。
  staged frame 不证明 QQ 来源；未来真实采集、窗口与操作者另批。
- B4 只改离线脚本/设计，保留间接回忆失败，真实 token/cache/cost/provider latency 全为 null。
- 本地 Docker daemon 关闭；容器 UID/卷、Chromium/native 须由最终各 Draft PR 精确 HEAD CI 另证，当前待核实。
  既有手机/恢复记录不提升本轮生产门禁，production_ready=false。

三个作者子任务对本轮新临时/缓存目录的清理均被自动审批拒绝，原因仅为 blocked by policy。
未删除或换工具重试；原受保护资料、诊断与私密证据均保留。
