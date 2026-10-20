# P4 隔离演练、停止与恢复

当前没有生产部署或真实 QQ 测试授权。本轮没有使用既有 Cookie、登录会话、
生产配置、在线容器、DSH/Android/Houdini/ADB。未来操作真实服务前须重新确认目标环境。

## 可复用演练

[p4-rehearsal.example.toml](p4-rehearsal.example.toml) 是可撤销的离线测试配置。
身份与 token 全部合成，模型为 demo、费用为零、主动发送关闭。测试通过显式
`Settings.model_validate(tomllib.loads(...))` 加载，避免 `Settings.load()` 继承生产环境变量。
实际验收由测试 fixture 注入 Mock 模型、ASGI HTTP 和内存来源后端；配置文件本身不启动服务。

全新数据库、独立 ErasureLedger、Bridge Spool 均由 pytest 在 `.local/test-temp` 创建。
测试不会启动生产 AstrBot/NapCat，也不启动旧 Bridge Reader 与新独立 Reader 竞争 lease。
QQ 图片签名 URL 只留内存，不写 Spool/Core；下载队列与文字入件/投递泵分离。
复现命令及各层模拟边界见 [验证记录](p4-validation.md)。

## 停止与撤销

1. 在被明确授权的测试环境暂停自主活动和主动分享，停用本轮新增 Reader；普通聊天由原有
   唯一 Bridge 继续负责。不要启动另一个 scheduler/Agent 或全局退出已有平台账号。
2. 对该环境专用 grant 设 disabled，撤销对应 Core adapter token；凭据仅留 Reader 私密目录。
   这能停止新 I/O，**不能自动删除旧派生内容**（审查 G1）。
3. 若撤销要求删除已有来源，先在已授权 Core 使用 control token 查询
   `GET /internal/v2/observations`，人工核对属于该授权范围的事件清单，再对每个事件调用
   `POST /internal/v2/observations/{event_id}/forget`。该端点通过独立 ErasureLedger
   删除依赖；不要 SQL 直删、猜测 grant 归属或批量清空整库。
4. 保留当前 Core DB、Outbox、UNKNOWN 请求/模型预留和最新独立 Ledger。
   不以旧库覆盖、不做 schema downgrade、不把 UNKNOWN 费用归零，不重置不确定投递。

R1–R3 修复没有新迁移；当前 schema 仍包括 #16 的 0015。不能把运行在旧 0013/0014 的
二进制或只支持两图的旧版本接到当前库，并声称已无损回退。需要程序回退时应先验证
兼容性和停机范围，保留数据库原件；原始存在 R1 的旧 Reader 不是可安全重新启用的退路。

## 加密恢复演练

本轮已在隔离库执行：生成 Fernet 密钥→备份→遗忘真实测试事件 ID→使用旧加密备份和
**当前最新、独立保存的 Ledger** 恢复到新文件→检查 sources、observations、goals、
interest、diary、share、Outbox 的删除/状态、FK 与完整性。密钥不提交或打印。

既有 `python -m companion backup <destination>` 与
`python -m companion restore <archive> <destination>` 使用单独注入的
`COMPANION_BACKUP_KEY` 和当前配置 Ledger。执行前确认 CLI 帮助和隔离配置；
本手册不是生产执行授权。恢复目标应是新路径，不能覆盖在线数据库。

恢复后的在途模型/来源请求保留 UNKNOWN；未确认投递需人工核对，不自动重放。
恢复成功后还应做 sources/派生内容不可见、原 QQ 会话归属和 token/权限复核，再决定
是否在授权窗口切换。此处本地演练 **不等于生产异地恢复**；异地介质、独立 Ledger
传输和持钥方、目标网络/资源/出网限制、真实回退时间仍待独立验收。

## 保留目录

旧保护清单继续引用 `retained-caches-p3.json`、`.local/p3/evidence/cleanup-blocked.json`
及 `.local/vision/evidence/cleanup-blocked.json`。没有重试删除它们。
本轮已被自动审批审查拒绝清理（`blocked by policy`）的目录：

- `.local/test-temp/p4-life`
- `.local/test-temp/p4-reader-security`
- `.local/p4/ruff-cache`
- `.local/p4/pytest-cache`
- `.local/test-temp/p4-bridge-first`
- `.local/test-temp/p4-bridge-second`
- `.local/test-temp/p4-bridge-third`
- `.local/test-temp/p4-focused-final`
- `.local/p4/tmp`

均保留，不提权、不改属主、不换工具规避；本轮台账为 `.local/p4/evidence/cleanup-blocked.json`。
本轮演练数据全部为合成数据；保留缓存不表示生产数据或真实账号发生了变更。
