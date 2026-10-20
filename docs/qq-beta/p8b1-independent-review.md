# P8-B.1 A1 / A3 独立审查

结论：**PASS_WITH_COVERAGE_LIMITS**。固定范围内没有未解决的可操作 P0/P1/P2；
接受 R1/R2 已有修复。没有产品源码修复，也没有运行权限变更。
新增一条此前缺少直接覆盖的 Worker 重启回归，已实际通过。
本报告不把合成测试提升为实机、账单或生产安全证明；`production_ready=false`。

## 1. 时间、范围与审查独立性

- 记录起点：2026-10-20 12:26:09 UTC / 20:26:09 Asia/Shanghai（状态文件建立；此前读取任务书、技能和 Git 范围）。
- 取证结束：2026-10-20 12:39:19 UTC / 20:39:19 Asia/Shanghai。
- 工作树：`E:/Companion Core/.local/worktrees/p8b1-independent-review`；分支 `codex/p8b1-independent-review`。
- 原 main：`a33d3cb77f348828b17e4bb48c621c53cfcdc017`。
- 深审冻结组合：`0fadc971d0d06bd7c68e05ab1e65e46ecd493554`，比较 `a33d3cb...0fadc971` 的原始 diff 和调用路径；
  结构增量另比较 `3e5400d82ea58623118f6f97d84357e35d4135c6..0fadc971`。
- R1/R2 红绿：`2c24e769893ec708b6a7967f4504fa9b3af42582..a316c79240f34b70e0539b70f42c3cef48ebcfb6`，
  以及 `595dfc385515b138972d4c45b507d822530ccaf6..0fadc971`。
