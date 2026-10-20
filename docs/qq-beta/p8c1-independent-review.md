# P8-C.1 独立审查

本报告记录固定候选的离线审查。C0、slash、C1 兼容性及 C2/C3 设计审查通过；本审查范围没有未解决的已证实 P0/P1/P2。最终产品候选为 `e5bdc075be060e1366a8171f43b84fac408b27a5`，C1 源提交为 `41163a0230503fd7762ec022c748f889ec4fbea9`。最终完整组合检查及 CI 由统筹逐项核验，本报告不预填其成功。

`production_ready=false`；真实 QQ、付费调用、目标部署、非空私人数据恢复、M1 与 Reader G1 验收均未执行。真实严格 QQ 场景仍为 `0/5`、`0/20`，手机 E2E、真实费用、token/cache/latency 均未知。

## 固定范围

| 范围 | 固定提交 | 当前结论 |
| --- | --- | --- |
| 审查起点 | `bbd712c1a79497762275e718e9f76959bedf6583` | 已冻结 |
| C0 原稿 / #30 | `4b791ba7d7bfa2a19aa19f1760ef168c93bd5a46` | C0-01 已复现 |
| C0 修复 / #30 | `e24d8788d4c597f9e938d96d87ed76805504f044` | 独立通过，历史残留保留 |
| slash / #32 | `3e13aa30342b5fb52e9c3f7edfc561384ac5ad1e` | 独立通过 |
| C1 最终源提交 | `41163a0230503fd7762ec022c748f889ec4fbea9` | 五项问题全部闭环 |
| 最终产品组合 | `e5bdc075be060e1366a8171f43b84fac408b27a5` | 独立审查通过；组合检查及 CI 由统筹完成 |
| C2/C3 | `86731cc3f96a752e0bfea1cfa27c2d9d2808440b` | 设计审查通过；付费仍 BLOCKED |
| #31 覆盖补充 | `0c26f2e538c4172865d9e1473c5601cb0972c41d` | 复用已验证证据 |

## C0：公开目标信息

原稿存在 P2 **C0-01**：公开目标连接标识出现在 `docs/qq-beta/p8c-readiness.json:161`、`docs/qq-beta/p8c-readiness.md:45` 及 `contracts/p8c-readiness.schema.json:261`，Schema 还绑定了目标常量。审查仅输出文件、字段和行号，未把原值复制到报告、测试或日志。

修复候选将目标改为匿名 `private_target_ref` 和可空 `target_inventory_sha256`。独立扫描所有新增差异文件，未检出原目标值；新增隐私回归也未复制原值。Schema 可接受多个不同的合法匿名目标引用及有效库存摘要，并拒绝非法引用。

独立 Git blob 比较确认：六份审批请求完全相同；目标之外的 JSON 字段和 Schema 定义完全相同；目标来源、未部署和未知观测字段保持相同；v2 目录六个文件逐字节相同。没有借脱敏删除审批合同或实验反例。

在修复的精确 SHA 上独立运行：

```powershell
./scripts/check.ps1 -TestFiles tests/test_p8c_public_materials.py,tests/test_p8c_readiness.py
```

结果：Ruff 通过，**20 passed in 1.70s**；额外 Schema 探针三例合法、四例非法均符合预期。脚本自有临时验证目录已清理。

