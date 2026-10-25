# P1–P4 v3 任务书覆盖核对

核对日期：2026-10-25。这是源码覆盖与 UI 缺口映射，不是部署或真人体验验收。
任务书为共享交接目录的 `p1-p4-taskbook-v3.txt`。生产/测试核对点为已冻结并发布到
`codex/p1-p4-integration` 的 `765ecb7b42d18c2ea84d957569b7711a0e1d51a2`。
UI marks 基线仍为 `0a1b301`，PR #109、#111 尚未合并。
REM 生成、渠道自动消费、兴趣/反馈材料和 N2/N3 摘要已有原生生产者与消费者。
Voice、account、Meme host、嵌套附件与 Life→REM 均已集成并有对应合成验证。
原始输入/媒体取消/责任回执、Reader URL/source 与生成后投递重核、原视觉结果入 SDK、
最终原图/记忆、整组分段额度和旧 RPC busy 边界已补齐。
完整 CI 已确认 SUCCESS，首次单项超时及同候选复验说明见统一摘要；真实模型/QQ 体验等待用户宣布可测试。

本表的最终结果统一引用[控制台差距报告中的唯一集成回执](p1-p4-implementation-status.md)。
本表复用已有专项证据，完整 CI 状态以该处为准。
41 项基础能力及既有页面核对见 [控制台差距报告](p1-p4-implementation-status.md)。
UI 结论依据当前源码与既有浏览器回执。