- 原 B1/B2/B4/B3 分别固定下表四个 SHA；预算、发送、基准和审批材料的对应文件与冻结组合逐文件比较。
- 最终集成头：`39de04edce1fc392c9fa6d9c6a734b46ff78aaee`；[PR #29](https://github.com/yanyuhanyue/companion-core/pull/29)
  由其他已获授权的聊天于 12:30:11 UTC 合并。最终 main / 本报告 `reviewed_sha`：
  **`bbd712c1a79497762275e718e9f76959bedf6583`**。
- `39de04ed` 与 `bbd712c1` 的完整 Git tree 均为 `6f60b1b82e2e2d439192f7b08f6aa73c9498b75e`。
  `0fadc971..bbd712c1` 只有 `.github/workflows/ci.yml`、`docs/ci.md`、`scripts/ci_policy.py`、
  `tests/test_ci_policy.py` 四项差异；**本审查涵盖的产品代码、依赖、迁移、部署、合同及 qq-beta 材料完全相同**。
  CI 增量已只读核对：PR 的代码变更、main、tag、merge queue、手动运行仍选 full；未知比较选 full；
  最终 gate 拒绝失败、取消、缺失或意外跳过的必需 job。没有以空 diff 代替原始审查。
- 应用 code-review 与 security-best-practices / FastAPI 参考；两个非本轮作者子代理分别只读审查 Reader/Worker、
  预算/发送，均为 `gpt-6.1-sol / xhigh`。主审核对证据、去重结论并统一修改测试。

## 2. GitHub 固定证据

以下状态在本轮通过 GitHub API/`gh` 重新读取，非照抄任务书。#24–#28 已由集成负责人收口为 MERGED；
本聊天没有合并、删除分支或修改原 PR。

| 范围 | 完整 SHA | 实际 CI / 状态 |
|---|---|---|
| 原 main | `a33d3cb77f348828b17e4bb48c621c53cfcdc017` | [39400867023](https://github.com/yanyuhanyue/companion-core/actions/runs/39400867023)，五类 job 全成功 |
| #24 B1 | `5cf997a801f1ab3826456b9264dcf67e71f3eba4` | [push 39411592236](https://github.com/yanyuhanyue/companion-core/actions/runs/39411592236)、[PR 39411607122](https://github.com/yanyuhanyue/companion-core/actions/runs/39411607122)，均 5/5 |
| #25 B2 | `1a452d287185e969cff7ecb52a469e218b194d57` | [push 39411621337](https://github.com/yanyuhanyue/companion-core/actions/runs/39411621337)、[PR 39411634894](https://github.com/yanyuhanyue/companion-core/actions/runs/39411634894)，均 5/5 |
| #26 B4 | `1346fa29cb1f63651377dc33525a09fc96cd526a` | [push 39411647511](https://github.com/yanyuhanyue/companion-core/actions/runs/39411647511)、[PR 39411661227](https://github.com/yanyuhanyue/companion-core/actions/runs/39411661227)，均 5/5 |
| #27 B3 | `692da26b0779e2ecd8130c199523ec897e0fde4e` | [push 39411811532](https://github.com/yanyuhanyue/companion-core/actions/runs/39411811532)、[PR 39411820838](https://github.com/yanyuhanyue/companion-core/actions/runs/39411820838)，均 5/5 |
| 结构独立修复 | `a316c79240f34b70e0539b70f42c3cef48ebcfb6` | [39417572868](https://github.com/yanyuhanyue/companion-core/actions/runs/39417572868)，5/5；[container job](https://github.com/yanyuhanyue/companion-core/actions/runs/39417572868/job/118610221014) 成功 |
| 组合修复 | `0fadc971d0d06bd7c68e05ab1e65e46ecd493554` | [39417572124](https://github.com/yanyuhanyue/companion-core/actions/runs/39417572124)，5/5 |
| 集成候选 #29 | `39de04edce1fc392c9fa6d9c6a734b46ff78aaee` | [39422081781](https://github.com/yanyuhanyue/companion-core/actions/runs/39422081781)、[39422068330](https://github.com/yanyuhanyue/companion-core/actions/runs/39422068330)，均七个 job 成功，包含五类完整验证 |
| 最终 main | `bbd712c1a79497762275e718e9f76959bedf6583` | [39422750301](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301)，七个 job 全成功 |

最终 main 的精确 job：[preflight](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627568755)、
[Linux](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627732027)、
[Windows](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627732199)、
[container](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627731832)、
[browser-reader](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627731939)、
[plugin-native](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118627731947)、
[ci gate](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301/job/118629232446)。

## 3. A1-R1：Reader 镜像与来源边界

历史红例确实存在：[旧 container 日志](https://github.com/yanyuhanyue/companion-core/actions/runs/39416290627/job/118606012175)
在只包含 Reader 的镜像启动 `--help` 时，经 `source_reader.heybox -> companion.sources` 报
`ModuleNotFoundError: No module named 'companion'`。这不是当前候选的失败。

已有修复 SHA 为 `a316c79240f34b70e0539b70f42c3cef48ebcfb6`，组合对应
`0fadc971d0d06bd7c68e05ab1e65e46ecd493554`；两者的策略叶模块和 Reader 镜像文件相同。
`companion/source_policy.py:6` 起仅导入 stdlib `dataclasses`、`typing`，常量由 frozen dataclass、
tuple、frozenset 组成。`companion/sources.py:30` 包装未知来源时仍抛
`CoreError("CONTENT_SOURCE_NOT_ALLOWED", 400)`。
`deploy/source-reader.Dockerfile:13` 仅额外复制 `companion/__init__.py` 与 `companion/source_policy.py`；
没有复制 Core 数据库、服务、Outbox、模型配置或 Secret。

Reader 的导入变化只更换图片主机常量来源。实际网络仍走 `source_reader/network.py` 的 HTTPS、固定主机/路径、
公网 DNS 答案及 pin 后 TLS 名称验证，不跟随跳转。来源 URL 与 CDN 规则没有因叶模块抽取放宽。
`source_reader/worker.py:64` 的逐 GET Core 预检、本地 grant 的 I/O 前后复核，以及
`companion/exploration.py:328` / `:349` 的租约、epoch、grant、适配器和提交验证均保留。
账号撤权拒绝后续读取；明确遗忘继续擦除已读取资料及其派生闭包。

精确 `0fadc971` 和最终 main 的容器日志均证明：`--network none --read-only --cap-drop ALL`、
`no-new-privileges`、UID 10003、受限 scratch，`--help` 成功；匿名只读 smoke 返回 PASS、
`upstream_requests=0`。最终 main smoke 记录内存峰值 28,467,200 bytes、pids 峰值 6。
本地 image 边界测试另外阻止导入 Core 业务模块及 fastapi/fastembed/pydantic/alembic/sqlite3。

限制：本机 Docker daemon 未运行。没有声称本轮在本机重新启动容器；上述容器证据来自精确 SHA CI。
容器 smoke 覆盖启动和拒绝未授权读取，受限 HTTP MockTransport、DNS/grant 行为主要由宿主测试覆盖，
未额外取得“容器内成功 HTTP fixture 全流程”的新证据。这是覆盖限制，没有发现相应实现缺陷。

## 4. A1-R2：图片 Worker 红例根因与恢复覆盖

[旧 Linux 红例](https://github.com/yanyuhanyue/companion-core/actions/runs/39416290521/job/118606010736)
为 `1 failed, 950 passed, 4 skipped, 1 warning`，失败断言 `len(vision.calls) == 1` 实际为 0。
独立核对旧测试和当前 `companion/content.py:639` 后，根因可进一步精确为：旧测试把
`content.HTTPMediaProvider` 这个**类型**整体替换成 lambda；生产离线准入的
`isinstance(self.provider, HTTPMediaProvider)` 因第二参数不再是类型而抛 TypeError。
`run_once` 在 FETCHING 阶段记录失败，尚未调用 provider。该 TypeError 已由纯内存探针确认，
不能把它描述为实际图片 provider 失效，或仅笼统归因于实例未被替换。

新测试 `tests/test_worker_composition.py:146` 保留真实 HTTPMediaProvider 类，只给构造器注入
MockTransport，因而真正经过 `process()` 的 POST。仍明确断言 POST 恰好一次、
link_image 为 SUCCEEDED、owner/source 关系一致、输出 observation 为 FETCHED、费用为 SETTLED，
并且没有触发聊天语义索引。没有删除任务、放宽成功条件或绕过费用状态。
该修复在 `0fadc971` CI 两平台均为 **952 passed / 4 skipped / 1 warning**，当前 main 也已验证。

已有测试涵盖撤权、结果迟到、超时/断网、图片费用 UNKNOWN、租约变化和 UNKNOWN 不重试；没有重复补这些测试。
本轮只新增一个组合覆盖点：真实 QUEUED 工作经下载及真实 `_reserve` 持久化为 RUNNING 后模拟进程退出，
关闭 Store、推进到租约过期，再两次进入真实 `content_worker`。
验证任务成为 `UNKNOWN / CONTENT_PROCESSOR_LOST`、费用从 RESERVED 转 UNKNOWN 但全额保留、
来源关系和预留流量保留、没有输出 observation，也没有再次下载、provider 调用或语义启动；Store 正常关闭。
这是**覆盖补充，不是新发现的产品缺陷**；未将合成进程退出称为真实主机断电测试。

回归提交：**`2ac4a154f50307d72d56769b4350c6655b267e92`**，仅新增
`tests/test_worker_composition.py:200` 的测试。本轮产品 `fix_commits=[]`。
该补充尚未进入最终 main，也没有本提交的 GitHub CI；它的实际证明是本地单文件 10 项通过。

## 5. A3：资金、发送、恢复与隐私结论

预算主体、模型适配器、0017 迁移及预算测试与原 #24 完全一致；window/outbound/app 和窗口测试与原 #25 完全一致。
主审与子代理读取原始实现、历史修复、调用方和既有回归后，未发现新可操作缺陷。

| 检查面 | 核对结果与证据位置 |
|---|---|
| UNKNOWN / RESERVED 和账本回滚 | `paid_budget.journal` 对照金额、状态、contract、持久 I/O fence 与 request 集合；DB 单独回退、journal 单独回退均拒绝重新调用。UNKNOWN 不释放次数、费用或并发。 |
| 跨进程、锁等待和截止 | BEGIN IMMEDIATE 及取锁/输入证明/fsync 后新鲜 clock 校验保留；实际多进程并发测试通过。独立 SQLite 锁等待跨审批截止及跨租约截止均 HTTP=0。 |
| token、价格、缓存和身份 | pinned tokenizer/framing、USD 非零费率、输入上界、逻辑 request、实际 Settings 身份及 per-window credential HMAC 保留；不可证明的媒体付费合同仍拒绝真实 HTTP。 |
| 晚到费用 | admission 的 journal custody 与可变启用审批分开；撤销审批、换 journal 路径或账本故障仍保全较高已知费用及停止信号。既有在途/晚到费用回归通过。 |
| 发送时窗 | OS 锁、固定身份/epoch/库存与逐项批准保留；输入及外部尝试持久上限各为5，并发1；实际 DNS preflight 后还有 I/O 前最后检查。UNKNOWN 消耗名额并停止，不自动重试。 |
| owner / private 与数据边界 | normalizer 只接受唯一 owner、bot、friend private；Core 仍校验主体与私聊。引用、转发、网页和图片文字不获得执行权限。 |
| Spool / Outbox | Core HTTP v1 边界、幂等 receipt、发送前持久不确定记录、ACK 回报重试与禁止重发保留；队列满及错误有明确状态。预算 UNKNOWN 和投递 DELIVERY_UNCERTAIN 分属独立账目。 |
| 最新 ErasureLedger | `backup.restore:36` 要求独立 ledger、身份一致、回放成功及完整性检查后才发布新文件；旧 COMPLETE 缺来源归属时 `privacy.py:316` 阻断。0017 付费库恢复不会获得新准入。 |
| 擦除协调增量 | `privacy.py:339` 按持久 tombstone 的明确归属修复旧 COMPLETE；handler 在事务中执行，失败不发布恢复文件，重复回放不重清无关新回复。费用、发送不确定状态不被退款/清零。 |

独立故障探针除了既有 109 项预算/窗口回归外，再覆盖：

1. SQLite 锁等待跨审批截止：`PAID_APPROVAL_EXPIRED`，HTTP=0，保留 RESERVED。
2. SQLite 锁等待跨 admission 租约：`MODEL_ADMISSION_LEASE_EXPIRED`，HTTP=0，保留 UNKNOWN。
3. DB 已记录 I/O fence、费用/状态仍与旧 journal 同为 RESERVED 时，仅将 journal 回滚至 I/O 前：拒绝后续 HTTP。

以上 3/3 通过，无真实供应商或 QQ 调用。既有历史 R-B1-01..04、R-B2-01 的修复和回归均被核对，
没有将历史已修复问题重复记为本轮开放缺陷，也没有重做额外重构。

## 6. 实际验证与证据边界

本地 Windows 11 / Python 3.12.10，pytest 9.1.1、httpx 0.28.1、pydantic 2.14.1、FastAPI 0.146.0。
复用仓库解释器及只读模型缓存，设置离线 Hugging Face；测试缓存、临时文件均在工作树内，协调状态写入约定路径。
主审通过 `scripts/check.ps1 -TestFiles ...` 跑全仓 Ruff 和相关文件，未无理由重跑完整项目。

| 实际验证 | 结果 |
|---|---|
| 擦除协调、release privacy/migration、grant erasure/preflight、Worker、Reader image、source definitions 八文件 | **106 passed / 0 failed / 0 skipped / 1 warning，166.12s**；Ruff 通过 |
| Reader/Worker 子代理先行定向验证 | **76 passed / 1 warning**，与上行重叠，不累计为新覆盖 |
| budget + window 两文件 | 首次 **108 passed / 1 failed，186.62s**；失败子进程在导入测试 helper 前报 ModuleNotFoundError。复用根解释器的 editable 路径没有当前固定树；显式 `PYTHONPATH` 后仅重跑失败项 **1 passed，3.48s**。109 项均实际通过，没有改测试断言。 |
| 独立账本/真实锁等待探针 | **3 passed**，HTTP=0 或没有新增 HTTP |
| 新回归后的 Worker 文件 | **10 passed / 0 failed / 0 skipped / 1 warning，22.47s**；Ruff 通过 |
| 最终 main Linux / Windows CI | 各 **1060 passed / 4 skipped / 1 warning**；Linux 146.73s，Windows 137.21s |
| 最终 main browser-reader | **4 passed / 1 warning，9.14s**；与 full suite opt-out 跳过分列 |
| 最终 main 容器 / plugin-native | 精确 job 成功；Reader non-root/readonly/no-network smoke PASS，真实上游请求0 |
| 最终 main replay / smoke / build / package | 两平台100-event deterministic replay、API+各 Worker+private delivery/auth smoke、wheel/sdist、无 Git 源码重包及安装迁移均通过 |

本地警告为现有 Starlette TestClient/httpx 弃用警告。没有把重跑或交叠测试求和，也没有把旧绿 CI 冒称为新增测试提交的 CI。
GitHub 原始 job 元数据、红例/绿例日志、本地结果和独立探针保留在工作树 `.local/review-evidence/`，
供统筹读取，不进入 wheel/sdist 或公开提交。新回归之外没有修改产品源码、依赖或迁移。
独立矩阵原执行脚本另存 `a3-independent-matrix.py`，SHA256 为
`baf6fe3b47ce24d17e5378dfeb8e2fd4c365aa1363fc52bff14b9ce20a339633`；主审已读取核对。
脚本以当前工作树为 PYTHONPATH、经 Python stdin 执行；原 scratch 保留，`exist_ok=False` 会拒绝复用，
不能直接重跑覆盖该证据目录。

## 7. 残余风险、写操作与后续

- 同时回滚/丢失 DB 与所有独立 journal/ledger，本地软件无法可靠识别；最新三类独立记录仍须在独立故障域保全。
- 软件 token/费用上界依赖已验证的供应商输入/费率合同，不能替代供应商账单和账户封顶；停止不能撤回已在途 I/O。
- 真实非空三卷/整机恢复、供应商付费、QQ 新窗口、手机端成对 E2E、M1、Reader 账号及媒体仍未执行；相关实机门禁继续 BLOCKED/DISABLED。
  历史真人5条回复没有提升为严格场景；真实 latency/cost/savings 的未测字段继续 null。
- 本轮写入仅为本树测试、审查报告及本树合成验证/证据文件，以及约定协调状态 JSON。
  只读查询 GitHub 并 fetch；没有操作 VPS、真实私密数据、收费、QQ、DSH、Android 或 Windows 远程执行器，未启用运行特性。
- 已验证的主审 check 回合临时目录由脚本正常清理。子代理清理自有目录的操作被自动审批审查拒绝，
  原因仅为 `blocked by policy`；没有删除，也没有换工具重试。
  保留 `.local/test-temp/reader-worker-review`、`.local/a3-review-pytest`、
  `.local/test-temp/a3-budget-rerun`、`.local/test-temp/a3-budget-review`；均为合成测试资料。
- 没有创建新 PR；本地回归提交和报告提交可供统筹选择 cherry-pick。没有产品修复需要集成，也没有未解决重大缺陷。
- 回退只能使用兼容代码/隔离目标；已经产生 admission 后不得删除0017账目、降级丢表或把 UNKNOWN 改回 RESERVED。
  保留原始数据库、最新 ErasureLedger、预算及窗口 journal。部署停止责任人/窗口/镜像 digest 仍由单独审批填写。

给下一轮的执行提示：

> 在最新 main 上先核对本报告 `reviewed_sha=bbd712c1a79497762275e718e9f76959bedf6583` 与所选候选的产品 diff。
> 若需要保留 Worker 重启组合回归，可审查并 cherry-pick `2ac4a154f50307d72d56769b4350c6655b267e92`，
> 用明确指向该工作树的 PYTHONPATH 运行 Worker 定向检查；新增产品差异才追加相关验证。
> 复用本报告及精确 SHA CI 的既有结果，不重跑相同全量。后续部署/迁移、真实非空恢复、QQ最多5次新窗口、
> 付费模型、M1、Reader账号读取分别取证和单独审批；本报告不授予任何实机权限。
