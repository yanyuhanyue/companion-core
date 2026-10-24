# 第二阶段：长期记忆与情景接续

独立模块候选 `3df9da6`（含 NM01/NM02 修复）已导入阶段二组合工作树，
并在阶段一 `07016eb` 基线上完成 Core 记忆调用、来源证明和成长提示接线。
本页记录实现与合成验收；组合提交、完整 CI 和部署由集成负责人统一冻结，真实 QQ 尚未验收。

## 已有能力与本次增量

继续使用 SQLite 事实、来源关联、修订、FTS+BGE 和 ErasureLedger。
已有 `Core.extract_once`、独立 `life-worker` 与 `NightlyConsolidator` 负责异步提取、
租约、预算、聊天优先和崩溃恢复。没有新建存储、调度器、模型适配器或关系索引。

本次补齐以下缺口：

- `Memory.episodes` 对最多 20 条单会话来源重新核验，按非空正文来源集合生成稳定 ID，
  在原事务内写情景、来源链接与提取完成标记。重复执行不重复创建情景或摘要；
  原先被隐私处理清除的情景不会因重复编码复活。
- 检索只使用正向、私聊、主人来源；检查完整上游依赖、撤销标记和外部账本是否已应用。
  传入时钟时同时检查原始来源到期、未来时间和事实 `valid_from <= now < valid_to`。
  Embedding 返回后重新核对记录、来源、privacy epoch 和账本，拒绝过时结果。
- 延迟提取的精确旧值若早于主人纠正，不重新进入当前事实。后续推断的冲突单独待核对，
  不把已确认的主人纠正降级。新的显式主人记忆指令仍按既有冲突流程处理。
- 新增 `MemoryContext` 的有界事实/冲突、近期接续、修订历史和只读来源失效计划。
  所有视图从原数据库读取，来源失效后重算；不保留另一份无法遗忘的上下文缓存。

旧情景只是对话摘录。接续读取使用仍有效的原文及其发生时间，不直接信任旧摘要，
也不把主人说过的话视为任务已经完成或助手已经送达的证明。

合法空正文图片/语音/文件和 `message.media_received` 参与同一批次的来源校验。
带正文的媒体可形成摘录；无正文事件随成功批次标记 `memory_processed=1`，不形成文字事实证据。
纯媒体批次返回 `None`，不创建空情景或摘要；重复处理、独立 worker 重启不再消耗提取尝试。
空正文来源的撤回、失效、正文/媒体摘要变化、类型或会话变化仍拒绝提交；任何标记更新均与
同批事实/情景提交共用事务。旧情景若已经链接空正文媒体，接续读取先核验所有来源，再略过空摘录。

## 接线合同

实现位于 `companion/memory.py`、`companion/memory_context.py` 与 `companion/core.py`。
Core 由本模块单一作者接线；API 时钟、配置与其他共享合同由集成负责人接线。

| 接口 | 返回与约束 |
| --- | --- |
| `Memory.search(query, ..., memory_ids=None, now=None)` | 原列表协议不变；指定 `now` 才应用到期/业务有效期过滤，保持旧调用兼容。所有调用都应用来源/账本/并发复核 |
| `Memory.project_recall_candidates(query, ..., now=None)` | 项目回顾兜底同样传时钟，避免绕过到期过滤 |
| `MemoryContext(memory).recall(query, now, actor="owner", memory_ids=None)` | 总候选最多 6；`facts` 保留 CONFIRMED/HYPOTHESIS 标签，`conflicts` 最多 2；返回状态与兼容原窗口合同的 `rows`。展示最多 8 条来源，完整 `evidence_ids` 不截断 |
| `.continuation(conversation_id, now, actor="owner", event_ids=None)` | 最近 7 天、最多 3 个情景，每个最多 3 条 200 字摘录；仅限传入会话。显式 event allowlist 必须覆盖整个情景，空列表返回空上下文 |
| `.history(memory_id, now, actor="owner", memory_ids=None)` | 最多 20 条有有效来源的当前/历史修订；allowlist 约束入口及每个成员。发生、记录和纠正时间保持原样，不推断现实中的生效时间 |
| `.invalidation_plan(event_ids, now, actor="owner")` | 返回来源及真实派生闭包、保留事实及剩余正证据、应退出事实和需重算情景；完全只读，不持久化撤回或删除 |

已接入的调用与约束：

1. `Core.recall_for_reply`、上下文兜底、候选核对、主人记忆命令和 HTTP 记忆查询均传统一时钟。
2. `MemoryContext.recall` 已进入真实回复路径，继续使用现有有预算的相关性核对；候选不是最终答案。
   `NO_RECORD` 仅表示当前授权范围内无命中。`MEMORY_INDEX_UNAVAILABLE`、
   `ERASURE_REPLAY_REQUIRED` 等错误原样传播，不能说成“你没告诉过我”。
