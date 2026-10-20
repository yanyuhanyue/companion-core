# P8-B 离线执行记录

本轮基线为 `a33d3cb77f348828b17e4bb48c621c53cfcdc017`，重新 fetch 后本地与远端一致，
#18–#23 均 MERGED，main CI [39400867023](https://github.com/yanyuhanyue/companion-core/actions/runs/39400867023)
五项成功。原工作区只有既有附件目录未跟踪，原路径与所有恢复/诊断材料保留。
P8-A 不重做；B1、B2、B3、B4 各用独立工作树与 `codex/p8b-*` 开发支线。
新 PR 只提交 Draft，不合并或清理分支。完整候选 SHA 与 CI 在最终 PR/私密执行清单单独关联，
本文件不以自己的提交 SHA 自证自己的 CI。

## 离线范围与证据

本轮未连接 VPS、扫码、读取真实 QQ/密钥/DB、复制私人资料、调用模型供应商、
购买快照、操作服务商账号、部署/迁移或启停服务。所有新模型 HTTP 和 OneBot 测试均使用
Mock/Fake/本地隔离夹具，`paid_requests=0`、`new_live_QQ_inputs=0`、`new_live_QQ_sends=0`。
原 runtime 940/schema0015 的信息仅来自 P8-A 历史快照，本轮未重新探测。

| 范围 | 实际命令与结果 |
|---|---|
| 开始基线 | `git fetch --prune origin`、status/branch/worktree/PR 与 main CI 查询，退出0；没有改用户文件 |
| B3 恢复定向 | 下方 Windows 命令：57 passed、0 skipped、2 warnings、59.49s，退出0；包含8个机器证据约束/负例 |
| B1 预算 | 精确 `5cf997a801f1ab3826456b9264dcf67e71f3eba4`：`./scripts/check.ps1 -TestFiles @('tests/test_paid_model_gate.py','tests/test_diary.py','tests/test_release_migration.py')`，118 passed、1既有warning、196.10s；预算62/日记50/迁移6；独立资金矩阵7 passed、14.94s |
| B2 有界发送 | 精确 `1a452d287185e969cff7ecb52a469e218b194d57`：公开窗口集47 passed、92.63s，独立故障集7 passed、16.87s |
| B4 成本基准 | 精确 `1346fa29cb1f63651377dc33525a09fc96cd526a`：基准/M0/affect定向37 passed、1既有warning、89.56s；基准CLI退出0 |
| 完整组合 | 冻结组合 `3e5400d82ea58623118f6f97d84357e35d4135c6`，`./scripts/check.ps1`：895 passed、6 skipped、1既有warning、892.19s，退出0 |
| 显式浏览器补测 | 同一冻结源码，`COMPANION_TEST_BROWSER=1 ./scripts/check.ps1 -TestFiles tests/test_source_browser.py`：4 passed、1既有warning、8.99s，退出0 |
| 冻结组合附加验证 | 同一 `3e5400d`：`uv build --offline`、`scripts/verify_package.py`、`scripts/replay.py`、`scripts/smoke.py`、`scripts/verify_onebot_compose.py` 均退出0 |

```powershell
uv sync --frozen --extra onebot --group reader
./scripts/check.ps1 -TestFiles @('tests/test_p8b_evidence.py','tests/test_release_migration.py','tests/test_source_grant_erasure.py','tests/test_source_grants.py','tests/test_onebot_erasure.py','tests/test_onebot_delivery.py')
```

B3 验证在 Windows/Python3.12.10、冻结62依赖的独立工作树进行，ruff覆盖全部源目录。
两个警告分别为既有 Starlette/httpx 弃用提示，以及本轮 Windows pytest 缓存重命名权限提示；
后者不属于既有警告，测试没有因此失败。只清理了该次检查自己新建的 GUID 临时目录，
未触碰旧审批拒绝清理的材料。

最终组合全量的6项跳过包括默认未开启的4个 Chromium 用例，已以上述显式补测通过；
另2项是本机无法创建符号链接。两次运行都只有既有 Starlette/httpx 弃用提示。
完整组合开始前与结束后163个源码、测试、脚本及冻结依赖文件 SHA256 不变。
最终门禁的8个约束测试另跑通过（0.32s），源码不再改动；不把组合树冒称为任一支线 HEAD。

