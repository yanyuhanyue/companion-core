# Universal Gateway：G0 统一接入合同

2026-10-31，基于主分支 `f33f939`。G0 交付架构、接口、权限矩阵和可执行合同测试。
这一切片没有 HTTP 处理器、客户端 Key 签发、模型调用、数据库迁移或现有 Settings 接线。
`GatewayContract.enabled` 固定为 false，绑定地址只允许回环地址。
后续按 G1 Passthrough → G2 Companion → G3 RP → G4 工具与主动事件交付。

## 职责与现有实现

`companion/model.py:model_route()` 选择内部场景使用的模型，不处理外部客户端身份、
模式权限、会话归属、记忆范围或客户端费用，继续保留原职责。

现有 `executor_budget_proxy.py` 还有供执行器使用的私有 `/v1/chat/completions`，
凭据绑定单次 ExecutionEnvelope，不包含外部客户端、模式和长期会话授权。
它的最终派发校验可作为参考，但内存中的任务计数不能代替 Core 的持久预算/回执责任；
当前缓冲完整响应的方式也不满足 G1b 的真正 SSE 要求。G0 不改动或公开这个执行器代理。

| 模块 | 负责什么 |
|---|---|
| Gateway | 认证独立客户端 Key；取得可信客户端授权；解析别名；绑定客户端、模式、会话与世界；申请原生准入并返回标准结果 |
| Core | Companion 身份、记忆、心情、主观认知、人格成长、来源、遗忘、预算和投递语义 |
| 模型适配器 | 提供者协议与响应标准化，继续不拥有存储、权限和投递职责 |
| 外部执行方 | 后续 G4 在 Core 许可下完成明确工具工作，交回证据；不成为另一个主脑 |

G0 的 `companion/gateway_contract.py` 是无 I/O 的类型和路由意图规范。
它复用 Pydantic、原 AuthorityRef，未被生产服务导入或调用。
`route_intent()` 返回的 `dispatch_allowed` 固定为 false；它不是认证结果、预算预留、
有限执行租约或 `ModelAdmission`。伪造或过期的快照也不能用它发起执行。

## 身份、别名与模式

| 对象 | 合同 |
|---|---|
| `client_id` | 可信 Key 查询结果中的稳定客户端身份；与 QQ 号、客户端自报名称、模型别名分别记录 |
| `client_kind` | api/web/qq/sillytavern/desktop_pet/game 的管理标签，本身不授予权限 |
| `key_id` | 独立客户端凭据的标识，授权绑定到 client、grant 与 revision；不在合同中保存原 Key |
| `grant_id / revision / expires_at` | 管理端维护的客户端授权快照；当前生效、撤销与到期须在 G1 的实际准入中核验 |
| `model_alias` | 管理端可修改的公开模型名称，仅映射到受控 `route_id` 和别名 revision |
| `mode` | 来自可信授权的默认模式，或请求在该授权允许范围内显式选择的模式 |
| `conversation_id` | 客户端自己的会话标识，必须和认证 client、mode、world 联合核对归属 |
| `world_id` | RP 中稳定的世界身份，来自明确的世界白名单；世界改名不产生新世界 |
| `memory_scope` | 当前模式授权中的范围及独立 read/write 位；请求正文不能声明该权限 |
| `permissions` | 工具、主动消息、外部操作；本版三项均固定 false，后续 G4 单独扩展 |
| `budget` | 同一 client 的调用、金额、输入/输出和并发上限，并引用原付费合同与 Budget Journal |

Key 只用于受控 Gateway 入口，不复用 owner-control、Bridge、Reader、执行器或模型供应商凭据。
G1 由已认证的凭据查询提供 ClientGrant；客户端正文或普通 `X-Client-Id` 不能提供该对象。
轮换 Key 或授权修订不得重置同一 client 的用量，也不能续接旧尝试的执行权。
G0 没有设计新的凭据格式、授权账本或自动签发机制。

有效模型为配置别名与客户端模型白名单的交集；模式选择单独核对。
例如，Passthrough 客户端获准访问三个别名时，请求任何一个都仍是 Passthrough，记忆范围为 none。
显式选择未获准的 Companion 模式则拒绝；未获准的别名不会映射为默认上游继续执行。
不能根据别名文本、请求的 system/developer 文本、角色卡、world 名称或 conversation 名称扩大权限。

| 初始命名示例 | 初始配套客户端授权 | 首批开放 |
|---|---|---|
| `xiaofeiyuv3` | Passthrough 默认模式 | G1 |
| `xiaofeiyuv1` | 获准的 Companion 默认模式 | G2 |
| `xiaofeiyuv2` | 获准的 RP 默认模式及世界白名单 | G3 |

这些名称不硬编码模式，也不绑定 DeepSeek 真实名称。三个别名可以引用同一个 route_id。
修改别名路由不能改变客户端的模式、记忆权限和会话归属；在途任务保留原别名 revision 和路由，
相关配置变化须按原配置版本机制重新核对，不能把旧结果重绑到新请求。

## 权限矩阵