| 任务书项 | 已有原生实现与核对入口 | 尚未闭合的验收或能力 |
| --- | --- | --- |
| P1.1 自然交流 | `conversation_policy.py`、Core context 按明确情境调整指令；默认 OFF，SHADOW 可观察 | 不能由策略代码推断自然度改善；同模型、同历史真人对照仍需回执 |
| P1.2 动态表达与节奏 | `expression.py`、Meme host 的目录/发现/选择与 Outbox；foreground 优先检查、回复修订和最终图片投递屏障 | 嵌套附件和 Meme host 已集成；跨情境重复率与节奏体验未验收 |
| P1.3 Affect | `affect.py`、`social.feedback_paused`；明确反馈、衰减、重置、来源撤回；独立 affect purpose | UI 没有有效来源、衰减、重置结果检查面板 |
| P1.4 对照实验 | 原生 evaluation、固定上下文和预算计量分片 | UI 缺创建/取消/评分流程；合成测试不证明自然聊天效果 |
| P2.1 热温冷 | `memory_projections.py`、MemoryContext 已提供逻辑层级、原生上下文接续；有效模型摘要可随 Episode 接续 | UI 缺层级、激活原因、升降温与摘要来源解释；跨日实际效果仍待体验 |
| P2.2 编码与召回 | MemoryContext、FTS5/BGE、来源/冲突/修订/擦除链与最终发送前的原事实/有效性重核；原 EXTRACT 的 salience 1–10 为保留用途估计，importance 为有效估计均值/10，仅同等相关性排序，不改真值或置信度 | 控制台记忆 API 未输出 importance、评分来源或失效解释；真实模型价值估计与召回收益未验收 |
| P2.3 共同经历 | `memory_projections.py` 与原生 Goals/Reader/Outbox 回执区分讨论、计划、执行、完成、失败、UNKNOWN、已告知 | UI 缺 Episode/Timeline 与真实回执下钻；不能把计划或日记当完成 |
| P2.4 人格与兴趣 | owner.preference 与 self.interest 分离；真实完成探索和跨日证据产生兴趣；`life_context.decision_context` 将当前授权来源内的自主兴趣、明确反馈和执行结果传入 Life | 跨日稳定性、自然表现和跨渠道长期迁移仍需验收 |
| P2.5 N1/N2/N3 | 原 Consolidator 四个付费阶段复用 EXTRACT 输出语义摘要与 salience，原事务持久化；MemoryContext 消费来源有效的模型摘要；已有 Episode 与晚到事件按原来源集合分组。Root 已验证真实 HTTP 与三个独立 Life 进程，不增加调用 | 现有 cognition 只展示阶段状态，记忆页面不输出语义摘要/importance；摘要质量与长期用途估计仍未做实际模型验收 |
| P3.1 认知循环 | 原生观察、Wake、Goal、Reader、Life 共用 Core；`decision_context` 纳入 FAILED/UNKNOWN 等原始执行结果，UNKNOWN 要求对账并阻止继续选择；完成结果可成为后续来源 | 有限多步骤续行消费者已接入；模型是否合理利用反馈尚未验收 |
| P3.2 持久规划 | 已集成有限 self-only `read_plan`：1–3 个具体 HTTPS 目标、原生步骤、CONTINUE/WAIT/ABANDON、版本 CAS、原始证据和 UNKNOWN 屏障；三步重排、读取结果投影、原租约 URL 与签名输入来源校验已有专项验证 | Root 已补齐 continuation 入口，并验证真实 HTTP 与两个独立 Reader 进程及迁移专项。有限读取计划不等于任意任务规划；计划质量仍待实际模型验收 |
| P3.3 唤醒调度 | 原 selector 串行轮询原生 lane、聊天优先、后台单步预算；新输入可接纳，原 RPC/SENDING 未结时第二 worker 等待；Life 有 SKIP/DEFER，REM、自动渠道消费、兴趣/反馈材料均有实际消费者 | 最终组合的多步续行与所有抢占/恢复路径仍以统一回执为准；没有新任务时应保持安静 |
| P3.4 REM | 默认关闭的私人虚构生成、原 selector、原账本/来源/擦除闭包已接入；真实签名 HTTP、独立 Life/REM 进程、生成后精确 owner cognition 读取及恢复不重付已有合成验证 | UI 无 opt-in/作品记录页，实际梦境质量未验收；虚构不升级为事实或公开来源 |
| P3.5 情绪认知协作 | Affect pause 可调节自主时机，独立授权，不改变事实或预算；表达偏好与短期状态分离 | 未证明更广泛任务/兴趣/反馈调节对自然度的净收益；PAD 未作为生产能力 |
| P3.6 观察台 | owner cognition API 读取 wake/life/goal/diary/reflection/nightly/model/rem；Goal 返回有界 steps、逐步原始回执和父级 UNKNOWN，第一步 DONE 不代表全计划完成 | console-v2 未消费 cognition API；缺统一观察台与续行/取消操作 UI；阶段状态不是语义摘要或 importance 展示，历史未保存字段不补造 |
| P3.7 外部执行 | SDK Conversation/DSH、原始 usage/执行凭证与原生工具边界复用；Bili 原 v3 视觉结果进入 SDK 上下文，并在最终模型和投递边界重核；Core/Voice/独立节点迁移已验证 | Voice、account、Meme host 与嵌套附件均已集成，已有真实 HTTP、独立 worker/Bridge 及合成传输回执；设备、音乐与 Computer Use 的实机体验仍须单独验收 |
| P4.1 表达意图 | Life 保存沉默、延后、研究、日记、私聊、QZone 草稿和 PRIVATE_CREATION；`consume_due` 在 selector 自动消费明确获准渠道；rem.save 保存创作原文，复用原 REM job/day quota，不增加第二次模型调用 | Life→REM 保存与 cognition 读取已通过真实签名 HTTP、独立 Life/REM 进程验证；原文/来源变化或撤回隐藏记录，保持只读与原 activity 费用。UI 缺精确渠道自动同意编辑与结果追踪 |
| P4.2 主动聊天 | 真实来源、明确反馈与执行结果进入 Life，原生自动消费、免打扰、冷却、次数、STOP 和反施压规则已接入 | 缺自然接续、幽默、重复率与时机的实际模型/真人验收；合成内容只验证机制 |
| P4.3 QQ 空间 | 原生候选、草稿、独立动作授权、owner_only、有效期/去重和原始 ACK/UNKNOWN；`SocialAutonomy._messages` 纳入实际 execution_outcomes | UI 缺精确内容/受众/有效期审批与对账；不能从投递回执和上下文消费推断内容选择质量已改善 |
| P4.4 统一身份 | 各渠道共用 Core；Life 读取经授权的兴趣/反馈/结果，QZone 读取其允许的公开来源及执行结果 | 保持渠道用途与隐私隔离；私人 Memory 仅在原授权用途内使用。虚拟房间/网页便签仍按任务书后置 |
| P4.5 长期调整 | 明确表达反馈与跨日偏好、去重/冷却/暂停已有实现；Life 和 QZone 已消费有来源的反馈或执行结果 | 跨渠道长期主题、时机、表达选择的稳定改善尚无实际模型/长期真人证据 |

