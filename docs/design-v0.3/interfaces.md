# 模块接口与权限边界（v0.3）

## 1. 单一事实源

| 领域 | 权威模块 | 其他模块权限 |
|---|---|---|
| QQ 消息/消息发送回执 | AstrBot Bridge | Core 记录标准事件，不能直接拿 QQ Cookie 调用发送 |
| 会话/事件/Outbox | Companion Core | AstrBot 只提交事件、领取授权出站消息 |
| 长期记忆/自我模型 | Memory + Self Model | DSH、QQ 插件只读经授权的投影，不能直接改库 |
| 聊天用模型决策 | Conversation | AstrBot 对 owner 私聊不再调用自身默认 LLM |
| 工具权限/对外审批 | Policy | 任何插件都不能绕过策略层直通外部账号 |
| 模型预算 | Budget/Usage | 任务工具用独立凭据/配额，不允许隐性绕账 |
| DSH 执行会话 | DSH Worker | Conversation 只能提交明确授权的任务 |
| Android App | Android Adapter | 不持有 QQ 登录态、记忆 DB 写权限 |

## 2. Python 领域接口（设计契约，不宣称可直接运行）

```python
from dataclasses import dataclass
from typing import Protocol, Literal, Sequence

@dataclass(frozen=True)
class Principal:
    actor_id: str
    scope: Literal['private', 'internal', 'public']

@dataclass(frozen=True)
class Envelope:
    event_id: str
    source: str
    source_event_id: str
    event_type: str
    conversation_id: str
    actor: Principal
    payload: dict
    trace_id: str

@dataclass(frozen=True)
class MemoryHit:
    memory_id: str
    subject: Literal['user', 'self', 'relationship', 'project']
    content: str
    status: Literal['HYPOTHESIS', 'CONFIRMED']
    confidence: float
    evidence_ids: tuple[str, ...]

class EventStore(Protocol):
    async def append_once(self, event: Envelope) -> tuple[str, bool]: ...

class MemoryRepository(Protocol):
    async def search(self, requester: Principal, query: str, limit: int) -> Sequence[MemoryHit]: ...
    async def confirm(self, requester: Principal, memory_id: str) -> None: ...
    async def correct(self, requester: Principal, memory_id: str, new_content: str) -> str: ...
    async def forget(self, requester: Principal, memory_id: str) -> None: ...

class SelfModel(Protocol):
    async def observe(self, evidence_event_id: str) -> None: ...
    async def snapshot(self, requester: Principal, max_traits: int) -> dict: ...

class ContextBuilder(Protocol):
    async def build(self, conversation_id: str, text: str, budget_tokens: int) -> dict: ...

class ChatModel(Protocol):
    async def complete(self, messages: list[dict], *, trace_id: str) -> dict: ...

class BudgetGate(Protocol):
    async def reserve(self, scenario: str, estimate_micro_usd: int) -> str: ...
    async def commit(self, reservation_id: str, actual_micro_usd: int) -> None: ...

class OutboxService(Protocol):
    async def enqueue_once(self, event_id: str, action: str, content: dict) -> str: ...
    async def lease_private(self, sender_id: str, limit: int) -> list[dict]: ...

class ExternalToolRunner(Protocol):
    async def run(self, task_id: str, capability_token: str, request: dict) -> dict: ...
```

> 初期实现均在同一 Python 项目中；跨进程只传递版本化 HTTP / JSON。抽象接口的目的是保证未来替换 AstrBot/DSH/数据库时不改领域逻辑。

## 3. HTTP 合同（具体可参见 contracts/openapi.v1.yaml）

| Endpoint | 阶段 | 调用者 | 作用 |
|---|---|---|---|
| `POST /internal/v1/inbox/events` | M0 | AstrBot Bridge | 身份验证、归一化事件、幂等入库并创建 job |
| `POST /internal/v1/deliveries/lease` | M0 | AstrBot Sender | 租借待发送消息，不返回未经审批的公开内容 |
| `POST /internal/v1/deliveries/{id}/begin-send` | M0 | AstrBot Sender | 原子进入 SENDING，发送前必须调用 |
| `POST /internal/v1/deliveries/{id}/result` | M0 | AstrBot Sender | 回执成功、可重试失败或状态不确定 |
| `GET /internal/v1/health/live` | M0 | 运维 | 进程存活 |
| `GET /internal/v1/health/ready` | M0 | 运维 | 数据库/迁移状态 |
| `GET /internal/v1/ops/status` | M0 | 运维 | 队列、模块、预算概览（不返回隐私原文） |
| `POST /internal/v1/memories/search` | M1 | 内部对话模块 | 带可见域权限的记忆检索 |
| `POST /internal/v1/memories/{id}/decisions` | M1 | Owner，经 AstrBot 验证 | 确认、纠正、忘记 |
| `POST /internal/v1/tasks` | M3 预留 | 核心策略层 | 授权创建 DSH/设备任务 |
| `POST /internal/v1/approvals/{id}/decision` | M2+ 预留 | Owner 经认证 | 针对不可变草稿哈希审批 |

