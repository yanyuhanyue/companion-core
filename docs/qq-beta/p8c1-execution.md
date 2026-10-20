# P8-C.1 集成交付与验收记录

本轮将 QQ 内置指令统一为起始 ASCII `/`，并实现默认关闭的 live 文本有界实验入口。
公开材料脱敏、提供方技术合同核查、恢复与六项独立批准的依赖一并收口。
原始基线及范围见 [baseline](p8c1-baseline.md)；机器状态见 [gates](p8c1-gates.json)。
本记录中的代码验证均为离线合成验证，真实运行准入独立保留。

## 行为与边界

- 例如 `/记住这个：我喜欢绿茶`、`/暂停写日记`、`/发送状态` 才进入对应控制逻辑。
  不带 `/` 的文本、引用里的命令、相似前缀、全角斜杠均继续普通聊天；
  旧已标记日记命令的遗忘与旧备份回放保留兼容。
- 首期有界 live 路径仅接收固定 owner/bot 的 friend/private 普通文字。
  记忆、日记、Reader、自主活动和设备等扩展保持关闭；该路径拒绝 `/` 指令。
  每个批准输入最多一次 chat 模型尝试、一次回复；模型尝试额度与外部发送额度分别保留。
- 继续使用原 Core 预算与独立 Budget Journal、Spool、Outbox 和 Window Journal。
  Bridge 通过 HTTP v1 获得小型批准回执，不持有模型凭据、Core DB 或遗忘账本管理权。
  截止时间、身份/epoch、源码/配置、账本、正文及 lease 必须在各自 I/O 边界重新核验。
- 原 Demo 入口仍要求 Demo 模式与功能关闭。
  local_synthetic 的 Mock 成功链只证明接口可联用，不证明平台真实来源、QQ 手机送达或供应商计费。
  staged CLI frame 不能用于 approved_live 的真实事件来源证明。
- local_synthetic 的 Core、协议探测和发送均限制实际目的地址为 loopback，
  模型传输必须为显式 MockTransport。Core 停止先记录到独立 Budget Journal，
  CLI 与持久状态区分已确认和未确认；旧 DB 不能撤销最新 journal 中的停止记录。
- 模型最终请求在所有阻塞检查完成后重新核对 live、付费、费率和 job/admission lease 的期限；
  截止后阻断 dispatch，已开始的费用仍可结算或保留 UNKNOWN。

## 组成与固定来源

