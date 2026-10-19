# 数据合同、保存语义、安全与恢复

## 1. 数据表归属

M0 表：`principals`, `conversations`, `events`, `jobs`, `outbox`, `approvals`（未来功能占位）, `model_usage`, `audit_log`。

M1 表：`episodes`, `episode_events`, `memories`, `memory_evidence`, `memory_revisions`, `traits`, `trait_evidence`, `conversation_summaries`, `memories_fts`, `privacy_erasure_ledger`（外部擦除日志的本地投影）。

SQL 字段及检查约束见 `db/schema.sql`。所有 ID 为稳定且不可复用的随机 ID/ULID；所有 `*_at` 为 ISO8601 UTC；用户显示时间只在展示层转换。

### 关键字段解释

- `source_event_id`：可靠的去重依据，不是临时任务 UUID；需要由 `bot_id + platform_conversation + provider_message_id` 组成。
- `privacy_scope`：`private/internal/public` 是传播权限标记；即使当前只有 owner，也不能把 `internal` 的工具输出发送到 QQ 群。
- `memories.subject`：`user/self/relationship/project` 分离，防止把用户喜欢的内容写成自己的偏好。
- `memories.origin`：`user_confirmed/event_observed/model_inferred`，可以直接区分事实、观察和推测。
- `memory_evidence.polarity`：正/反证据都需要保留，便于下次冲突判定。
- `traits.independent_sessions / independent_days`：兴趣形成的统计基础，不使用一次模型输出冒充多份独立证据。
- `outbox.content_sha256` + `approvals.content_sha256`：一旦公开内容改变，审批必须重新获取。
- `model_usage.billed_micro_usd`：费用使用整数微美元，不使用浮点数作为金钱账本的持久类型。

## 2. 事务规则

- **入站原子事务**：验证后，`INSERT conversations`、`INSERT events`、`INSERT jobs` 同一事务提交；发生唯一键冲突时读取已有 job，不能创建第二个。
- **生成回复**：模型网络请求不在数据库事务内；完成后在事务中设置 job、插入 Outbox 的唯一 delivery_key。
- **发出**：Outbox 租约使用原子条件更新，只有一个 Sender 拿到当前消息；网络发送前落库 SENDING 状态；ACK 使用租约 token 和当前状态做 CAS。
- **记忆修改**：修订 `memories`、更新 evidence、清理 FTS、对受影响 summaries/traits 打失效标记在一致事务内；较贵的重建使用单独可恢复 Job。
- **审批**：私聊消息只构造提案；单独鉴别 Owner 审批，目标/正文/媒体哈希不可变。

## 3. SQLite 配置

每一个连接都执行 `PRAGMA foreign_keys=ON` 和 `busy_timeout=5000`；数据库设置 `journal_mode=WAL` 和 `synchronous=FULL`。WAL 必须位于本机文件系统，不能直接放在 NFS/SMB/Tailscale 挂载目录。写入是单写者：短事务、限制写者并发，必要时给 Worker 一个单写队列。

`db/schema.sql` 是独立参考 DDL，**不能用 CREATE IF NOT EXISTS 代替数据库迁移**。真实升级使用 Alembic 或带版本号的可回滚迁移，并测试空库与旧版库。启用 FTS5 后，要为忘记、纠正和恢复提供重建/一致性检查。

## 4. 数据留存与遗忘

- 不将 QQ Cookie、模型密钥、Windows MCP Token 写进数据库、会话上下文或日志。
- 原始聊天的初始保留期在 YAML 中设为 365 天，这是可修改运营默认值，不代表任何隐私承诺。
- 保存长期记忆之前先判定价值与可见范围，私密身份信息和未经请求的第三方资料默认不自动进入长期记忆。
- 用户“忘记”某条记忆时必须清除：活跃记忆正文、FTS索引、相关摘要、特征证据、Context 缓存。必要时进一步删除承载该内容的原始事件。
- **备份不能原地撤销**：需要在加密备份之外另建小型、持久化的 `erasure ledger`（仅记录目标ID和擦除版本，不存被忘记的原文）。恢复旧备份之前必须重放所有后续擦除操作，未完成重放则不能打开聊天服务。
- erasure ledger 自身需要单独备份且权限隔离；备份过期清理策略必须明确，不能承诺对已经外泄的副本进行远程删除。

## 5. 信任边界

| 输入来源 | 信任等级 | 允许动作 |
|---|---|---|
| Owner 已鉴别的 QQ 私聊 | 用户级指令 | 申请工具授权；仅在策略允许后执行 |
| B站/小黑盒页面、消息图片文字 | 不可信内容 | 摘要、候选素材；无权修改策略 |
| 模型输出 | 建议/候选，不是权限 | 生成回复草稿、记忆候选、工具请求 |
| DSH/Android/Windows 工具结果 | 受来源标记的数据 | 记录事实回执；不可修改政策 |
| 配置与管理员审批 | 最高系统权限（仍需认证） | 修改预算、发布规则、工具能力 |

## 6. DSH 特别风险（M3 前必须评估）

官方 Python SDK 使用独立 `dsh_home` 与 `cwd`，但 `sdk-minimal` Shell 默认仍可修改运行用户能够看到的路径。容器必须是非特权用户、最小只读挂载、独立文件系统和凭据；不要挂入 AstrBot 数据卷或记忆数据库。需检查 `session-log-deepseek.enabled` 默认日志贡献设置，根据隐私策略决定显式关闭。

## 7. 安卓共置的预留条件

`features.android=false`。启用前检查宿主内核 Binder/binderfs 权限、架构兼容、视频播放能力、CPU/内存/磁盘余量和应用登录风控。即使 Android 无法运行，也不得影响 Companion API/QQ 的健康；其 IP 仍是 VPS 出口，不会因 Tailscale 自动变干净。

## 8. 中文全文检索的额外边界

SQLite 默认的 FTS5 `unicode61` 分词不等于可靠的中文分词。示例 DDL 特意使用 `trigram` 支持至少 3 个 Unicode 字符的中文子串检索；对 1–2 个汉字的短查询必须提供限定范围的 `LIKE`、结构化字段或可选的分词/语义检索回退。CI 必须核验 VPS SQLite 编译版本是否支持该分词器。
