# 状态机合同（v0.3）

## A. 接收与普通回复（M0）

```text
NapCat -> AstrBot -> authenticated bridge
  | owner/private/echo/contract validation
  +-- REJECTED        [no event row, audit-safe rejection]
  +-- ACCEPTED        [TX: conversations + events + QUEUED job]
       | idempotency: (source, source_event_id) repeated => same event/job
       v
     QUEUED -> RUNNING -> SUCCEEDED -> Outbox.PENDING
                   |             \
                   |              -> FAILED (retry exhausted)
                   -> RETRY_WAIT -> QUEUED
       Outbox.PENDING -> LEASED -> SENDING -> SENT
                       |          |             \
                       |          |              -> DELIVERY_UNCERTAIN
                       |          -> (crash/timeout => DELIVERY_UNCERTAIN)
                       -> RETRY_WAIT -> PENDING
                       -> FAILED / CANCELLED
```

**不变量**

- 事务中要么同时存在 `events` 与应答 `jobs`，要么均不存在。
- `source_event_id` + `source` 具有数据库唯一约束；重复投递不产生新 job 或新的回复。
- 同一个会话一次最多一个 `RUNNING` 的回复任务，按收到的顺序处理。
- `LEASED -> SENDING` 必须在首次网络发送前落库；`LEASED` 过期可安全重领，`SENDING` 过期只能标记 `DELIVERY_UNCERTAIN`。
- 最后一步 `send_message()` 如果成功但 ACK 未提交，绝对不能承诺 QQ 全局 exactly-once。标记 `DELIVERY_UNCERTAIN`，主动检查 QQ 可查询的消息回执；无法确认时默认不自动重发。
- 当模型失败，只有明确为可重试的故障才进入 `RETRY_WAIT`，指数退避并限制最多 3 次。
- Outbox 中所有 `qzone.publish`、`qzone.comment`（未来阶段）都不能由 QQ 私聊发送器领取。

## B. 长期记忆（M1）

```text
CANDIDATE --reject--> REJECTED
   |
   | independent evidence, validated extraction
   v
HYPOTHESIS --owner confirms--> CONFIRMED
   |                            |
   +-- new contradicting evidence -> review -> SUPERSEDED / REJECTED
   |                            |
   +----------------------------+-- forget -> FORGOTTEN + projection invalidation
```

- `HYPOTHESIS` 不可作为确定事实向用户陈述。
- 记忆更正生成一条新的记忆，同时旧记忆变为 `SUPERSEDED`，保持修订历史（忘记请求除外）。
- 用户请求忘记时，除了从 FTS/语义索引移除，还要清除或重建依赖它的摘要、自我特征、缓存；备份到期清理且不可在恢复时重新引入，审计仅保留不含原文的擦除标记。
- 用户明确说“记住”可以提升优先级，但不自动赋予不安全操作权限。

## C. 自我特征（M1）

```text
none -> EXPLORING -> EMERGING -> STABLE
                        |         |
                        +-- FADING <---+
```

- 没有手工种子性格；`traits` 初始为空。
- `EMERGING` 至少有来自 3 次独立会话/活动的有效证据（可配置）；`STABLE` 至少跨 7 个不同日历日并经过冲突检查。
- 本次模型回复自身的语言风格不能单独作为人格形成证据，避免自我重复强化。
- `STABLE` 并非永恒；强度与置信度可递减，且必须提供证据 ID。
- 工具权限与安全合同不在 `traits` 表，也不受模型更新。

## D. 对外发布与审批（预留 M2/M3，M0/M1 禁用）

```text
DRAFT -> WAITING -> APPROVED -> EXECUTING -> EXECUTED
            |          |             |
            +-> REJECTED             +-> DELIVERY_UNCERTAIN
            +-> EXPIRED
            +-> CANCELLED
```

- 仅白名单所有者在 QQ 私聊对**不可变草稿哈希**确认才进入 `APPROVED`。
- 审批默认 24 小时过期，内容变动或目标会话改变立即失效。
- QQ 空间发布、公开评论默认始终要求审批；若无法验证审批态，一律拒绝执行。
- 公开行为的发送状态不确定时绝不盲目重发。

## E. 设备状态（预留 M3/M4）

```text
UNCONFIGURED -> PROBING -> READY -> DEGRADED/OFFLINE
                                  -> READY (fresh health check)
```

- Android 需要内核/容器权限和 App 兼容检查通过才能 READY；失败不影响日常 QQ 对话。
- Windows MCP 离线时不能把工具任务自动改成云端等价的高权限执行。