3. Core 接续调用为完整情景建立显式来源 allowlist，复用 owner/private 与窗口历史授权，
   在现有字节和上下文额度内保留摘录。只批准事实的窗口不读取情景。候选核对、最终上下文与
   后台提取通过 `pin(now, rows=..., event_ids=...)` / `revalidate(proof, now)` 在 await 前后
   核验完整来源及祖先；实际保留的来源写入 `job_sources`，不在独立主人输入间造依赖边。
4. `/你记得什么 历史 mem_ID` 才读取修订历史；普通聊天只检索当前事实。
   控制命令的动作使用 `control_job` 规则：新输入可抑制旧确认气泡，来源撤回仍阻止动作。
5. 持久撤回由同事务 Ledger 重新计算完整来源闭包、受影响事实并验证剩余独立来源，
   沿用 fsync 与 apply/replay 合同。`invalidation_plan` 只是可选只读预览；若使用，须在事务内
   重算，不能沿用事务外旧结果，也不能取代 Ledger 的完整事实集合、授权或可重放回执。

`ExpressionLearning.snapshot(requester)` 已由 Core 的独立提示 hook 消费。启用需
`expression_learning_enabled`（默认 false）与 `expression_enabled`，仅普通主人私聊；
未截断的 STABLE 项及 `0 < strength <= 0.5` 才能进入提示，同维度短时 Affect 优先。
被上下文裁剪的提示不登记其来源；保留的全部证据及 item version 在延迟提交前重验。
沿用现有 snapshot 即可，不新增成长适配层。通知路径保持原有提前返回。

## 撤回、到期与遗忘

PR #83 的 `raw_retention` 已保留独立有效正证据、让全部失效的事实退出使用，
并覆盖派生纠正副本。原 `event_batch` 会遗忘所有相关事实，不能替代主人撤回的保留语义。

读取来源时同时识别旧备份中缺少 `event_dependencies` 的 QQ 纠正副本：仅在原有
`memory_evidence` / 修订关系和 `ErasureLedger.retention_events` 的准确纠正命令判定成立时，
沿该来源继续校验。较晚创建的 `owner.decision` 及其下游继承原始纠正来源的有效期，
无需等待清理 worker，也不补写关系索引。共享事实的普通独立证据不会因此失效；
没有 QQ 原始纠正来源的原生主人决定仍按自身来源判断。
`tests/test_next_memory_legacy.py` 覆盖清理前、旧备份恢复前后、独立/派生补证、
`search(now=...)` / recall / history 与失效计划的一致性。

复用阶段一已实现的 Ledger-first `input_recall`：单来源/真实派生闭包清除后，
按 A05 规则重算证据；显式 forget 与 source_grant 的原删除闭包保持不变。
本模块不新增账本 record 类型；沿用原撤回入口、乱序 notice 和旧备份重放。

独立测试覆盖撤回后读取合同：唯一来源退出；独立有效来源仍可支持同一事实；
撤回来源及含该来源的情景不再进入上下文。显式遗忘测试实际运行原 Ledger、加密备份恢复，
验证当前事实、旧修订、近期接续均不复活，另一独立事实保留。

## 固定检索评估

公开合成集固定在 `tests/test_next_memory_evaluation.py`；SHA256 为
`a6843aa5c7fa8b8fb2067be16dd90cf63f54329bd8a3c77b6888b28c57ba859d`。
包含跨天间接回忆、休息日锻炼、同名不同语境、同名歧义、时间变化、主人纠正、冲突、
属性歧义和无记录。撤回、自然到期、遗忘及旧备份重放另由生命周期测试检查。

| 指标 | 本地结果 |
| --- | --- |
| 查询 / 必要事实 | 10 / 10 |
| 必要事实找回 | 10/10 |
| 多余候选 | 4，共返回 14 个候选 |
| 带来源 ID、来源类型与时间的候选 | 14/14 |
| 无记录正确澄清 | 2/2 |
| 旧修订 / 到期内容进入当前召回 | 0 |
| 撤回来源 / 遗忘及恢复泄漏 | 生命周期与真实签名 HTTP 撤回断言均为 0 |
| 供应商真实调用 / 实际 usage | 0 / `null` |

保留了同名、同项目及“最喜欢”措辞带来的 4 个多余候选，最终相关性检查仍然必要。
没有修改 FTS/BGE 排名算法，也未据此宣称相对旧基线的质量提升；未引入图或新索引。

## 验证与交付状态

