# P7 阶段发布复核

**实验环境已运行，真实 OneBot 握手与手机5条 Demo 收发成功；完整场景验收和生产发布仍未通过。**
运行源码 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`，配置 `P7-DEMO-1`；
报告提交由 [Draft P7-A #20](https://github.com/yanyuhanyue/companion-core/pull/20) head
和私有最终清单单独标识。产品源码/迁移无修改，#19→#18→main关系保留。

## 已验证的结果

- #19相对#18的24文件增量复审，无新增可复现P0/P1/P2；完整Windows检查715 passed/2 skipped/1 warning，Chromium执行。
- 合成Compose、100-event replay、API/worker/auth smoke与发布包验证通过。
  原报告4592241的push/PR两组各5项CI成功；最新报告head以本PR检查和最终清单为准。
- 主人明确主机无生产任务、专用于Companion Core并允许使用，本轮按此实验授权继续隔离安装。
  原服务恢复点仍不存在，不能计为恢复PASS。许可沿用主人已确认的邮件声明，未读取/公开原件。
- 官方SnowLuma按linux/amd64固定摘要运行；Core/Bridge从固定Git archive构建，归档SHA256
  `fbb94835a502447c44edf289d5dd75069c9cc574c8338e094cb6c0f5b5ef7c4b`验证通过。
  Docker29为本地构建提供真实RepoDigest，运行Compose三组件均image@sha256、无build。
- Core迁移exit0，API healthy/HTTP200，聊天Worker运行。SnowLuma WebUI/noVNC HTTP200，
  supervisor qq/snowluma运行，Linux QQ包3.2.32-52194；QQ登录后精确只读probe验证
  SnowLuma/2.0.3-node、身份匹配、online/good均true，真实owner friend事件array通过。
- 手机确认5条Demo回复，每输入1条、无重复。最终5个job成功、Outbox SENT=5、attempts=5，
  Spool inbox/results各accepted=5，PENDING/LEASED/SENDING/DELIVERY_UNCERTAIN均0。
  ModelUsage SETTLED=5为Demo，费率估算0，付费调用0；未以SETTLED计为真实付费。
- 逐条操作辅助进程使用既有deliver_tick，不运行发送循环；常驻delivery=false，先持久预留配额，
  仅白名单事件、最多5次、固定身份和截止时间。窗口已主动关闭，不能继续发第6条。
  初始Compose run参数错误在任何发送前退出，配额/attempt/results均0后修正，未重试不确定投递。
- 匿名LIVE_ONEBOT n=5：receive→spool P50 16.3272ms/P95 31.738ms，
  receive→Core ingest ACK P50 76.784ms/P95 131.5216ms，HTTP send ACK P50 165.4549ms/P95 320.6717ms。
  各自是组件内跨度，n很小；未采集Core context/真实模型区间，不是完整QQ响应SLA。
  手机时间不完整且逐条由操作者放行，真实端到端P50/P95保持null。
- 原有3个容器的挂载、镜像、状态、启动/restart、网络/安全所选元数据完全一致；
  原网络/卷/监听保留，原服务重启0次。只新增5个项目容器（4运行、1迁移退出）、
  2个网络、7个卷（含隔离smoke卷），管理口只在回环，公网新增监听0。
- 初始空Core加密备份868452bytes已到主人工作站，另存DPAPI密钥并独立解密恢复、重放最新Ledger、
  integrity ok/foreign keys0；它仅证明初始空库恢复，不包含后来QQ样本，不代替整机/原服务/生产恢复。

| 组件 | 实际 RepoDigest |
|---|---|
| bridge | `companion-p7-bridge@sha256:8367e1d85b90df577df5aaa8c3da0d771cc4545ae5bb241d7f1b45c0ed3e760f` |
| core | `companion-p7-core@sha256:5eb8045c47f62f68202e164bb3fcb7f93be4633c31a15d55047743bc436b7891` |
| snowluma | `motricseven7/snowluma@sha256:78b66d568e3c0991a8ba89bfc188281f87d5d45f32e259c885f43e2312ff0f4a` |

## 剩余门禁

5条手机可见是收发样本数，严格首批五场景仍PARTIAL、20场景签收0/20。
Demo固定回复不能验收语义问答/上下文；真实同ID重复未观察到（duplicate_suppressed=0），
不能拿不同ID同文输入或离线Fixture补数。单独的故障/恢复签收、部分客户端时间/评分仍缺失。
继续实测应先批准新范围/窗口；付费模型需明确provider/model/endpoint/费率/单轮和周期硬上限，
Key只进Core私密配置；M1仍需独立授权。不能自动续期、清队列或开启长期投递。

主人已确认自然过期/停止后续读取保留历史、明确遗忘清除相关派生资料的规则。
独立 [G1 Draft #21](https://github.com/yanyuhanyue/companion-core/pull/21) 候选
`907d62c1491e0a3613a7653f2d4eb0275ec4a357` 的完整本地检查731 passed/2 skipped/1 warning，
包验证、回放和worker冒烟通过；候选尚未部署，真实运行G1仍未通过。
旧资料归属清单若非空仍需冻结相关处理并由主人审查；不能用离线回归代替真实账号验收。
memory/Reader/diary/探索/唤醒/社交/content/executive/media/Android全部关闭，
没有产品DSH或Windows执行。原服务恢复点、生产异地恢复、真实费用、账号长期稳定性和20自然日记仍缺，
production_ready=false。

## 数据边界与回退

真实owner/bot、两个独立强Token、稳定epoch、VNC凭据仅保存在私密配置；公开Git不含服务器路径、
IP+端口拓扑、账号/正文/密钥/私人截图。Core DB/Ledger、Bridge Spool、QQ三卷分别隔离；
Bridge UID10004，只挂配置/Spool，无模型Key/CoreDB/Docker socket。Core UID10001。
Core/Bridge只读根/drop ALL/无新特权；SYS_PTRACE/seccomp例外仅SnowLuma。
Bridge256MiB/PID64，Core API/Worker各1GiB/PID96，SnowLuma3GiB/PID512。

发生错误目标、身份/版本改变、UNKNOWN、Spool冲突/容量、HTTP/WS异常等情况，停止新输入和本项目投递，
保全DB/Spool/最新Ledger/稳定epoch和私有证据；不重发UNKNOWN、不重置费用、不影响原服务。
仅针对本项目服务停机/回退固定摘要，禁止down -v、全局prune或覆盖在线库；旧备份先重放最新Ledger。
私有加密备份与DPAPI材料、登录数据和验收证据必须在任何worktree清理前保全。

恢复脚本首次未显式关闭SQLite造成临时目录残留，已修正后独立恢复成功。
自动审批拒绝了一条包含复核/临时目录清理的命令，原因仅blocked by policy；命令未执行，
该原临时目录保留且未重试删除，新一轮恢复临时副本正常清理。保留的仅是初始空Core恢复副本。