### 接入鉴权

1. API 不映射到公网端口；仅内部 Docker 网络或 Unix Socket 可达。
2. Bridge 请求必须携带 Bearer Token；核心自行比对白名单 QQ 账号和私聊会话，不能只相信客户端声称的 `actor.role=owner`。
3. `source_event_id` 必须稳定、唯一；OneBot 归一化时使用 `bot_account_id:conversation_id:platform_message_id`，不能仅用可能复用的序号。
4. Header `X-Idempotency-Key` 与规范化事件绑定；相同键不同内容返回 409。
5. 核心不接受来自网页/帖子/LLM 文本的自我授权标志。
6. 所有跨模块调用附 `trace_id`，日志仅保留脱敏状态与长度等元数据。

### 入站成功响应示例

```json
{
  "event_id": "evt_01K0000000000",
  "job_id": "job_01K0000000001",
  "duplicate": false,
  "state": "QUEUED"
}
```

重复事件应返回同一个 `job_id`，但 `duplicate` 为 `true`。

### Outbox 租约与反馈

Outbox 租约只对经过身份校验的 AstrBot Sender 开放。Sender 领取消息后通过 `context.send_message(UMO, MessageChain)` 发送；其 API 的成功返回不一定等同于收件人最终已读，所以系统只记录“平台接受”或“发送状态不确定”，不声称已读。

- 领取 `LEASED` 返回一次性的 `lease_token`；真正调用 QQ 发送前必须 `begin-send` 原子写入 `SENDING`。过期 `LEASED` 可以安全重领。ACK 必须绑定当前 token。
- 发送前发生明确可识别的网络故障，可以进入 `RETRY_WAIT`。
- `SENDING` 阶段发送调用过程中丢连接或进程崩溃，标记 `DELIVERY_UNCERTAIN`，先核对服务端消息回执，**不默认重试**。
- 不可变 `delivery_key`：`reply:{event_id}:v1` / `proactive:{activity_id}:{content_hash}`。

## 4. AstrBot 桥接伪代码

```python
# 伪代码：具体事件接管/冲突插件顺序，按锁定的 AstrBot 版本做集成验证。
@filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
async def on_private_message(self, event):
    if not self.is_owner(event.get_sender_id()):
        return
    if self.is_bot_echo(event):
        return
    event.stop_event()   # 本 QQ 私聊的默认 AstrBot LLM 不再执行
    normalized = normalize_to_companion_v1(event)
    await self.client.ingest(normalized)

async def sender_poll_loop(self):
    while self.running:
        for msg in await self.client.lease_deliveries(self.sender_id):
            await self.send_and_commit_receipt(msg)
        await self.wait_for_next_poll()
```

注意：`stop_event()` 的触发优先级、其它插件的冲突及异常分支需要 M0 集成测试，伪代码不足以保证在所有 AstrBot 版本中都能无副作用接管消息。推荐在 owner 私聊中显式关闭 AstrBot 默认 Agent，以实现**单一回复所有权**。

## 5. 上下文与记忆的接口规则

上下文构建顺序：稳定安全/身份边界规则 → 当前事件 → 最近少量对话 → 与本轮主题相关的记忆 → 必要的自我模型摘要。由于前缀缓存通常更利于稳定前缀，运行时不要每轮更改系统规则。检索结果本身按不可信数据引用，而不是提升为更高的指令权限。

默认只注入：最多 6 条相关记忆、最多 5 条自我倾向、最多 12 条近期消息。超过 Token 软预算时优先移除低相关、未确认或过时的记录。明确的当前消息及用户最近确认的边界不得被“压缩丢失”。

## 6. 模型适配与用量统一

`ChatModel` 应提供 OpenAI-compatible Adapter；正式运行主要使用 `deepseek-flash`。DeepSeek 官方文档在 2026-10-19 将旧 `deepseek-v4-flash` 列为兼容别名，配置应改用新名称。

每次请求：预算预留 → 模型调用 → 以 provider `usage` 为准结算 → 写账本 → 解除多余预留。若调用超时且不知是否被服务商计费，则记录 `is_estimate=true`，不得虚构零成本。

M0/M1 的 DSH、Android、QZone 均关闭；未来启用 DSH 时应使用它自己的独立执行身份、会话目录、模型配额与工具审批接口，不能假定对话模型网关自然计量到 DSH 的所有用量。
