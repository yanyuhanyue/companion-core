# P8-C 六项独立实机准入包

本页保留 P8-C 时点的审批草案；当前模型费用依据 [P10 用量与费用观测](p10-provider-evidence-delta.md)，当前真实操作范围见 [P10 对象与签收](p10-r1-r2-signoff.md)。

本包仅准备可填写的审批与验收材料，证据等级 `OFFLINE_PROPOSAL`。
[机器清单](p8c-readiness.json)新增六个独立条目，不改变
[P8-B 旧审批格式](p8b-approvals.json)或旧门禁 Schema，也不被运行程序读取为授权。
每项须由主人分别签署准确的私密记录；填写本表、合并文档、CI 通过均不授予执行权限。
当前恢复、部署、QQ、付费为 `BLOCKED`，M1、Reader G1 为 `DISABLED`，`production_ready=false`。

## 版本与证据边界

2026-10-20 20:40（Asia/Shanghai，12:40 UTC）完成 GitHub 与两份协调状态的核对：
main 为 `bbd712c1a79497762275e718e9f76959bedf6583`，
[CI 39422750301](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301)
7/7 成功，包括 Linux、Windows、browser-reader、plugin-native、container 五类检查。
#24–#29 均已由集成负责人合并；#24–#27 原始 HEAD 依次是
`5cf997a801f1ab3826456b9264dcf67e71f3eba4`、
`1a452d287185e969cff7ecb52a469e218b194d57`、
`1346fa29cb1f63651377dc33525a09fc96cd526a`、
`692da26b0779e2ecd8130c199523ec897e0fde4e`。

