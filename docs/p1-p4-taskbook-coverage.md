# P1–P4 v3 任务书覆盖核对

核对日期：2026-10-25。这是源码覆盖与 UI 缺口映射，不是部署或真人体验验收。
任务书为共享交接目录的 `p1-p4-taskbook-v3.txt`。源码基线为 Root `6ba4594`
与 cognition REM `8f073df`；Voice `79cd957`、`467b46b`、`15ec410` 仍是待迁移接入分片。
后续候选改变相应消费者时，应更新该行；不把其他分支已存在的代码当作基线已集成。

唯一最终集成回执仍待集成负责人提供。本表复用已有分片证据，不代替完整 CI。
41 项基础能力及既有页面核对见 [控制台差距报告](p1-p4-implementation-status.md)。
本次只读取 UI 源码，没有重新运行浏览器或修改 UI。

| 任务书项 | 已有原生实现与核对入口 | 尚未闭合的验收或能力 |
| --- | --- | --- |
| P1.1 自然交流 | `conversation_policy.py`、Core context 按明确情境调整指令；默认 OFF，SHADOW 可观察 | 不能由策略代码推断自然度改善；同模型、同历史真人对照仍需回执 |
| P1.2 动态表达与节奏 | `expression.py`、Meme 选择及 Outbox；foreground 优先检查、回复修订屏障 | 完整原生媒体接入仍在收敛；跨情境重复率与节奏体验未验收 |
| P1.3 Affect | `affect.py`、`social.feedback_paused`；明确反馈、衰减、重置、来源撤回；独立 affect purpose | UI 没有有效来源、衰减、重置结果检查面板 |
| P1.4 对照实验 | 原生 evaluation、固定上下文和预算计量分片 | UI 缺创建/取消/评分流程；合成测试不证明自然聊天效果 |
| P2.1 热温冷 | `memory_projections.py`、`memory_context.py` 逻辑分层与上下文选择，沿用单一记忆存储 | UI 缺层级、激活原因及升降温解释；真人跨日效果未验收 |
| P2.2 编码与召回 | MemoryContext、FTS5/BGE、证据有效期、冲突/修订/擦除/恢复链 | 需要使用固定数据对照确认间接提问与 token 收益；不推断复杂图谱已实现 |
| P2.3 共同经历 | `memory_projections.py` 与原生 Goals/Reader/Outbox 回执区分讨论、计划、执行、完成、失败、UNKNOWN、已告知 | UI 缺 Episode/Timeline 与真实回执下钻；不能把计划或日记当完成 |
| P2.4 人格与兴趣 | `interest.py` 分离 owner.preference 与 self.interest；后者只计真实完成探索、独立来源与跨日证据；`growth.py` 处理明确表达反馈，Core 使用有限稳定偏好 | 自主兴趣可进入聊天，但 Life 候选材料尚无 self.interest 输入；行为倾向影响自主选题与长期反馈的完整闭环未成立 |
| P2.5 N1/N2/N3 | `nightly_stages.py` 与原有 Consolidator/Diary/Reflection；selector 调用单步接口；cognition 读取原生阶段 | UI 缺检查点/重试/资格解释；最终组合恢复与长期体验仍需集成回执 |
| P3.1 认知循环 | Wake/Goals/Reader/Life 共享 Core；真实完成的读取可以重新成为 Life 来源 | 失败目标在 Life 候选查询中被排除，未见将失败原因用于下一次选题的材料；不能宣称完整结果驱动认知循环 |
| P3.2 持久规划 | Goals 区分 self 与 commitment；原生等待、重试、取消、完成与不确定状态 | `Goals.accept` 为每个目标创建一个执行步骤；未实现通用多步拆解、根据结果修改步骤计划。单步读取与重试不等于任务书全部规划能力 |
| P3.3 唤醒调度 | `autonomous_selector.py` 串行轮询原生 lane、前台检查、后台单步预算；Life 可 SKIP/DEFER | 兴趣驱动及失败反馈驱动选题仍有上述缺口；最终抢占/恢复组合验收由集成回执证明 |
| P3.4 REM | 默认关闭的独立私人虚构模块、原生账本/来源闭包；REM cognition 元数据分片已有 34 项读取组合测试 | 读取证据不代替生成/恢复验收；本基线未接收后续 REM 生成候选回执，UI 无 opt-in 或记录页 |
| P3.5 情绪认知协作 | Affect pause 可调节自主时机，独立授权，不改变事实或预算；表达偏好与短期状态分离 | 未证明更广泛任务/兴趣/反馈调节对自然度的净收益；PAD 未作为生产能力 |
| P3.6 观察台 | `/internal/v2/cognition` 返回 wake/life/goal/diary/reflection/nightly/model/rem；原始引用、结构化理由、费用、错误与 UNKNOWN 有界可读 | console-v2 未调用 cognition API；没有统一观察台及暂停/恢复/取消闭环；历史完整 allowed_items 未保存时明确 NOT_RECORDED |
| P3.7 外部执行 | SDK Conversation/DSH 边界与原始 usage、执行凭证复用；M0/M1 禁止外部动作 | Bridge/Voice 原生迁移尚未完成；Windows/Android/音乐/Computer Use 须各自实机验收，不能由 SDK 接入推断 |
| P4.1 表达意图 | Life 保存 SKIP/DEFER/研究/私人日记/私聊草稿/QZone 草稿；`diary_life.py` 将原决策写入原生日记 | 基线的 `consume` 由控制 API 调用；自动消费另有 `a34d27a` 分片，需集成后核对。REM 创作记录也不能推断已接入同一渠道消费选择 |
| P4.2 主动聊天 | 真实来源的 Social/Life 草稿、免打扰、冷却、次数、STOP、反施压约束 | 自然接续、幽默、重复抑制仍需真人体验；自主自动消费须以前一行的集成为准 |
| P4.3 QQ 空间 | `social_autonomy.py`、原生计划、独立动作授权、owner_only、过期/去重/原始结果核对 | UI 缺精确内容/受众/有效期审批与 UNKNOWN 对账；未见根据发布反馈学习选题的完整闭环 |
| P4.4 统一身份 | 聊天、日记、REM、Social 均使用同一 Core 与来源/预算边界 | 不能将私人聊天、文档、梦境作为公开来源；虚拟房间与便签按任务书不阻塞本阶段 |
| P4.5 长期调整 | 明确表达反馈与跨日偏好、去重、冷却、暂停机制可用 | 未见跨渠道分享主题/时机/表达方式的长期反馈学习闭环；缺效果对照与真人长期观察 |

