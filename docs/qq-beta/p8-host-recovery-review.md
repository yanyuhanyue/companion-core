# P8 宿主、非空数据恢复与回退审查

**整机/原服务恢复门禁仍为 `BLOCKED`。** P7 实验主机使用例外不是备份成功，也不等于生产恢复能力。
已验证的真实离机恢复仅为初始空 Core；后来五条 QQ 真实消息后的非空状态恢复为 `NOT_VERIFIED`。
本 A1/A2 子工作包只读本地源码/固定 P7 报告并运行合成回归，没有 SSH、主机启停、真实数据读取/复制、快照、上传或恢复。
其他 P8 执行记录由 [readiness review](p8-readiness-review.md) 和 [证据索引](p8-evidence-index.md) 单独归档；
不能把阅读 P7 报告写作本轮重新观测主机。

## 已覆盖与缺口

历史依据固定为 P7 报告 `904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf`：
[恢复方案](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-recovery-plan.md)、
[宿主恢复字段](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-host-recovery.json)、
[发布复核](https://github.com/yanyuhanyue/companion-core/blob/904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf/docs/qq-beta/p7-release-review.md)。
这些链接指向独立报告支线，没有复制或改写 P7 文件。

| 范围 | 实际证据 | 状态 / 不能推出的结论 |
|---|---|---|
| 供应商整盘快照 | P7 主人确认无恢复点；实例页面未观察到入口；id/费用/恢复入口均 null | `NOT_CREATED`；不能据此说供应商绝不支持快照 |
| 原有 1Panel/OpenResty/redroid | P7 3 个容器、17 条 bind/16 个来源、21 项权限元数据与前后比对；原服务重启 0 次 | 元数据清单不包含可重建服务所需全部私密内容；独立备份/恢复 `NOT_VERIFIED` |
| 初始空 Core 离机加密恢复 | 2026-10-20T06:43:03.873063+00:00，868452 bytes；最新 Ledger replay，integrity=ok，FK errors=0；events/outbox/model_usage 均 0 | `PASS_INITIAL_EMPTY_CORE_STATE`；仅此范围 |
| 初始密文校验 | SHA256 `c892c100bbdfd463faf8d7a25cf25b39484578978413d6cf88d9cd07ab3bf065` | P7 记录值；本轮未读取档案或重新计算 |
| 初始最新 Ledger | SHA256 `eb579cec25bd3f6ce8a089059fad815d2bc53115f0d9a4bace9e67f83d48e8f4` | 仅证明初始演练使用它；不能证明它仍是五条消息后的最新 Ledger |
| 五条真实消息后的非空 Core | P7 运行快照 events=5、Outbox SENT=5、delivery attempts=5；没有相应独立加密恢复证据 | **`NOT_VERIFIED`**；不得用初始空库或合成非空恢复补齐 |
| Bridge Spool / 稳定 epoch / QQ 登录卷 | P7 曾运行；没有新状态的协调备份与隔离恢复记录 | `NOT_VERIFIED`；不能恢复后自动接收/发信/登录出网 |
| 宿主启动与全部磁盘/原服务 | 无整盘恢复演练、无可用恢复入口回执 | **`BLOCKED`** |
| 生产异地恢复与凭据灾备 | 未演练，初始密钥只记录为主人账号保护，独立离线密钥托管未完成 | **`BLOCKED`**；同一机器故障可使档案与密钥同时不可用 |
| 本轮合成恢复 | 下列现有测试实际执行：15 passed / 0 skipped / 1 warning | `OFFLINE_PASS`；不等于非空实机/宿主/生产恢复 |

旧 P7 文字曾出现“安装仍阻断”，其后机器字段和结尾明确记录主人批准专用实验主机例外。
本审查采用精确结论：该历史例外允许当时隔离实验安装，**恢复门禁没有变成 PASS**。
本轮备份/复制/部署/停机仍须针对准确对象另行批准。

## 可审的恢复审批包

下面是待主人决定的具体范围，不是已获得的操作许可。所有 `UNKNOWN/null` 都应在执行前填实，
公共报告只记匿名清单/哈希；实际路径、账号、密钥、业务正文与登录资料只进入受控私密记录。

| 决策项 | 建议与所缺信息 |
|---|---|
| 方案 S：供应商整盘快照 | 先确认当前套餐对全部系统分区/附盘的覆盖、应用一致性、是否需停机、实例删除后保留、区域/期限/费用、隔离新建/挂载恢复入口；全部 `UNKNOWN` |
| 方案 F：独立离机加密档案 | 目标必须与源宿主故障域分离；保留可重建服务的文件、权限、定义和应用一致性导出；目标、配额、保留期、传输费用、责任人仍待定 |
| 允许数据范围 | 下表的每项私密数据读取/复制须明确列入批准，不因已有目录元数据而自动获准 |
| 一致性与停机 | 先批准本项目 Bridge→Worker→API 的准确服务停止范围；旧服务停机另列服务、最大时长、恢复顺序；没有通用“允许停整机” |
| 密钥保管 | 档案与密钥分离，主人掌握独立离线解密/恢复副本；指定托管人、恢复材料与灾难时可用性；禁止密钥进 Git/CI/日志/命令行参数或与档案同包 |
| 最新 Ledger | 独立于 DB/旧档案保全；记录 ledger_id、最新序号/末记录哈希、文件哈希/时间与不再写入的一致性边界；无法证明最新即阻断 |
| 恢复目标 | 全新隔离盘/目录/主机，默认禁止 QQ/模型/站点业务出网，禁用 Worker/Bridge/Reader/自主能力；目标和防回连隔离证据待定 |
| 停机与回滚人 | 主人指定操作者、允许影响时窗与失败停止点；当前 `null` |
| 成功证据 | 快照/档案实际 ID、完成时间、范围、字节、密文哈希、费用、保管人与恢复入口；独立解密、权限、应用检查和恢复后无发送证据 |
| 失败处理 | 停止本轮任务，保留源、最新 Ledger、错误证据与已生成档案；不覆盖在线卷、不删旧恢复点、不自动再发或降级迁移 |

本轮未查询/购买供应商套餐、未联系服务商、未点击快照或救援入口。
P7 引用的官网通用产品介绍不是本实例能力证据；快照支持、一致性、费用与恢复入口继续 `UNKNOWN`。
后续查询公开材料也只能形成能力线索，不能替代当前实例的实际成功回执。

### 范围清单与一致性方案

| 对象 | 必须保全与验证 | 一致性/停止计划 |
|---|---|---|
| Core 数据库 | DB 中已提交 WAL 页、schema/version、身份、预算 reservations/UNKNOWN、Outbox、provenance | 使用现有 SQLite backup API；先停止本项目全部写入者并记录队列，不对活动 DB 盲目文件复制 |
| 最新独立 ErasureLedger | ledger_id、完整记录链、最后序号/哈希、权限与单独档案 | 写入者停止后与 DB 快照形成清单；不能用旧备份所附 Ledger 替换较新副本 |
| Bridge | 持久 Spool、稳定 protocol_epoch、配置版本、未确认入件/发送结果 | 停止受控发送进程和 Bridge，逐项登记 uncertain；保留原状态，不清队列/换 epoch |
| SnowLuma | 三个登录/数据卷、固定版本/镜像摘要、必要私密设置 | 如需一致性停机须另行批准；恢复副本离线，不自动登录或对 QQ 出网 |
| 1Panel/OpenResty | unit/版本、配置、证书、站点、应用数据库与依赖 | 由所属服务支持的一致性导出/暂停方案；需独立服务停机许可，保留 uid/gid/mode/ACL/xattr |
| redroid | 实际共享数据源、设备/权限/启动定义、固定镜像 | 活动 Android 目录拷贝不能称一致恢复；两个容器共用来源时只选择一个启动；不得扩大 Android 权限 |
| Docker/宿主 | 镜像摘要或可恢复镜像、网络/容器定义、磁盘/分区/文件系统、systemd、必要依赖 | 不把 Docker 活动存储目录打包后宣称整机恢复；SSH 私钥不混入业务档案；不更改防火墙/原服务 |

### 空间、费用与时间预算

P7 的历史只读估算：必要业务来源约 2.0GB、Docker 镜像大小元数据约 4.7GB，
文件级档案建议预留 10–15GB、含验证空间约 30GB、首次传输约 10GB；整盘可见容量约 80GiB。
这些值未在本轮重新测量，也不是已生成备份的大小；压缩、镜像共享层、QQ 新数据、保留策略均会改变它们。

执行预算应先按实际新统计填写：`required_storage = archive_bytes × retained_copies + isolated_restore_bytes + scratch_bytes`；
`transfer_time = selected_bytes / approved_effective_bytes_per_second + consistency_stop_time + verification_time`。
供应商快照/存储/出入流量单价、税费、档案实际字节、限速、RPO/RTO、最大停机时间与总费用硬上限现在均 `UNKNOWN`。
未确定并批准费用上限前不得购买/创建；不能填“免费”“零影响”或虚构完成时间。

## 现有离线脚本/CLI与实际验证

已核对 [Core CLI](../../companion/__main__.py) 与 [backup.py](../../companion/backup.py)。
仓库已有 `python -m companion backup DESTINATION` / `restore SOURCE DESTINATION`，不新增第二套备份系统。
它们只覆盖 Core，不是宿主、Spool 或 SnowLuma 的完整备份命令。
未来仅在准确配置/数据/目的地/密钥与读取复制权限获批后，才可由受控环境运行下列模板：

```powershell
# Configuration, latest independent Ledger and key come from the approved private environment.
python -m companion backup "$P8NewArchive"
python -m companion restore "$P8ApprovedArchive" "$P8NewIsolatedDatabase"
```

`COMPANION_BACKUP_KEY` 由受限进程环境安全注入，不能写入命令历史、日志或公开文件；
`COMPANION_CONFIG/COMPANION_ERASURE_LEDGER` 指向经审核配置及最新独立 Ledger。
新档案不能覆盖已有文件；恢复目标必须是新路径，绝不把在线库路径填进第二个参数。
`restore()` 先读取已有 Ledger，在未发布的临时数据库中迁移到 head、校验 ledger_id、
回放最新 Ledger、处理不确定状态并检查完整性/FK，最后才发布新数据库文件；
不得先启动服务暴露旧数据再补做 Ledger replay。
Core 恢复成功不会授予启用 Bridge/Worker/付费/M1/设备会话的权限。

本轮在 Windows、代码基线 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`（C19）上运行现有合成测试，命令为：

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_release_migration.py tests/test_onebot_erasure.py::test_pre_event_backup_latest_ledger_blocks_pending_spool_resurrection tests/test_onebot_erasure.py::test_complete_ledger_records_rebuild_missing_tombstones tests/test_m0.py::test_m0_11_encrypted_backup_restore tests/test_m1.py::test_m1_09_erasure_invalidates_derivatives_and_survives_old_backup tests/test_onebot_delivery.py::test_restart_after_durable_send_intent_reports_uncertain_without_send tests/test_onebot_delivery.py::test_late_provider_receipt_cannot_override_human_reconciliation tests/test_resilience.py::test_erasure_ledger_loss_blocks_startup tests/test_observations.py::test_restoring_backup_never_restores_device_permission --basetemp="$P8RecoveryTemp/pytest" -o "cache_dir=$P8RecoveryTemp/pytest-cache"
```

`$P8RecoveryTemp` 是本轮新建的工作区 `.local/test-temp` 合成数据目录；不使用旧受保护目录。
结果：**exit 0；15 passed、0 skipped、1 warning，21.48s**。警告是 Starlette TestClient/httpx 弃用提示。
匿名日志为工作区私有证据 `p8-a2-recovery-tests.txt`，与最终全量验证和 CI 分开记录。

| 已执行的现有测试 | 直接证明的范围 |
|---|---|
| `test_release_migration.py`（6 项） | 单迁移 head；非空旧 schema 升级保留原列；加密旧库+最新 Ledger；删除目标与合法独立事实区分；预算 UNKNOWN 与 Outbox uncertainty 保留 |
| OneBot pre-event backup / COMPLETE tombstone（2 项） | 旧备份+pending Spool 不复活已删除入件；既有 COMPLETE 记录可重建缺失 tombstone |
| M0 encrypted backup / M1 old-backup erasure（2 项） | 合成非空库可解密恢复；相关派生资料删除不被旧备份复活；缺 Ledger 失败 |
| restart intent / late receipt（3 项，含 2 个核对状态） | 持久发送意图后重启不再次发送；迟到回执不能推翻主人 SENT/CANCELLED 核对 |
| missing Ledger startup / restored device permission（2 项） | 无 Ledger 拒绝启动；恢复副本不能沿用旧设备会话授权 |

这些测试只使用合成数据、Stub/本地客户端；没有触发 QQ 或真实模型计费。
Linux uid/gid/ACL 实际恢复、隔离主机恢复、真实五条消息后的最新 DB+Ledger+Spool 一致性不在这 15 项证明范围。
最终全量检查结果见 P8 总证据，不把本小集当作全量 PASS。

曾提交一条“pytest + 本轮目录清理”的命令，自动审批以 `blocked by policy` 拒绝，进程未启动。
随后只执行测试，保留新合成临时目录；未重试被拒绝删除动作、未碰旧受保护恢复目录。
任何后续清理必须先确认保护/审批状态，不能换工具、改路径或混入其他命令绕过拒绝。

## 隔离恢复与回退顺序

1. 固定候选完整 SHA/摘要、schema、批准范围、原服务摘要和隔离目标；证明目标不会接在线卷/QQ/付费端点。
2. 按已批准范围停止新增输入、受控发送、Bridge、Worker 和 API；记录 Outbox/Spool/预算与最新 Ledger 清单。
3. 校验实际快照/密文哈希、密钥可用性和最新 Ledger 身份/链，恢复到新隔离目标；缺一项即停。
4. Core 使用上述现有 restore 路径；检查 schema、integrity/FK、provenance、权限、删除闭包、tombstone 与预算 UNKNOWN。
5. 在禁止发送的条件下验证 Spool/Outbox；恢复的活动投递进入 `DELIVERY_UNCERTAIN`，禁止自动重新派发。原 SENT/CANCELLED 不回退。
6. 原服务只按其独立获批的恢复计划验证；redroid 共享源不同时启动两个副本。比较在线原服务前后摘要，有差异立即停止扩大动作。
7. 保存成功/失败证据、真实字节/费用/RPO/RTO 与残余缺口；只有得到该范围真实独立恢复证据才能提升对应门禁。

代码回退应先停止发送，保留卷/Spool/稳定 epoch/最新 Ledger，记录准确候选与队列状态，再选择兼容当前 schema 的已审镜像。
本审查没有新增迁移；不得在旧库上降级迁移，不能把旧镜像兼容性或旧 Ledger 当作当然成立。
整盘回退、真实数据切换、账号会话撤销和删除恢复点都不是自动回退动作，须另批准确范围。

立即终止条件：目标指向在线路径/卷、密钥泄漏、档案/权限材料缺失、无法证明最新 Ledger、
QQ/模型意外出网、UNKNOWN、或 1Panel/OpenResty/redroid 状态异常。
保留证据与源数据；不执行 `docker compose down -v`、全局 prune、广泛清理、覆写在线库或盲目重试 UNKNOWN。
真人先核查实际 QQ，随后仅通过已认证主人核对将未知投递收口为 SENT/CANCELLED，不能改回 PENDING。