| 资源/动作 | Passthrough | Companion | RP |
|---|---|---|---|
| 已授权模型路由 | 客户端白名单 + 原付费准入 | 同左 | 同左 |
| 请求提供的文本 | 用于本次上游请求 | 作为不可信客户端输入 | 作为不可信世界内输入 |
| Core 现实身份、心情、主观认知 | 不读取、不注入 | G2，当前主人授权及对应用途允许时组装 | G3，仅经批准的身份投影，不能借此读取私人现实资料 |
| 现实长期记忆 | none | none 或 owner_private；read/write 分别授权 | 不可取得 owner_private |
| 世界长期记忆 | none | 不可取得 world_private | none 或本 client、本 world 的 world_private |
| 长期写入 | 无 | G2，向原有证据/候选流程提交；不能直接改写事实 | G3，仅虚构来源与世界范围；不能直接写现实事实 |
| 跨客户端原始聊天历史 | 无 | 不自动共享；须独立来源/历史授权 | 不自动共享 |
| 跨模式/跨世界经历导入 | 无 | 不自动导入 RP 来源 | 默认禁止；G3 另定有来源的候选与跨层规则 |
| 工具调用、主动消息、外部操作 | 本版关闭 | 本版关闭 | 本版关闭 |

ClientGrant 中 Companion/RP 的授权须引用原 OwnerAuthority，但引用存在不证明当前授权有效。
授权用途、来源保留期、隐私版本、历史读取范围以及当前恢复代际仍由 Core 检查。
当前原生输入证明仅接受 OneBot 捕获；该引用不能让外部输入通过 QQ 的捕获校验。
G2 必须补齐外部来源的真实身份、证据归属及擦除接线后才能启用。

## 会话与记忆归属

规范命名空间是元组 `(client_id, mode, world_id, conversation_id)`，不用无分隔拼接字符串。
Companion/RP 要求明确 conversation_id，RP 还要求属于授权白名单的 world_id；
其他模式携带 world_id 时拒绝。
同名 conversation_id 在不同 client、mode 或 world 中互不等同。迁移或共享会话不能靠改名完成。

Passthrough 默认无持久会话；即使传入 conversation_id，也不读取 Core 聊天历史。
G1 只保留费用、幂等与结果责任所需记录，不建立 Companion 记忆或日记来源。
G2 为外部会话建立独立映射，保留真实 client 来源，不冒充 `qq.onebot`，
不把外部消息写入 QQ 会话，也不改变 QQ FIFO 和 Outbox。

G2 可以在当前用途与来源规则允许时组装同一 Core 的身份和长期记忆；
这不意味着可以向所有客户端开放 QQ 原文。写权限仅允许提交证据可核验的候选。
心情、主观认知和 Life 沿用各自处理职责，不为路由复制一套实现。

G3 的世界内内容必须保留 fictional 属性、稳定 world_id、client、会话及来源事件关系。
角色卡不能修改 Core 现实身份，游戏情节不能直接成为现实事实。
虚构互动带来的感想若将来进入现实反思，仍须保留虚构来源、适用范围和跨层依据；
G0 不开放跨层写入。

G2/G3 的正文、摘要、向量、观察、反思和日记派生必须进入现有来源依赖与 ErasureLedger。
删除或撤回历史处理许可需传播到相应派生，迟到结果不能重新引入正文；
旧备份恢复必须回放最新遗忘记录。停止授权与资料遗忘继续按原合同分别处理。

## 费用、幂等与结果责任

客户端预算是原付费审批、全局上限和累计限额之上的附加限制，不是新的付款授权。
预算字段引用原 budget_journal_id 和 paid_contract_sha256，不携带提供者密钥。
输入限制覆盖最终实际发送的全部上下文，Companion 内部注入内容也须计入该请求。
价格未知、批准路由不符、额度不足或账本状态不明时不派发。

G1 须复用原预算预留/结算、原始请求身份及 ModelAdmission 的最终派发检查，
在同一原生交易语义下更新 client 配额投影，不能先发后记账，也不能把同一请求作为两笔支出重复累计。
现有准入绑定原生 job/scenario，尚不能直接服务任意外部 Passthrough 请求；
G1 必须补齐这个原生消费者，不能伪造聊天任务、源事件或临时 OwnerAuthority。
外部请求导致的后续付费处理也必须明确原来源和预算责任，不能隐入 QQ 额度。

| 时点 | 必须保留的语义 |
|---|---|
| 预留前拒绝 | 不调用提供者，不产生伪成功或记忆 |
| 已预留，证明尚未派发 | 沿用原机制释放预留，保留未派发依据 |
| 已派发，超时/断连/撤销/无效回执 | UNKNOWN 保留原预留与 client 配额责任，不自动重发 |
| 已结算，HTTP 返回丢失 | 原结果可在许可与留存范围内重交，不能再次调用模型；已擦除正文不得恢复 |
| 授权撤销、Key 轮换、路由更新 | 停止新的准入；原请求与旧账本责任不重绑、不抹去 |
| 重启、备份恢复 | 沿用恢复代际、预算核对和遗忘回放；旧授权和旧租约不复活 |