[集成 PR #29](https://github.com/yanyuhanyue/companion-core/pull/29)
已合并，源 HEAD `39de04edce1fc392c9fa6d9c6a734b46ff78aaee`，
[push CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39422068330) 与
[PR CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39422081781) 均 7/7 成功。
其 tree 与上述 main 均为 `6f60b1b82e2e2d439192f7b08f6aa73c9498b75e`，已用 Git 核对。
本文实际核对该 tree 的源码合同，代码迁移 head 为 `0017_paid_model_gate`。
集成与独立 A1/A3 状态均已 done。独立报告结论为 `PASS_WITH_COVERAGE_LIMITS`，
审查最终 main `bbd712c1a79497762275e718e9f76959bedf6583`，未发现未解决的 P0/P1/P2。
报告提交 `e5026d816b70c2b169f53ca1268b43c5e943dd27` 由统筹保管，尚无公开 PR 链接。
覆盖限制仍包括仅离线合成、本机 Docker 未运行（核对精确 SHA 容器 CI，未新增容器内成功 HTTP fixture 全流程），
以及回归补充 `2ac4a154f50307d72d56769b4350c6655b267e92` 尚未入 main、只有本地证据。
机器快照的源码候选 `candidate_sha=39de04edce1fc392c9fa6d9c6a734b46ff78aaee`
与集成负责人已完成记录一致；它不是部署许可。P8-C 六份请求尚未冻结部署批准，
各自 `candidate_sha`/批准 head 字段仍为 null，须绑定最终获批对象。
执行前须重新对齐 main、最终独立审查与六份批准记录，不能用相同 tree 推定不同 HEAD 的全部检查。

已合入历史中的模块边界原 HEAD 为 `a316c79240f34b70e0539b70f42c3cef48ebcfb6`
与 `0fadc971d0d06bd7c68e05ab1e65e46ecd493554`。后者
[CI 39417572124](https://github.com/yanyuhanyue/companion-core/actions/runs/39417572124)
五项成功。集成负责人已清理这些源分支，提交历史保留；本包不替代 A1/A3 的非作者审查。

| 对象 | 可据以填写的事实 | 尚缺的实测 |
|---|---|---|
| 旧测试主机 | [P7/P8 历史记录](p8-host-recovery-review.md)：源码 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`、Demo、`P7-DEMO-1`、schema `0015_article_images`，五条真人输入/五条 SENT | 五条消息后的真实非空恢复、QQ 三卷/Spool/原服务/整机恢复均未证实；地址不从历史推猜 |
| 最终目标 | 仅以 `private_target_ref=private_target_final` 引用受控私密目标清单；用户交接尚未由本轮探测确认，Core 尚未部署 | `target_inventory_sha256=null`；实际目录、挂载、服务清单、配置、镜像、DB/schema、备份和延迟待独立批准后填写 |
| Windows 工作站 | 已配置的范围只有 `windows.read_text/windows.write_text`；Core 未启用该能力 | 截图、键鼠、应用启动未接入；链路探针不证明桌面端到端性能 |
| 历史 QQ 与 E2E | 旧主机 Demo 样本 5/5；严格 0/5、完整 0/20；手机成对 E2E `n=0,p50/p95=null` | 最终目标的新严格窗口和手机证据均无；旧样本不得迁移归属到新服务器 |

最终目标事实来自本轮人工交接，未由公开 CI 或本次主机观察验证。
所有真实路径、QQ 身份、密钥、登录资料、正文和账单保留在获准的私密记录；公开表只记引用、计数和哈希。
原参考对话的最后两条正文没有返回，本包依据另附任务书与仓库源码，不声称已读取那两条正文。

## ① 真实非空数据与主机恢复（BLOCKED）

填写机器清单 `requests.recovery`，批准必须逐项界定源、全新隔离目标、读取/复制范围、
操作者、时窗、费用、密钥托管、RPO/RTO、最大停机和原服务负责人。
不同来源主机应分别列出；若计划把旧状态迁到最终服务器，数据迁移与身份/会话使用范围须明确列入，不能由部署许可推定。

| 必须形成的一致性集合 | 备份/校验内容 | 当前未知字段 |
|---|---|---|
| Core 非空状态 | SQLite backup API 保留已提交 WAL；Outbox、model_usage、model_admissions、model_budget_windows、来源归属及 schema；另记录 DB/WAL/SHM 清单及停写边界。活动库不得以单文件盲拷代替一致快照 | 私密源引用、实际行数/字节、密文 hash、schema、时间点 |
| 最新独立 ErasureLedger | 原 ledger_id、实际末 `seq`、文件 hash、最后记录 hash、截止时间、权限/独立托管；恢复前先验证身份与序列，回放删除闭包 | ledger_id、末 seq、hash、托管/故障域 |
| 最新独立预算 journal | 所有有效/已撤销/已轮换批准所引用的原账本；journal_id、完整文件 hash、记录条数及链尾；与 DB 中每次费用/状态/I/O fence 对账 | journal_id、末记录序位、hash、chain head、独立故障域；该格式没有原生 seq，不伪造 |
| Spool、稳定 epoch 与窗口 journal | Spool DB 的已提交 WAL、待确认入件/回执、window_attempts/test_window、固定 epoch；每个窗口 journal 的真实末 seq、链尾，与 Spool 原额对账 | Spool/epoch hash、未确认数、window_id、末 seq、链尾/故障域 |
| SnowLuma 三卷 | `snowluma-data`、`snowluma-qq-config`、`snowluma-qq-data` 的私密实际挂载、权限/版本；另批一致性停机与会话数据复制 | 每卷密文 hash/大小、停机许可、离线恢复结果；不包含扫码/重新登录许可 |
| 原服务与宿主 | 1Panel/OpenResty/redroid 的独立一致性导出、定义/权限/依赖；如申请整机快照，核实本实例全部磁盘覆盖、恢复入口和费用 | 每个服务/磁盘范围、导出策略、停机许可、快照能力/ID/价格、实际验证 |

这里的“集合”是经停写证明同一截止边界的清单，不承诺跨 DB/文件天然原子。
ErasureLedger 当前是身份+连续 `seq` 格式，并非预算 journal 的哈希链；最后记录 hash 是本次清单需另算的校验值。
窗口 journal 有 `seq` 和 `previous`，预算 journal 只有链关系及头部 journal_id。
三个副本的最新性都须由独立托管记录证明，仅有本地 hash 不能检测所有副本一起回滚。

获批后的验收顺序：

1. 先核对源/目标私密清单，证明目标是全新路径/卷/主机；关闭所有消费进程，默认禁止 QQ、模型、社交及站点业务出站。
   提交经验证的网络隔离证据，应用开关本身不足以证明隔离；不得误挂在线卷。
2. 在明确批准的时窗停止新增输入与受控发送，再停止 Bridge→Worker→API 及其他获准写入者。
   一次发送进程的停止入口与普通 Bridge 分开固定；SnowLuma/原服务需分别批准，不能借用本项目停机许可。
3. 停写后形成上述完整集合，记录 PENDING/LEASED/SENDING/DELIVERY_UNCERTAIN、RESERVED/UNKNOWN、Spool 未确认数和费用/窗口剩余额度。
   使用现有 `backup`；它仅覆盖 Core DB，不会自动打包其余对象。密文与密钥分离，并验证独立密钥恢复。
4. 仅用现有 `restore` 恢复到新隔离目标；该函数在发布文件前迁移、验证 ledger_id、回放最新 ErasureLedger、检查 integrity/FK。
   恢复代码会迁移到其自身 `head`，因此必须预先固定代码/镜像并验证其 head 正是获批 head。
   预算/窗口 journal 与 Spool 的独立对账仍需单独进行，不能把 Core restore 成功当作三个账本均已恢复。
5. 验证活动 Outbox 转为 `DELIVERY_UNCERTAIN`，SENT/CANCELLED 不回待发；model_usage RESERVED 转 UNKNOWN，费用保留。
   有 paid admission 的恢复副本置 `model_budget_restore_required=1`，不能恢复付费执行。
   既存 UNKNOWN 继续保留；来源 grant 被撤销、设备/媒体会话被关闭，内容/探索未完成项进入 UNKNOWN，不自动恢复权限或重放任务。
6. 用旧备份+最新 ErasureLedger 验证纠正后继/派生资料删除；归属不明的旧 COMPLETE 保留
   `SOURCE_GRANT_LEGACY_ERASURE_REVIEW_REQUIRED`。未获核对不得猜归属或写新 Ledger 掩盖缺口。
   单独回滚任一 DB/账本必须阻断；全部副本同时丢失/回滚无法由本地证明排除，须保留阻断并核对独立托管/供应商账单。
7. 分别记录 Core、Spool、QQ 卷、原服务和整机的实际还原范围/结果、出站计数、耗时/RPO/RTO、费用和签收。
   初始空 Core、15/57 项合成测试均不能填写真实非空/整机 PASS。

## ② 固定候选部署与迁移（BLOCKED）

填写 `requests.deployment`：最终完整 SHA、Core/Bridge/SnowLuma 的 `image@sha256`、
配置版本/hash、Compose/挂载/网络清单 hash、目标身份、迁移 head、最新三账本清单、
已验证的真实非空隔离恢复引用、停机与兼容回退记录。部署批准不包含复制历史 QQ 卷或启用其他五项。

本包核对的 [0017](https://github.com/yanyuhanyue/companion-core/blob/39de04edce1fc392c9fa6d9c6a734b46ff78aaee/migrations/versions/0017_paid_model_gate.py)
以 `0016_source_grants` 为父，新增 model_budget_windows/model_admissions 及索引，保留既有使用记录。
0015→0016→0017 的链必须用最终获批发布包在新空库和历史非空合成库验证，再在获批隔离恢复副本检查。
最终服务器尚未部署 Core；旧测试主机的 0015 不是最终服务器的 schema 观测。

部署默认 Demo、delivery/memory/reader/diary/wake/autonomy/设备/社交关闭。
只准使用已固定并通过同 HEAD 集成检查的候选。新 schema 的 downgrade 在已有 paid admissions 时明确拒绝；
即使无 admissions，也不能将技术上可执行的降级理解为批准的数据回退。
历史 Core digest `sha256:5eb8045c47f62f68202e164bb3fcb7f93be4633c31a15d55047743bc436b7891`
仅来自 [旧发布记录](p7-release-review.md)，没有证明兼容 0017 与最新账本，不能填入兼容回退目标。

## ③ 新 QQ 严格发送窗口（BLOCKED）

填写 `requests.qq_window`：唯一 owner/bot 私密校验及 hash、固定 epoch、目标候选/镜像/配置、
新 window_id、准确 `+08:00` 开始/截止、匿名输入/输出白名单、初始 PENDING/uncertain/Spool 状态、
窗口账本的原配额、操作者和停止人。上限为 **5 个新输入、5 次外部尝试、并发 1、每个输入最多一条回复**；
许可可以更小，不能借重启/换 epoch/恢复旧 Spool 重置。旧五次许可已用完，历史 Demo 5/5 不覆盖新窗口。

对齐候选的 [window 合同](https://github.com/yanyuhanyue/companion-core/blob/39de04edce1fc392c9fa6d9c6a734b46ff78aaee/companion_onebot_bridge/window.py)：
I/O 前持久预留，DNS preflight 后、最终 I/O 边界再查时窗和 fence；过期/身份变化/未知发送立即停。
**当前候选的有界 QQ 入口硬性要求 Demo 且全部功能关闭，`paid_requests=0`。**
取得④并重签窗口仍不能使真实模型/M1/Reader 联用生效，现有入口会以
`DEMO_CLOSED_FEATURES_REQUIRED` 拒绝。该组合继续 BLOCKED；先另行实现并审查兼容的有界入口与交叉回归，
再申请相应能力与准确窗口，不能仅修改审批 JSON 或绕过现有检查。
只开启有界一次发送入口，禁止以常驻 `delivery_enabled=true` 代替窗口。

验收使用现有 [严格真人场景](p8-human-acceptance.md)，仅在上限内选定首批场景。
真实同 ID 重复仅自然发生才记 LIVE，不造危险重复。每条手机端记录匿名 trace、发出时间、可见回复时间、
时钟来源/误差和配对规则；缺任一端不纳入 E2E。当前新目标严格样本 `0/5,0/20`、
手机配对 `n=0,p50/p95=null`。ACK、服务端耗时、Tailscale 探针均不代替手机 E2E。

## ④ 真实付费模型（BLOCKED）

填写 `requests.paid_model`，逐项核实 provider/account/model、最终 HTTPS endpoint、区域/条款、
实际费率来源及有效期、输入/缓存/输出价格和币种、税费/舍入、密钥托管与轮换、tokenizer artifact hash、
提供方真实 framing/结构化输出边界、硬输入/输出、单尝试/逻辑请求/日/月/窗口总费用与次数、
总并发、唯一 DB/账户入口、独立 journal 故障域、账户级封顶、核账/停止人。

当前 [PaidModelApproval](https://github.com/yanyuhanyue/companion-core/blob/39de04edce1fc392c9fa6d9c6a734b46ff78aaee/companion/config.py)
只支持 `USD`、`chatml-v1` 的受控文本合同；“可填写 currency”不代表支持其他币种。
真实 provider/tokenizer/framing 尚未证实，所有实际值保留 null；不能拿测试非零费率或默认模型充数。
工具/多模态输入不在该文本付费合同内，生图/视觉/语音不得借此启用。

[预算实现](https://github.com/yanyuhanyue/companion-core/blob/39de04edce1fc392c9fa6d9c6a734b46ff78aaee/companion/paid_budget.py)
在 SQLite 事务内预留，账本 fsync 后提交；锁等待/输入证明后及 HTTP 前重验，UNKNOWN 不释放金额或并发。
单个逻辑请求包含 recall review/chat 等可能多次模型尝试，每次均独立计费；QQ 一条回复不等于一次模型费。
禁用/轮换批准后仍保留原 journal custody 和晚到费用。软件上界估算不是供应商账单绝对封顶，
账户被其他 DB/Key/网关共用且无统一控制时仍 BLOCKED。异常 usage/价格/币种停止新增调用并核账，不能把 UNKNOWN 当免费重试。

## ⑤ M1 基础记忆（DISABLED）

填写 `requests.m1`：明确获准真实证据集/主体/保留期、候选与配置、首批写入/检索数上限、
主人确认/纠正/拒绝机制、敏感内容边界、停止人与删除验收；如涉及付费/QQ，分别依赖④/③。
不得把 M1 批准等同于 Reader、日记、自主、设备或关系图的启用。

首批验收：一条获准真实事件→候选→主人确认→精确检索；未确认与来源文本不能升格事实；
同一来源纠正保留 supersedes/provenance，检索优先有效后继；敏感/冲突候选拒绝或待确认；
遗忘后直接/向量/派生视图均不再出现；用旧备份+最新 Ledger 证明不复活，保留合法独立事实。
每一步只记脱敏计数和证据引用。SQLite 与现有 embedding 是事实来源，未来设计见独立 RFC。
M0/M1 不执行 DSH、Android、Windows MCP、QZone 或公开评论。

## ⑥ Reader G1 真实账号读取（DISABLED）

填写 `requests.reader_g1`：准确来源/账号私密归属、只读范围、grant_id/期限、私密登录卷范围、
历史内容清单、请求次数/字节/时长/费用上限、撤销/遗忘/停止人和隔离部署证明。
公开匿名 fixture 通过不证明真实账号、内容读取或 G1 实机成功；不随部署自动登录。

验收要求每次 GET 前与结果提交前重验 grant/隐私，撤销与到期后零新读取、在途结果不提交；
撤销未来读取（revoke）只取消后续读取，不自动删除合法历史；必须另有准确的遗忘范围批准，
再用 forget 覆盖来源归属、正文/图片 observation、纠正后继和派生记忆/日记/索引，并以最新 Ledger 重放。
旧 COMPLETE 归属模糊仍阻断。Reader 镜像不可持有 Core DB、模型 Key、Outbox、Docker socket；
保持非 root/只读根/受限网络。SSRF、规范 URL/CDN、注入文本仅作数据的离线红绿证据由 A1/A3 提供。
需要真实付费深读/视觉时再独立满足相应能力与④，当前文本模型审批不覆盖媒体。

## 停止与兼容回退

六份记录均须指定停止人、准确入口与影响范围。QQ 使用固定候选的 `window-stop`（经批准私密 Bridge 配置）；
它不做网络 I/O，但执行命令前仍需校验准确 Spool/窗口。停止有界发送进程，再按批准范围停止 Bridge→Worker→API；
SnowLuma 与原服务单独处理。停止不能撤回已经在途的 HTTP/QQ 请求，未知状态交由主人核对，迟到回执不得推翻人工 SENT/CANCELLED。

目标/身份错、意外业务出站、最新账本缺失/不匹配、UNKNOWN 被清零/自动重试、额度失真、旧归属未定、
付费/账号边界变化、原服务异常时立即停止扩大动作，保存源和诊断。
保留 DB/WAL、Spool、稳定 epoch、最新三账本、配置和密钥版本引用；不 down -v、不 prune、不覆盖在线库。
回退只允许已经证明兼容当前 schema、Ledger 和费用/窗口状态的固定镜像；未知兼容性时保持停止，在新隔离目标恢复，
不以旧镜像+旧 DB/账本换取新额度。任何重新启用都需在原批准的准确范围内复核，范围变化另行签署。

## 交付验证与下一轮使用

本轮只修改文档、离线数据/基准及清单验证；检查结果和最终来源快照记录在
[机器清单](p8c-readiness.json)与 PR。全量运行包/迁移/跨平台 CI 由集成负责人统一执行，
本文不替其宣告通过。所有实机字段未知时保持 null，不把离线样本/合成计数写进真实门禁。

未来功能按 [独立 RFC](../design-future/p8b1-future-rfc.md) 与
[依赖/验收 backlog](../design-future/p8b1-backlog.md) 推进，
[30 例合成合同](../experiments/p8b1-context-v2/README.md) 保留原 7/8 漏例，
不声称实际 provider tokens/cache/费用或手机延迟改善。

本轮定向 `./scripts/check.ps1` 完成 Ruff 与 34 项检查（新清单、旧门禁、原 #26 基准）；
补强来源快照 Schema 后仅重跑受影响清单 17 项，全部通过。
最终清单+新合成合同合跑 25 项通过（3.29s），独立 docs 脚本 Ruff 通过；
冻结 `results-final.json` 与实际重算完全一致，原 #26 fixture 未改。
文档本地链接检查通过，独立 A4 文档复核的 revoke/forget 与 Demo-only 入口问题已修正文案。
本轮临时测试目录清理曾被自动审批拒绝（`blocked by policy`）；保留该批本轮目录，未绕过拒绝重试。

下一轮提示词：读取本文件及 JSON，重新固定最终 main/完整候选 SHA 与同 HEAD 集成/独立审查证据；
先由主人逐项签署六份私密记录并填齐源/目标、三镜像摘要、配置/迁移、最新三账本、停止/回退与额度。
按依赖先在新隔离目标验证真实非空完整恢复，再执行准确获批部署；QQ/付费/M1/Reader 分开验收。
缺字段或缺授权保持原门禁；公开报告只保留哈希、匿名计数和证据引用。不要将本提示词解释为上述实机操作许可。