## 当前集成状态与剩余验收

P4.1 的 Life→REM「保存梦境或创作」及所有本轮原生 host 已集成；作品保持私人虚构与原始来源约束。
现阶段剩余项为既有 UI 缺口、部署验收，以及用户宣布可测试后的真实模型/QQ 体验。

1. Voice ASR/TTS、Meme 目录/发现/选择、account 动作、嵌套附件与 Bridge dispatch 已有合成组合验证；profile 未授权轮询和 account ACK handoff 已有修复回执。
2. console-v2 尚无 cognition 页面、importance 或模型摘要解释；41 项基础能力标记及既有 18 项自主调整范围沿用先前核对，本轮仅更新报告。
3. 有限三步 Goal、兴趣/反馈及失败/UNKNOWN 消费者已有实现；实际模型的选择质量、自然度和长期陪伴效果待体验。
4. 完整 CI 统一引用控制台差距报告中的集成运行，attempt 2 已确认 SUCCESS；平台通过与跳过数、浏览器检查及首次超时说明均以同一回执为准。

## 最新边界与验证证据

| 已集成增量 | 生产者、消费者与保留语义 | 专项验证边界 |
| --- | --- | --- |
| 原始输入及冻结责任 | `owner_input_state` 校验原签名、正文/身份/时间、admission 和冻结 `owner_delivery`；按原 event/job 身份找回责任 | 原投递 origin 为空或改绑会被拒绝；合法已付费后的擦除/epoch 推进可读回；费用、UNKNOWN、terminal 与未结责任分别保留 |
| 原生媒体取消与恢复 | `pending_media` 在最终 HTTP 委托前重核原输入状态；已取消 CHAT 停止后续媒体 I/O，擦除后凭绑定回执清理对应 pending | 新输入、撤回、上传/下载中断与恢复均走真实入口；权限 403 或篡改回执不清理；真实模型 UNKNOWN 不因本地取消结清 |
| Reader URL/source | `reading.py` 复用原租约 URL 摘要和保留签名输入来源，每次验证重新核对 | URL 或来源身份变化拒绝读取结果；晚到已知/UNKNOWN 计量、重复回执与预留责任保留 |
| Reader 生成后投递 | `owner_delivery._sources` 在回复记录、lease、begin-send 与 send-check 调用 `agent_reading.revalidate_results(..., executing=False)`，复用保存的 reader snapshots | 结果正文、证据或原来源绑定变化拒绝投递；已结模型费用保留；结果正文/来源变更与合法 Bili/ACK 组合共 9 项通过 |
| Bili/Reader 原始结果上下文 | `agent_reading` 以 `reading_attempts.result_event_ids_json` 的原 v3 输出身份装配结果；`agent_runtime` 在最终模型传输和 Conversation 保存前复用重核，`bili_video` 校验原阶段分析/摘要/usage/回执 | Tools 33 项相关检查通过；已结部分结果可保留，PENDING/UNKNOWN 不能冒充完成的视觉分析；既有 deferred continuation、快照与计量边界保持 |
| 分段整组额度 | `owner_delivery.check_delivery` 复用原 quota 与 immutable reply_group，对剩余未记账段作只读总量预检 | 窗口/日/月/account 额度不足时首段拒发；重启 ACK 前缀不重复计数，send-check 幂等；实际尝试仍由原账本逐次记录 |
| 最终原图与记忆 | 原始图片证据及实际选中记忆的事实/有效性贯穿模型与发送检查 | 原图、记忆或来源变化会触发相应屏障；SDK 与独立 worker 的组合检查复用真实入口 |
| 原 RPC busy | Core 原领取事务复用未结模型与 SENDING 检查 | 新输入继续接纳；原 CHAT 取消不会提前释放在途 RPC，第二 worker 等原责任结清；Root 的真实 burst 与原图组合 10 项通过 |