| 工作包 | 固定候选 / Draft PR | 同 HEAD CI 证明位置 |
|---|---|---|
| B1 | `5cf997a801f1ab3826456b9264dcf67e71f3eba4` / [#24](https://github.com/yanyuhanyue/companion-core/pull/24) | push 39411592236 / PR 39411607122，最终逐项结论在 PR 说明 |
| B2 | `1a452d287185e969cff7ecb52a469e218b194d57` / [#25](https://github.com/yanyuhanyue/companion-core/pull/25) | push 39411621337 / PR 39411634894，最终逐项结论在 PR 说明 |
| B4 | `1346fa29cb1f63651377dc33525a09fc96cd526a` / [#26](https://github.com/yanyuhanyue/companion-core/pull/26) | push 39411647511 / PR 39411661227，最终逐项结论在 PR 说明 |
| B3 | 本文所在支线的最终 PR HEAD | 自身完整 SHA、push/PR run ID 与结论由 PR 元数据/说明及外部执行清单证明，避免自引用提交循环 |

这些是实际创建的 Draft PR 和工作流 ID，不能仅凭 ID 宣称成功。
此文档冻结时各新 CI 尚在执行；最终交付须另外核对准确 head_sha 和每个 job，
结果写入各 PR 说明及私密机器清单，不改写本提交来伪造自身 CI。
`validation.failures` 仅表示最终未解决失败；红例、被替代的中间运行及基准失败样本均保留。

附加验证使用已有冻结依赖与公开 embedding cache，`HF_HUB_OFFLINE=1`，没有下载模型。
wheel/sdist 及无 Git 导出重包排除私密 canary，安装 wheel 初始化迁移通过；回放100事件/30事实，
recall@6=1、公开动作0，实际模型准确率未测。smoke 覆盖 API、chat/diary/life/executive/content 五类 worker、Demo 私密投递及鉴权。
Compose 仅合成配置/build plan 检查，未连接 daemon、读取真实环境或拉起 SnowLuma；
本地 daemon 关闭，不将其记作容器运行通过。

预算与发送独立审查见 [独立安全审查](p8b-independent-review.md)。B3 草案另由非作者检查，
8个机器证据约束测试通过（0.31s），最终报告增量继续接受审查。
中途源码被新缺陷修复替代的全量运行均标记 CANCELLED_SUPERSEDED，不能当作全量通过。
曾有一次组合测试准确阻断遗留 Demo RESERVED 账目：调整的是合成日记测试夹具，
在切换 live Mock 前结算 Demo 记录，未放宽未决账目 guard。

B4 的12样本/11组结果：chat均12次，合成 relevance review 从9次变为2次；
两组必要参考均只保留7/8，试验遗漏间接回忆，基线在压力样本遗漏偏好。
结论 DESIGN_ONLY_NO_PRODUCTION_BENEFIT_PROVEN，保留失败，不实施生产优化；
真实 usage/token/cache/cost/provider latency 全为 null。synthetic_estimate 为 Unicode 字符代理，
与另列的保守 UTF-8 字节上界区分，均不是真实 tokenizer usage；
后台四场景仅为 payload simulation，不能冒称实际 worker 收益。

三个作者子任务清理本轮新建临时/缓存目录时被自动审批拒绝，原因仅为 `blocked by policy`。
目录和诊断已保留，未换工具或路径重试；每次 check.ps1 只清理自己新建的 GUID 目录。

恢复夹具证明的是：历史非空 SQLite 的旧列/FTS/向量/证据不被迁移破坏；最新独立
ErasureLedger 在发布恢复文件前清除已遗忘闭包；source-grant 纠正后继/旧 COMPLETE 模糊归属
阻断生效；无归属旧来源只建清单；还原的活动 Outbox 不重新发送、RESERVED 不变成免费，
恢复 grant 不重新授权未来读取。它没有复制或还原真实五条 QQ 消息、Spool 或登录卷。

## 门禁与申请

[p8b-gates.json](p8b-gates.json) 及 [Schema](../../contracts/p8b-gates.schema.json)
只描述本轮离线证据；原 P7/P8 数值保留为历史。
Schema 负例拒绝无引用的 OFFLINE_PASS、离线阶段真实发信/收费/合并以及伪造生产/E2E提升。
完整公共申请字段见 [p8b-approvals.json](p8b-approvals.json)，
恢复与部署程序见 [p8b-deployment-request.md](p8b-deployment-request.md)。
未确认的负责人、精确时窗、目标故障域、镜像/配置哈希、密文字节与 hash、密钥托管、
供应商能力/费用、RPO/RTO 和预算均为 null/BLOCKED。

历史手机样本仍为5条，各1回复无重复；严格首批0/5、完整0/20，真实 E2E 配对0、P50/P95=null。
非空实机/宿主恢复、真实收费、G1运行、M1/Reader/Diary/Wake/自主/PAD/图索引/DSH/Windows/Android
继续 BLOCKED 或关闭，`production_ready=false`。

## 回退与下一步

本轮代码的撤回范围只在新开发支线，通过正常可审查 revert，不改写 main。
不存在本轮实机部署可回退。未来回退先暂停新增输入与一次发送进程，再按准确批准停止本项目
Bridge→Worker→API；保全 DB/WAL、Spool、稳定 epoch、最新独立 Ledger、费用/窗口账本和配置哈希。
不能降级在线 schema、丢新 Ledger、重发 UNKNOWN、清队列或换 epoch 重置配额。
SnowLuma、原服务、QQ三卷、整机分别申请，不继承本项目停机权限。

后续提示词：基于本轮四个最终 Draft PR 固定 SHA、完整组合测试和同 SHA CI 证据，
先审查 B1/B2 的持久限额及独立审查结论；合并另批，保持所有实机开关关闭。
再分别填实非空恢复/部署/新五次 QQ 窗口/真实供应商费用的准确审批，未单独批准前不执行。
Token 分级加载、记忆图与 PAD 仅为离线 RFC，保留失败样本，不实施生产路由或 schema。