| 内容 | 来源 |
|---|---|
| 公开六项准入及匿名目标字段 | [PR #30](https://github.com/yanyuhanyue/companion-core/pull/30)，脱敏候选 `e24d8788d4c597f9e938d96d87ed76805504f044` |
| Reader 容器成功夹具与 Worker UNKNOWN 回归 | [PR #31](https://github.com/yanyuhanyue/companion-core/pull/31)，`0c26f2e538c4172865d9e1473c5601cb0972c41d` |
| QQ 指令防误触 | [PR #32](https://github.com/yanyuhanyue/companion-core/pull/32)，`3e13aa30342b5fb52e9c3f7edfc561384ac5ad1e` |
| 有界 live 文本及审查修复 | [PR #33](https://github.com/yanyuhanyue/companion-core/pull/33)，`41163a0230503fd7762ec022c748f889ec4fbea9` |
| 供应商合同及恢复批准增量 | 独立文档提交 `86731cc3f96a752e0bfea1cfa27c2d9d2808440b`，已移植到集成分支 |

各来源候选的成功记录不替代最终组合的验证。最终产品候选、独立审查与完整本地结果写入
[gates](p8c1-gates.json)；最终 PR HEAD 和合并后 main SHA 的 CI 逐 job 证据写入 PR 外部交付记录。
该方式避免用文档内自指 SHA 冒充正在验证的提交。

## 已完成的组合验证

| 检查 | 结果 |
|---|---|
| #31、#30 脱敏与 `/` 指令第一次组合：指令/Worker/准入/隐私 | Ruff PASS；88 passed，119.45s；1 项已有弃用警告 |
| `/` 与 live 消息入口冲突解决：指令/live 窗口/新增交叉用例 | Ruff PASS；73 passed，109.99s；0 skipped / 0 warnings |
| 总门禁、提供方合同门禁、公开材料隐私 | Ruff PASS；24 passed，0.60s；0 skipped / 0 warnings |
| 最终产品候选完整检查 | Ruff PASS；1221 passed，6 skipped，1 warning，1088.97s |
| 最终门禁记录、合同及公开材料检查 | Ruff PASS；24 passed，0.57s |

上述检查均使用仓库 `scripts/check.ps1`，临时数据位于工作树 `.local/test-temp`。
新增 6 个交叉用例覆盖裸命令词完成正常模型/Outbox/OneBot Mock 链且无控制副作用，
以及有界入口收到 `/` 命令时零模型请求、零发送、零队列任务。
最终产品候选为 `e5bdc075be060e1366a8171f43b84fac408b27a5`。
完整检查采用 `./scripts/check.ps1`，固定工作树 import 路径并使用已缓存的离线模型。
随后仅整合独立报告与实测门禁记录；运行源码、脚本和测试与该候选逐文件相同。
唯一 warning 为已有 Starlette/httpx 测试客户端弃用提示。
6 项 skip 为本机缺少符号链接权限的两例，以及由独立 browser-reader CI 运行的四例 Chromium 沙箱测试。
[独立报告](p8c1-independent-review.md) 已关闭五项 C1 问题，审查范围没有未解决的已证实 P0/P1/P2。
机器状态记录 `OFFLINE_COMPATIBILITY_READY`；最终 PR HEAD 与合并后 main 的精确 CI
仍须在外部交付记录中逐 job 核实，满足后才能对外声明 `P8-C.1_OFFLINE_READY`。

## 真实运行仍缺的证据

提供方结论为 `PROVIDER_CONTRACT_UNVERIFIABLE`：
当前 ChatML/tokenizer/结构化后缀与实际供应商计费预处理的等价关系、账户硬封顶及唯一入口没有可靠证明。
两种替代设计仅经过设计审查，不能激活当前 PaidModelApproval。
具体来源与限制见 [provider review](p8c1-provider-contract-review.md)。

恢复、部署、QQ 新窗口及付费模型保持 BLOCKED，M1 和 Reader 保持 DISABLED。
最终目标未在本轮盘点；历史 Demo 手机样本不计入新目标验收。
真实 usage、账单、手机延迟为 null，严格 QQ 场景仍 0/5、0/20，`production_ready=false`。
可选 C5 检索评测保留为独立后续工作，本轮不宣称实际 Token 或费用降低。

下一条可执行工作是补齐私密批准包需要的提供方合同证据与精确恢复对象范围；
真实盘点、备份、恢复、部署、付费调用和 QQ 发送仍需各自独立批准。
完整依赖见 [recovery and approval delta](p8c1-recovery-approval-delta.md)。

## 停止、回退与保留材料

遇到 UNKNOWN、DELIVERY_UNCERTAIN、账本不一致、身份或隐私变更时停止新增动作并保留额度和负债。
代码回退必须先证明兼容当前 schema、budget/window journal、遗忘记录及窗口绑定；
不能通过旧 DB、旧 journal 或新 epoch 重新取得额度。
真实恢复使用新隔离目标、最新三类独立记录及一致性 DB/WAL/Spool/QQ 卷，不覆盖在线副本。

公开目标连接字段已移除；已推送历史及外部副本仍可能保留，详见
[public evidence review](p8c1-public-evidence-review.md)。本轮不改写历史。
本轮部分测试缓存清理被自动审批拒绝，原因是 `blocked by policy`，已保留且不换工具重试。
用户附件、原有工作树及受保护诊断材料保持保留。