N2/N3 的独立 nightly、Goal HTTP/独立 Reader、Life→REM 与 cognition、Voice Node→Core→Bridge、
Meme/account host 和嵌套附件的专项证据均保留。历史迁移 fixture、原始失败 XML 与后续修复检查
分别留存；最后的 Bili followup 与历史/额度/分段测试提交已审查并纳入冻结候选，Docs 199 项通过。
完整 CI 只引用同一处集成摘要与最终回执。首次单项 Linux 等待超时在本地 4 次及同候选 CI 复验均通过，
生产代码和原 20 秒断言未改，根因未确认。平台跳过项及独立浏览器检查分别记录；
现有合成验证不作为真实模型、QQ、设备或长期体验验收。

## 最终候选刷新结构

以下结构对应上述冻结候选，完整 CI 已确认 SUCCESS；UI 缺口和体验验收分别保留。
现有可移除实现标记继续与运行状态、实际授权和自主调整消费范围分开。

| 范围 | 生产者与实际消费者 | 必查原始字段或证据 | UI 核对与未完成条件 |
| --- | --- | --- | --- |
| 有限多步 Goal | 原生 Goal 创建/续行、Reader、Life 续行消费者 | ordered steps、WAITING_DECISION、版本 CAS、逐步来源和结果、父状态、UNKNOWN、撤回/恢复 | cognition 只读不等于完整操作 UI；缺续行/等待/放弃入口时明确记录；普通多步规划不由 1–3 步 read_plan 推断 |
| N2/N3 | 原模型输出、原生持久化、Core continuation/召回 | 来源快照、摘要资格、salience、资格失效、实际上下文引用及擦除回放 | 缺阶段/摘要来源解释时单列 UI 缺口；只有字段或读取代码不能算生成链已完成 |
| 兴趣与反馈 | 真实完成探索、明确反馈、Life/QZone 后续选择 | 独立来源/日期、相反反馈、选择前后变化、原用途授权 | 区分主人偏好、自主兴趣与长期行为；已接入消费者与未验收的长期选择效果分别记录 |
| Cognition | 原生保存记录、owner 控制读取 | steps、verified_completed_steps、每步回执/截断、来源撤回、STOP 后读取 | console-v2 无实际请求/页面时保持 UI 缺口；不把后端 API 或占位能力 ID 写成页面已交付 |
| REM | 独立默认关闭的生成、原账本、私人读取 | FICTION、未验证问题、有效来源、独立进程恢复与费用保留 | opt-in、记录与来源撤回展示分别核对；不得把虚构投影当事实或公开来源 |
| 原生媒体/Voice | 原签名捕获、Core/独立 worker、OwnerOneBot host 与已集成 Bridge dispatch | schema3 binding、原 authority、原始输入/媒体取消回执、最终原图与 send-check、ACK/UNKNOWN、清理 | 合成 dispatch 已验证；UI 操作与用户宣布可测试后的真实 QQ/节点体验分别验收 |

每行最终只引用同一份集成 CI 回执，并分别记录“代码已接入”“UI 已接入”和“真人验收”。