## 优先补齐的实际缺口

1. 原生 Voice 消费者：后续 0044 已由 R1 提供，真实 TLS API、独立语音节点、
   STOP/期限、原始结果、清理及 UNKNOWN 的原生域检查已通过。
   Bridge 消费者与最终组合验收继续由集成负责人收敛；不以单个分片推断部署可用。
2. P3.6 页面：当前 `console_static/app.js` 只有原有 Life/任务/配置视图，
   `dream.rem` 等条目是能力 ID 注册占位，不会自动构成 cognition 页面。
3. P3.2 多步规划与 P2.4/P3.1/P4.5 反馈闭环：现有单步 Goals、兴趣投影、Affect 和
   明确表达偏好提供基础，但不能按模块名称将整条能力标成完成。实现新机制前需单独讨论范围。
4. 完整自然度和长期陪伴验收：离线功能测试、模拟 provider、代码存在均不能替代真人体验。

## 核对证据的边界

REM cognition 冻结提交 `8f073dfbe27b039e92eb98579145ec20d13f0fb6`，
读取组合 34 项、Docs 199 项通过；原生历史语音校验依赖顺序为
`79cd957` → `467b46b` → `15ec410`，最后一项保持回复 canonical 摘要一致。
这两类回执只证明各自范围，最终引用一个集成 CI 摘要，不复制另一套组合验收身份。

