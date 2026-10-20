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
| B1 预算 | 候选完成后由其独立报告和固定 SHA CI 记录，不预填 PASS |
| B2 有界发送 | 候选完成后由其独立报告和固定 SHA CI 记录，不预填 PASS |
| B4 成本基准 | 候选完成后由其独立报告和固定 SHA CI 记录，不预填收益 |
| 完整组合 | 待本地组合验证；不是 main 已合并或在线已升级 |

```powershell
uv sync --frozen --extra onebot --group reader
./scripts/check.ps1 -TestFiles @('tests/test_p8b_evidence.py','tests/test_release_migration.py','tests/test_source_grant_erasure.py','tests/test_source_grants.py','tests/test_onebot_erasure.py','tests/test_onebot_delivery.py')
```

B3 验证在 Windows/Python3.12.10、冻结62依赖的独立工作树进行，ruff覆盖全部源目录。
两个警告分别为既有 Starlette/httpx 弃用提示，以及本轮 Windows pytest 缓存重命名权限提示；
后者不属于既有警告，测试没有因此失败。只清理了该次检查自己新建的 GUID 临时目录，
未触碰旧审批拒绝清理的材料。

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