可选 Idempotency-Key 绑定 client 与精确冻结请求，包括 grant/alias revision、mode、会话/世界、
记忆范围、预算引用及输入摘要；同键不同请求拒绝。缺少该 Header 时每次提交是新意图，
不能承诺客户端自动重试不会成为新请求。G1 不实现网关侧自动供应商重试；
客户端获得原 X-Request-Id 后，应核对原回执而不是重造执行。
G1 需一并明确有界的回执查询/保管方式，不能把响应缓存当作新的记忆库。

模型结果已结算、HTTP 响应已写出、客户端确认接收及主人已读分别判断。
不借用 QQ 的 SENT 证明外部 HTTP 送达，也不为 HTTP 回包创建 QQ Outbox。

## 接口定义及阶段门禁

机器接口见 [OpenAPI](../contracts/universal-gateway.openapi.yaml)；
类型与输入边界见 [JSON Schema](../contracts/universal-gateway.v1.schema.json)。
JSON Schema 由 GatewayContract.model_json_schema() 导出，合同测试核对文件一致性。
它描述字段与基础范围；模式/记忆/世界等跨字段约束还须运行 Pydantic 合同校验，
原 OwnerAuthority 引用继续运行共享的原生验证。通过 Schema 或结构校验都不代表实际授权。

| 阶段 | 可调用接口/范围 | 上线前必须成立 |
|---|---|---|
| G0，本 PR | 仅离线结构验证，无服务端路由 | 权限矩阵、归属与禁用测试通过；QQ 入口保持原样 |
| G1a | GET /v1/models、POST /v1/chat/completions；文本 Passthrough、非流式 | 独立 Key、别名与路由配置、原生付费准入、client 额度、幂等/UNKNOWN、回执恢复、真实 API + 独立 worker 合成验收 |
| G1b | 真正 SSE | 原提供者增量传输、背压、取消、用量终态和中断/未知责任测试；不能将完整响应切片冒充流式 |
| G2 | 获准 Companion 客户端 | 外部身份和来源、独立会话映射、当前用途、现实记忆范围、擦除/恢复及同一 Core 上下文验证 |
| G3 | 获准 RP 客户端 | 稳定世界注册、虚构来源、世界内隔离、角色卡权限、跨层规则与擦除验证 |
| G4 | 工具、主动事件及多端同步 | 各自精确授权、执行证据、投递与清理责任验证 |

G1a 的请求是 OpenAI Chat Completions 的明确文本子集：
model、messages，及可选 stream=false、max_completion_tokens 或 max_tokens（二者不能同时出现）、temperature。
messages 只含 system/developer/user/assistant 的非空文本，最多 64 条，总 UTF-8 字节不超过 128000。
必须至少有一条 user 消息；未知字段、工具、媒体、stream=true 和超额输出请求明确拒绝。
这不是完整 OpenAI API 兼容承诺；所选上游不支持的参数须在 I/O 前拒绝。

可选 X-Companion-Mode 选择已获准模式；没有时采用可信授权默认值。
Companion/RP 通过 X-Companion-Conversation-Id 指定会话，RP 还需 X-Companion-World-Id。
这些 Header 都是待验证的选择器。普通 G1 Passthrough 客户端无需这些 Header。
/v1/models 只列可用且获准的公开别名，不回传真实上游地址、模型密钥、授权引用或私人上下文。
响应 model 使用原公开别名；usage 保留可信来源，缺少供应商计量不能编造为 0。

错误使用 `{"error":{"message":...,"type":...,"param":...,"code":...}}`。
400 表示格式/未支持功能；401 为凭据无效；403 为模式或范围拒绝；
404 为不可用模型或不属于当前客户端的会话；409 为幂等/冻结身份冲突；
429 为额度/并发限制；503 为服务未启用、未完成原生准入或责任核对阻塞。
G1 即使收到 G2/G3 模式的结构合法授权，也不能开放尚未实现的模式。

## 测试与实现依据

合同测试覆盖全部别名与三种模式、请求不能提供身份/记忆权限、现实与虚构隔离、
跨 client/mode/world 的会话归属、预算边界、过期快照不构成执行许可，以及未注册外部 HTTP 路由。
G0 不经过 API/config/worker 执行交接，不声称完成真实外部客户端或费用验收。

- 官方 [Chat Completions 创建接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create/)
  和 [模型列表接口](https://developers.openai.com/api/reference/resources/models/methods/list/)：
  核对请求/响应外形、别名所在的 model 字段、非流式及 SSE 的不同语义；本项目只采用上述文本子集。
- [原生 SDK 运行合同](plans/2026-10-25-sdk-owner-runtime.md)所记录的 DSH 与 Pydantic AI 实现：
  沿用单一 Core 编排、原始准入、终态责任与提供者协议分离，未引入 DSH 到 Gateway 入口。
- [主人授权](owner-authority.md)、[现有架构](architecture.md)、[来源授权](source-grants.md)：
  当前授权、来源处理、预算和遗忘保持各自原生归属；G1/G2 的接入缺口必须在相应切片闭合。