`tests/test_next_memory_worker.py` 使用真实认证 HTTP v1 入站、原聊天作业，随后启动
独立 Python 进程运行实际 `life-worker` 循环。只固定时钟、mock 外部 provider HTTP。
覆盖纯文字、文字加空正文图片、带正文的媒体事件和纯媒体批次。每种场景连续启动三次独立
worker，只有首次产生 1 次 `memory_extract`，混合批次正常事实保留、所有有效来源处理完成，
无 `memory.batch_failed`。纯媒体批次不创建事实/情景/摘要。按现有聊天路径，模拟 chat 调用
分别为 1、1、2、0 次，所有调用均 SETTLED，模拟 usage 每次输入 100、输出 20；
这些不是供应商实际账单。另在真实 Core 提取期间注入来源撤回、正文或媒体摘要篡改，
验证同批事实和处理标记不会部分提交。

当前接线的检查命令和文件指纹由专属 `memory-status.json` 中 verification receipt 记录，
JUnit 保存在该 worktree 的 `.local/phase2-memory-check.xml` 与收尾回归的
`.local/phase2-memory-final-delta.xml`，其唯一失败是新增长历史用例未形成可用历史；
修正夹具后的定向成功结果为 `.local/phase2-memory-history-fix.xml`。复用未变化的成功结果，
121 个不同测试最终均通过。独立模块候选的历史回执单独保留。共享完整 PR CI summary
由集成负责人补充，避免复制不同组合候选的身份。已知 Starlette/httpx 弃用提示属于现有依赖。

`tests/test_next_memory_integration.py` 覆盖实际 HTTP 和独立 Core worker 的完整第九条事实来源、
未展示情景来源在 await 期间撤回、独立证据保留、到期 API/回复过滤、显式历史、严格事实范围，
以及成长提示的开关、短时优先和被裁剪来源不登记。授权历史拼接超过 1000 字时仍能正常回复；
HTTP 查询的原有限额继续由入口校验。后台提取保留仍有效的被取代输入；
其 consolidation 剩余计数由集成方同步验证。已发生调用保留 SETTLED/UNKNOWN 的原责任。

状态：Core 接线与局部合成验收；组合完整 CI、部署、真实 QQ 与真实跨天效果由集成方继续验收。

## P2：温度与经历状态只读投影

`MemoryProjections(memory).snapshot(now, memory_ids=(), episode_ids=(),
current_event_ids=(), used_memory_ids=(), event_ids=None, actor="owner")`
是本片的集成接口，实现在 `companion/memory_projections.py`。每个 ID 集合最多 100 项，
输出顺序稳定、重复 ID 去重。调用方提供已经授权的选中记忆与情景；空选择返回空视图。
`event_ids` 是可选来源 allowlist，必须覆盖整个情景和关联目标，实际完成回执也须在范围内。
本片不修改 Core、API、配置、worker、迁移或 UI；集成负责人可在原记忆读取/控制台入口调用，
不能把投影当作新的提示授权。跨 await 使用仍须沿用原 `pin/revalidate`，不得缓存投影替代来源检查。

- Hot：来源在当前上下文、事实确实被调用方用于本次上下文，或有原生活跃目标、执行不确定性、
  尚未闭合的主人承诺。关注关系采用原 `goal_evidence` 的精确关联，不猜测主题相似关系。
- Warm：没有以上关注条件、但有效来源发生在最近 7 天，复用已有情景接续时间窗。
- Cold：更早的有效证据。温度只用于展示/上下文选择，不改变事实确认、重要性、FTS5+BGE 排序、
  保留期或删除规则；较早的假设仍带 HYPOTHESIS 标签，不会自动成为已确认事实。

经历保留 `discussed`，其每个目标单独投影 `planned/running/done/failed/UNKNOWN/closed`，
避免将多个目标压成一个虚假的完成状态。CANDIDATE 只是讨论，原生取消才投影 closed。
done 同时要求原 Goal/step 完成、完全一致的结果 ID、成功 FETCHED/WATCHED 回执和有效来源闭包；
模拟来源、缺失/不匹配回执、EXECUTION_UNCERTAIN 都不能证明真实完成。目标 `told/closed`
分别表示承诺反馈实际 SENT、以及原承诺终态和执行结果已对应；普通 SENT 回复仅证明那条回复送达，
不能证明目标完成。DELIVERY_UNCERTAIN 和草稿均不算 told。最多读取 20 个关联目标及 20 条回复，
超过时显式标记 truncated，消费者不得把有界结果说成全部历史。

