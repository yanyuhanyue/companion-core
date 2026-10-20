# P8-B 候选部署与非空恢复申请草案

证据等级 `OFFLINE_PROPOSAL`。本轮没有登录 VPS、读取真实数据或密钥、复制档案、
购买快照、启停服务、构建在线镜像、迁移或发送 QQ。所有操作均待各自准确审批。
可填写的机器清单为 [p8b-approvals.json](p8b-approvals.json)，
恢复程序沿用 [P8 恢复合同](p8-host-recovery-review.md)，不另建备份工具。

## 候选与历史运行版本

本次重新 `git fetch` 确认 main 为 `a33d3cb77f348828b17e4bb48c621c53cfcdc017`。
#18–#23 均 MERGED，main 的 [CI 39400867023](https://github.com/yanyuhanyue/companion-core/actions/runs/39400867023)
五个 job 成功。候选新代码分别在 B1/B2 Draft PR；未合并、未部署。
必须从最终获批提交构建、核验每个 image@sha256，不能把任意支线通过当成组合已通过。

历史运行依据是 P8-A 固定报告，**本轮没有重新观测主机**：
源码 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`、配置 `P7-DEMO-1`、schema `0015_article_images`。
已合并 main 包含 `0016_source_grants`；B1 若新增迁移，必须把完整单链和最终 head 纳入批准，
不能把“0015→0016”当作自动批准以后的所有迁移。候选完整 SHA、配置哈希和迁移 head 现为 `null/BLOCKED`。

| 对象 | 历史记录的准确 digest | 新候选获批 digest |
|---|---|---|
| Core | `sha256:5eb8045c47f62f68202e164bb3fcb7f93be4633c31a15d55047743bc436b7891` | `null/BLOCKED` |
| Bridge | `sha256:8367e1d85b90df577df5aaa8c3da0d771cc4545ae5bb241d7f1b45c0ed3e760f` | `null/BLOCKED` |
| SnowLuma | `sha256:78b66d568e3c0991a8ba89bfc188281f87d5d45f32e259c885f43e2312ff0f4a` | `null/BLOCKED` |

上述历史 digest 来自 [P7 发布记录](p7-release-review.md)，没有在本轮拉取/探测。
旧 Core 镜像只是历史运行对象，尚未证明能安全读取候选 schema/最新 Ledger，
**不能直接作为在线降级目标**。固定 SnowLuma 版本也不意味着本轮许可重新登录/启动。

## 分开签署的范围

| 申请 | 允许执行前需要填实 | 当前状态 |
|---|---|---|
| 非空 Core | 责任人、停止本项目写入者时窗、DB/WAL、Outbox、预算账本、最新独立 ErasureLedger、档案 hash/故障域/密钥托管/RPO/RTO | BLOCKED |
| Bridge | 准确 Spool、稳定 epoch、窗口配额账本与未确认项的一致性边界；绝不复用旧恢复点重置配额 | BLOCKED |
| QQ 三卷 | 私密范围、是否另批停 SnowLuma、会话隐私、隔离恢复不可出网；不包含重新扫码/登录许可 | BLOCKED |
| 原服务 | 1Panel/OpenResty/redroid 各自负责人、数据导出/停机/恢复策略，不能从本项目停机推定 | BLOCKED |
| 整机/供应商快照 | 当前实例支持、覆盖磁盘、一致性、保留、区域、费用上限、隔离恢复入口 | UNKNOWN/BLOCKED |
| 新部署 | 最终 SHA/各镜像 digest/配置 hash/单链迁移/冻结清单/隔离恢复成功证据/兼容回退候选 | BLOCKED |

数据范围、目标、密钥读取和离机存储须逐项批准，公开材料只保存引用和哈希。
历史约 10–15GB 档案/30GB 验证空间的规划不是本轮测量；实际大小、压缩率、传输时间、
流量费、供应商支持和价格均 `null`。本轮不查询账号管理页面、不购买服务或向第三方发消息。

## 获批后的恢复程序与失败停止点

1. 操作者确认源/目标、准确审批和固定版本。目标必须是新隔离路径/卷/主机，
   禁止 QQ、模型和社交网络出网；API/Worker/Bridge/Reader/自主能力都不启动。
   容器 `network: none` 或等效受验证策略的证据哈希必须填写，光改应用开关不足以证明隔离。
2. 按批准先停止新增输入和受控执行器，再 Bridge→Worker→API；
   Core 60 秒、Bridge 30 秒 grace period 只是仓库配置，不代表实际停机耗时已验证。
   SnowLuma 和原服务停机权限分别确认。先记录 PENDING/LEASED/SENDING/DELIVERY_UNCERTAIN、
   预算 RESERVED/UNKNOWN、Spool 未确认项、固定 epoch、窗口账本，禁止自动消费历史项。
3. 使用现有 Core `backup` API 保全已提交 WAL。独立保存最新 ErasureLedger 的
   ledger_id、末序号/链尾、hash、时间和一致性边界；预算恢复还须核对 B1 的持久准入账本。
   不能只恢复旧 DB 中的费用/次数而丢掉较新的调用。密钥与密文分离，演练独立密钥恢复。
4. 仅向新隔离目标运行现有 `restore`。迁移单链到获批 head，在发布文件前回放最新 Ledger、
   检查身份/链、integrity/FK、归属冻结、未知费用和发送状态。任何失败均不发布恢复目标。
5. 旧 COMPLETE 已丢归属且仍有活动纠正后继时，保留
   `SOURCE_GRANT_LEGACY_ERASURE_REVIEW_REQUIRED` 阻断；不能猜 grant、删除原证据或写新 Ledger 掩盖它。
   无归属旧来源只生成隔离人审清单，非空时冻结相关处理能力。
6. 核对还原的活跃 Outbox 成为 `DELIVERY_UNCERTAIN`、RESERVED 费用成为 UNKNOWN 且金额保留，
   SENT/CANCELLED 不回 PENDING；恢复 grant 不恢复未来读取权限。Spool/窗口配额必须与最新账本核对，
   不能清库、换 epoch、复制旧 Spool 获得新五次。
7. 记录实际非空行数、密文字节/hash、验证结果、出网计数、耗时/RPO/RTO与操作者签收。
   原服务、整机、QQ 卷分别提供独立恢复证据；空 Core 或合成 SQLite 不满足这些门禁。

目标碰到在线卷、最新 Ledger 无法证明、UNKNOWN 无法解释、意外出网、权限/配置不符、
旧归属模糊、供应商扣费/流量超限或原服务异常时停止扩大动作，保留源、密文与诊断。
不覆盖在线 DB、不 down -v、不 prune、不重发不确定消息、不以旧账本退款或恢复权限。

## 准确停止与回退模板

这里只给未来审批时需填实的模板，本轮未运行。
`$ApprovedComposeArgs` 必须来自同一批准记录的全部 compose/env/project 参数，
先私下比对容器/挂载/网络清单，不将展开配置上传日志。先停止受控一次发送进程，
再执行已批准的本项目服务停止。具体有界 CLI 及停止入口在 B2 最终候选中单独固定。

```powershell
docker compose @ApprovedComposeArgs config --services
docker compose @ApprovedComposeArgs stop onebot-bridge
docker compose @ApprovedComposeArgs stop companion-worker
docker compose @ApprovedComposeArgs stop companion-api
```

停止不能撤回已经许可/发出的 HTTP 请求；保留未知状态并请主人核对手机。
失败回退保留 DB/WAL、Spool、稳定 epoch、最新独立 Ledger、预算/窗口账本和配置/密钥哈希。
只有已证明兼容当前 schema/Ledger 的固定镜像才可申请重启；否则保持停止，
在新隔离目标恢复并重新审查。旧二进制不支持新 Ledger 时不能直接降级在线库。
本轮代码回退只对新开发支线增加可审查 revert，不强推 main；新 PR 合并与支线清理均待准确批准。

## 后续独立决定

非空恢复、候选部署、新五次 QQ 窗口、真实付费 provider、M1 和 Reader G1 六项互不继承。
收费项必须填写 [机器申请表](p8b-approvals.json) 中模型/地区/费率/时区、
硬输入与输出界、单次/日/月/跨月累计费用与次数、并发、账户级供应商封顶和停止人。
单 DB 只能限制经过该 DB 的流量；同一提供方账户有独立调用入口而无账户控制时继续 BLOCKED。
软件预估不能承诺供应商最终扣费，价格/usage 异常须停止新增调用并核账。

严格真人场景仍 0/5、0/20，真实 E2E 成对样本 0、P50/P95=null。
本轮材料完成只形成申请条件，`production_ready=false`。