同一精确 SHA 的 [push CI 39431799166](https://github.com/yanyuhanyue/companion-core/actions/runs/39431799166) 与 [PR CI 39431806367](https://github.com/yanyuhanyue/companion-core/actions/runs/39431806367) 均逐项核实：preflight、Linux、Windows、container、browser-reader、plugin-native、ci 各自成功。复查 PR HEAD 未漂移。

**C0-01 在修复 SHA 上关闭。** 原提交、历史 diff、既有通知、克隆或 artifact 仍可能保留原信息；最新文件修复不等于历史清除。未重写历史，未更改主机或网络配置。现有证据不能推导凭据失窃或主机入侵；进一步访问控制或历史处理由仓库所有者单独决定。

## #31 既有证据

沿用 `0c26f2e538c4172865d9e1473c5601cb0972c41d` 的已验证三文件差异和 [独立报告](https://github.com/yanyuhanyue/companion-core/blob/0c26f2e538c4172865d9e1473c5601cb0972c41d/docs/qq-beta/p8b1-independent-review.md)，没有重做 Reader/Worker 回归。固定 [PR CI 39425616690](https://github.com/yanyuhanyue/companion-core/actions/runs/39425616690) 与 [push CI 39425606792](https://github.com/yanyuhanyue/companion-core/actions/runs/39425606792) 各七项成功。

受限 Reader 容器的成功夹具读取已验证：非 root、只读文件系统、无网络，`fixture_reads=1`、`fixture_requests=2`、`restricted_rejections=3`、`upstream_requests=0`。该记录属于离线合成夹具，不是已读取真实账号内容的证据。

## slash：显式命令与历史遗忘兼容

完整 23 文件差异及相关调用点已审查。整体消息在首尾空白处理后必须以 ASCII `/` 开头，分派按精确命令名识别；Core 提前暂停、命令分派、Diary、Life 及敏感读回均复用同一解析。裸文本、文本引用、行内 slash 和相似前缀不执行控制动作。已标记的历史旧命令仍参与遗忘来源解析，不作为新命令激活条件。

另增 **10 项独立合成探针，10 passed, 1 warning in 16.42s**，没有重跑作者的 262 项测试：

- 八类敏感 slash 读回在实际读取后推进隐私 epoch，全部阻断，0 Outbox、0 模型调用。
- 提前暂停发生在 worker 前，取消实际合成 pending activity job/Outbox 和 RUNNING diary run，并清空 diary lease。
- 新冒号 slash 日记读取/修订与合成旧中文 marked 命令，经 source-grant forget 及遗忘前备份恢复、最新 Ledger 回放后，相关事件与读回 Outbox 均已清除。

warning 为现有 Starlette/FastAPI 测试客户端的 httpx 弃用提示。测试启动时固定候选 HEAD，并核实实际 import 源目录，避免错误测试基线源码。作者树保持干净，HEAD 未漂移。

同 SHA 的 [push CI 39432135157](https://github.com/yanyuhanyue/companion-core/actions/runs/39432135157) 与 [PR CI 39432145374](https://github.com/yanyuhanyue/companion-core/actions/runs/39432145374) 各七项成功。未确认新 P0/P1/P2 缺陷。

原生 QQ 回复/转发的 quote 语义并非始终保留在 Core HTTP v1 中；本结论覆盖已传入文本的识别，不声称真实平台全部引用形式已经验收。本轮仅清理独立 slash 合成测试临时目录的请求被自动审批审查拒绝，返回 `blocked by policy`；合成临时数据保留，未重试。

## C1：五项问题闭环

C1 审查以完整合成成功链路和失败矩阵共同作为验收条件。预审复现的两项问题先在 `7fc3912d79fbb6844999e0d8e22acdfe3cf5d36e` 通过修复验证；后续定向检查确认的一个 P1、两个 P2，也已分别在 eedd 和最终产品组合闭环：

| 编号 / 级别 | 触发及影响 | 状态与证据 |
| --- | --- | --- |
| C1-PRE-01 / P2 | DNS pin 后完整 URL 比较误拒绝合法主机名 | 在 7fc 修复验证通过：真实 pin 成功；DNS 异步等待跨截止则 0 send，保留 UNKNOWN 尝试 |
| C1-PRE-02 / P1 | 本地停止后，已排队 Core 作业仍能启动模型调用 | 在 7fc 修复验证通过：CLI 获得 Core 持久停止确认后 queued worker 为 0 模型调用；模型 preflight 中停止也为 0 dispatch，费用保留 UNKNOWN |
| C1-03 / P2 | `local_synthetic` 接受 DNS 固定到非 loopback 私网地址，原红例有 3 个协议 probe 和 1 次发送 | 在 eedd 修复验证通过：Core open 与协议 probe 前均拒绝，全部非 loopback transport dispatch 为 0；完整 loopback 成功链保留 |
| C1-04 / P2 | CLI 无法联系 Core 时只显示通用 STOPPED，未明确保留停止未确认状态 | 在 eedd 修复验证通过：实际 CLI 和重开 Spool 后的状态均区分 confirmed / unconfirmed；确认记录持久化到 Window Journal |
| C1-05 / P1 | 模型最终预检在同步 journal 校验前采样时间，校验完成后跨截止仍沿用旧时间 | 在源 411、最终产品 e5bd 修复验证通过：最终纯检查在所有阻塞工作后采样时间，截止后 0 dispatch，1 admission 和 UNKNOWN 预留均保留、0 Outbox |

精确 7fc 独立增量轮为 **5 passed、2 failed in 12.97s**；源 HEAD、import 路径和运行文件指纹均核实。首次因 pytest basetemp 父目录缺失产生 setup errors，补齐本轮目录后重跑，未将环境错误算作产品缺陷。为确定 C1-03 是否也影响协议 probes，仅重跑该例并增加 transport 分类计数，**1 个预期失败 in 3.44s**；输出未包含目标地址。

C1-03 必须在合成模式的全部实际目的地址上落实 loopback 限制，覆盖 Core 请求、协议 probes 和最终发送；只在最终 send 校验中阻断仍不足。C1-04 的停止边界可以是 Core 持久确认，不要求网络分区中不可能的瞬时撤回；要求的是失败时如实保存和呈现未确认状态，已开始的模型调用继续保留费用和 UNKNOWN。

C1-05 通过包装实际 journal 校验，在其返回时推进合成时钟至 `plan.end` 复现，**1 个预期失败 in 3.57s**。模型最终 dispatch 前应在所有阻塞校验之后重读期限和 lease；不能仅在之前的锁获取或请求 hook 开始时采样时间。该单例是在发现明确的剩余时序疑点后新增，没有扩跑已通过套件。

精确 eedd 的独立增量为 **13 passed, 1 deselected in 21.20s**，未报告 warning；被排除的正是已知未修的 C1-05。HEAD、实际 import 路径及运行文件摘要均已核实。除 03/04 闭环，还验证：Budget Journal 已 append、DB 停止标记尚未写入时模拟崩溃，或恢复停止前的真实合成 SQLite 快照配最新 journal，均无法重开或新增模型调用；重复停止仍可确认；已进入 Mock transport 的调用在停止后可按有效 usage 结算，超时则保留 UNKNOWN，金额和一次 admission 不丢失、0 Outbox。非显式 MockTransport 的合成模型传输在 dispatch 前拒绝。

最后在精确产品 `e5bdc075be060e1366a8171f43b84fac408b27a5` 仅重跑 C1-05 独立红例：**1 passed in 3.05s**，未报告 warning。实际 journal 最终校验后推进时钟至窗口截止，模型 dispatch 为 0；一次 admission、`io_started` 和等于预留上界的 UNKNOWN 金额完整保留，0 Outbox。没有重跑已通过的 13 项。

源 eedd→411 的新增最终 `dispatch_fence` 在配置摘要、journal/DB/源码验证和 lease 解析后才读取时钟；时钟采样后没有 DB、文件、锁或 await。它同时检查 live/paid/费率期限及 job/admission lease。`check_model` 重新读取当前 job 状态及 lease token；组合与源 411 的 budget/live_text blob 完全相同。

新停止控制记录使用原独立 Budget Journal；没有新的财务队列。它永久阻断新准入，同时保留原 `allow_settlement` 收口路径。旧版本降级或新窗口轮换须另审，不能删链尾或用旧库恢复调用许可。

所有复现均无真实 DNS、HTTP、QQ 或付费 I/O。原预审 `window.py` SHA-256 为 `ac3563b730a0859c7d666e0d21f0678f41134a7876983592b6ef5ba152f0cc9b`，停止复现的 `live_text.py` SHA-256 为 `a2b5f9757ff25807eee6e00facf37e6596420e279f3ffc54a3b72bfe9f12185a`。这些指纹固定原始红例，不能代替最终提交验收。

## C2/C3：设计通过，真实准入继续阻断

准确候选 `86731cc3f96a752e0bfea1cfa27c2d9d2808440b` 的父提交为已审 C0 修复，只新增五个合同、报告及测试文件，无预算或配置实现变更。相对初审材料的增量均已核对，作者树保持干净；未确认 P0/P1/P2。

供应商结论保持 `PROVIDER_CONTRACT_UNVERIFIABLE`。当前 [token 用量说明](https://api-docs.deepseek.com/quick_start/token_usage/)、[Chat Completions 参数](https://api-docs.deepseek.com/api/create-chat-completion/) 及 [官方 V4.1 encoding 参考](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/README.md) 不能将本项目的 ChatML/tokenizer/structured suffix 绑定为托管服务的精确收费合同。[余额文档](https://api-docs.deepseek.com/api/get-user-balance/) 的 CNY/USD 与 [账户隔离说明](https://api-docs.deepseek.com/quick_start/rate_limit/) 也不能替代具体账户币种、外部原子封顶及全部入口证据。官方依据已只读核验，未请求模型、余额或其他账户 API。

两种替代方案只通过条件性设计审查：

- A：由供应商执行与准确 payload、身份、币种和全部收费项绑定的一次性金额上界；当前未证明这种服务存在。
- B：在供应商证明全部收费输入、输出及有效最高费率后，按完整上下文的保守上界预留；公开上下文标签和 `max_tokens` 不足以满足这些前提。

两者都保留最新独立 journal、账户全部入口和唯一准入、provider hard cap、每次调用前预留、UNKNOWN、次数和并发限制，以及原操作者每请求 token/数据范围。金额上界不等于精确 token 证明，不授权扩大输入；方案不能适配原限制时继续拒绝。它们不能填写当前 `PaidModelApproval`，须经新实现及独立审查后另行申请。

C3 保留旧实验目标与新目标的独立匿名身份；恢复集合含 Core/WAL、最新 ErasureLedger、所有相关 Budget custody、Window Journal/Spool/epoch、QQ 三卷及获准原服务。未虚构 Budget Journal 原生序号，六项批准分别保持 BLOCKED/DISABLED，实际费用、token、缓存、延迟和手机 E2E 均保留未知。

独立运行精确候选的 `check.ps1`，选择新 gates 测试及公开材料隐私检查：Ruff 通过，**10 passed in 0.48s**。未重跑原六项合同全套，脚本自有临时目录已清理。该五文件提交为本地交付，不声称独立远程 CI；最终组合精确 SHA 的 CI 由统筹完成。

## 集成冲突增量

精确合并提交 `df4f65c633a4c04d5272480d974b35a3ad2d5f89` 的 `companion/core.py` 冲突解决已独立核对：保留 slash 解析、diary/control 分类及两处提前暂停中的 `not bounded_text`，并保留 bounded job 跳过命令分派及敏感读回 epoch 重核。

只选择新增 `tests/test_live_text_commands.py` 运行 `check.ps1`，Ruff 通过，**6 passed in 9.07s**；未重复统筹的 73 项。裸“暂停写日记 / 暂停主动分享 / 记住这个”走普通 live Mock 回复且无控制副作用；带 slash 的三例在入队前拒绝，0 模型请求、0 发送。该轮仅验证合并交界；C1-03/04 另由后续 eedd 增量闭环。

最终产品逐文件来源比对：**33 个文件一致，全部 17 个运行源码差异均有已审来源**。Core 合并结果及六例测试与 df4 blob 相同；C1 源与 411 相同；slash 与 3e13、Reader/Worker 与 #31、C0 与 e24、C2/C3 与 867 对应文件相同。唯一额外测试准备差异来自 `fb12dd3136a5269bb8a367cbe70c6c298424b66a`：在离线兼容 READY 的负例中先明确清空候选/证据两个字段，使后续证据填写不把负例变成合法正例；已独立检查该两行差异，未改运行合同。

已通过检查只在源码无关或 blob 相同的范围复用。最终完整检查、Linux/Windows、容器、浏览器、原生插件和聚合 `ci` 的精确 HEAD 结果须由统筹在组合交付记录补齐；本报告不把历史 CI 当成最终组合 CI。

最终报告在精确产品 e5bd 上通过公开材料匿名目标回归：Ruff 通过，**1 passed in 0.20s**，覆盖本报告及现有 P8-C 公开材料；脚本自有临时目录已清理。

清理本轮六个 C1 合成测试临时目录的请求被自动审批审查拒绝，理由仅为 `blocked by policy`；这些目录与此前被拒绝的 slash 合成目录均保留，未重试或改用其他方式清理。独立探针、运行收据和来源比对证据保留在本地审查证据目录。

最终集成及全量精确 SHA 验证由统筹完成；本报告不自行合并或删除任何分支。