目标来源与事实来源使用不同的原生边界：Memory 事实仍要求 owner 证据；Goal 可以同时引用
owner 输入和真实 observation，后者仍保留 companion 身份，逐项核验私有来源闭包、TTL、撤回和
显式 allowlist。不会将观察伪装成主人发言。执行视图还按原 `goal_step_json` 中的 goal/step ID
读取关联 Reader request 与 reading_attempt 状态，返回 `executions` 中的原 request/step ID。
最多展示 20 项，UNKNOWN 优先且单独标记截断；请求来源也须满足同样的授权及有效性检查。
任何仍可见的 UNKNOWN 执行保持投影 UNKNOWN，即使原目标已因重试耗尽变成 FAILED；
`native_state` 保留真实目标状态。投影不结算、释放或重试这些原生责任。

每次查询复用 `MemorySources`、来源有效期和 Ledger custody/epoch 复核，从同一 Core DB 重建。
没有持久投影或第二事实源，因此没有新增表、擦除 handler 或后台任务；原生删除和擦除重放后重新查询
不能复活记忆。按调用方实际使用传 `used_memory_ids`，不要把“候选被检索到”冒充“已经使用”。

实施前对照与复用依据：

- 对照 `codex/next-memory` 的 `3df9da6`：当前冻结基线已经保有情景编码、旧纠正来源校验、媒体批次
  和 MemoryContext 成果；保留新基线的 async recall，不回退覆盖旧分支。
- 实际检查本机 DSH `dsh-session-persistence/lib/index.js` 的 `validateStoredEvents`
  （SHA256 `cc0b6d3a224133af611b428d5a49020e300f86c3b4ba28037aeb219029bde3eb`）：
  读取时校验已知事件类型并拒绝无法解释的日志。沿用此严格解释原则，Core 继续以自身原生状态与
  来源校验为准，不导入 DSH 的存储或执行器。
- [KI-CO recall gate 固定源码](https://github.com/Kisera001/KI-CO/blob/fe1672bc25e45506018d57fdead51d7ce6d3cc72/src/utils/memoryRecallGate.ts)
  提供有界关注条件的比较；本片使用明确的原生来源/目标关联，不复制词法阈值。
- [YuriOS DreamConsolidator 固定源码](https://github.com/yuri-os/YuriOS/blob/cd4d1f16ae095f0f694dbbe7b1aac8e33db25367/yurios/mind/dream.py)
  区分已结束日及原始经历。本片保留证据发生时间；夜间阶段见下节，虚构 REM 仍属于后续片。

`tests/test_memory_projections.py` 验证冷记忆仍可召回、只读无状态写入、重复选择/重新打开 DB 等价、
原 Goal 状态转换、投递不确定性与 SENT 闭合、模拟/缺失执行证据、来源过期/撤回/上游失效和备份擦除重放。
该接口尚未接入产品页面；完整 HTTP + 独立 worker 合成验收在共享入口接线后由集成负责人执行。

## N1–N3 与实际温记忆接续

夜间阶段使用同一批原事件和已有 Episode，不建立温记忆副本。
N2 只编码尚未被完整有效情景覆盖的来源，保留原摘要；N2 先发生时不更改
`memory_processed`，后续提取复用已覆盖来源并只为新增来源创建情景。
这些情景立即由原 `MemoryContext.continuation` 提供给有权限的上下文接续。
N3 经提取和冲突复核提交的事实仍走原 FTS5+BGE，温度不屏蔽较早的有效事实。
持久单步、共享检查点、预算与擦除合同见 [夜间整合](../design-v0.5/consolidation.md)。

### 实际 Hot/Warm 上下文选择

`continuation` 接受 `current_event_ids`、`used_memory_ids` 和逐来源 `source_check`。
调用方只传本次选中并保留的事实 ID；Cold 事实的有效来源可以使关联情景在本次选择中成为 Hot，
不写入永久热度。当前来源、原生未结目标/承诺/UNKNOWN、最近七天情景分别有最多 20 项候选，
避免近期大量聊天挤掉旧目标。候选 SQL 仅做预选，实际温度继续复用同一投影来源和目标校验。
结果最多 3 个情景，每个最多 3 段、每段 200 字；超出候选或结果上限显式截断。
整段情景必须通过 allowlist 和可选来源校验，任一来源撤回都不能局部复活该情景。

共享 Core 接线还须对新选出的历史来源执行当前 history 授权（含原签名旧记录读取器），
将已使用历史和情景一并计入条数及 UTF-8 字节上限，沿用 pin、实际 job_sources 和最终上下文复核。
如果最终预算裁剪移除了用于激活的事实，调用方也须移除依赖该事实的接续。
本片定向测试见 `tests/test_memory_temperature_context.py`；真实入口与独立 worker 验收待 R1 接线。
