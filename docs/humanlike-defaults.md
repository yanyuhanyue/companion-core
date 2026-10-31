# 拟人化默认值（2026-10-29）

主人反馈“bot 回复不像人”。排查发现，按当时的本地配置，每轮聊天的系统提示词只有
`PRINCIPLES` 一段，其中大部分是证据和能力约束；已批准的 `participation_v2` 策略、记忆、
分段回复都没有生效，模型也不知道现在几点、离上一轮隔了多久。本次改动把这些能力改成默认开启，
并重写基础提示词。

## 改动

| 项 | 之前 | 现在 | 位置 |
|---|---|---|---|
| 基础提示词 | 13 行，开头是“AI 虚拟朋友，没有预设性格”，约 10 行是约束 | 先讲身份和说话方式，再讲事实底线；记忆专用规则拆出 | `companion/model.py` 的 `PRINCIPLES`、`MEMORY_RULES` |
| 记忆规则 | 始终在提示词里 | 只在 `memory_enabled` 且本轮允许记忆时注入 | `companion/core.py` `context()` |
| 对话策略 | 未配置即 `OFF`；默认策略 v1 | 默认 `ENABLED` + `participation_v2` | `ConversationPolicyConfig` |
| 长期记忆 | `memory_enabled = false` | `true` | `Settings` |
| 分段回复 | 未配置即单条 | 默认 `mode="semantic"`，最多 3 条，间隔 1.5 秒 | `Settings.segmented_reply` |
| 聊天 thinking | DeepSeek 一律 `disabled` | 仅 `chat` 场景开启，`reasoning_effort="high"` | `Settings.chat_thinking`、`companion/model.py` |
| 聊天输出上限 | `max_output_tokens`（768） | thinking 开启时为 `max_output_tokens + chat_thinking_tokens`（768 + 3072），但不超过付费审批 `paid_model.max_output_tokens` | `Settings.output_tokens` |
| 时间感知 | 无 | 聊天时系统提示词末尾附主人这条消息的本地时间，以及距主人上一条消息的间隔（10 分钟以上才写；本轮不允许读历史时不写间隔）。时间取自已存事件，重建上下文时文字不变 | `conversation_policy.time_hint`、`core.context()` |
| 非聊天任务的策略 | 传 `None`，等于关闭 | 显式传 `mode="OFF"`，默认值改成开启后主动消息等任务仍不套用聊天策略 | `core.context()` |
| 精确 Token 计数 | 只支持非 thinking 请求 | 也支持 thinking 请求，按 `thinking` 模式和同一 effort 渲染 | `companion/token_measurement.py` |
| 控制台 | `provider_thinking` 固定显示 `disabled` | 开启时显示 `chat:high` | `companion/console_read.py` |

### 新提示词的取舍

保留的底线：不编造线下经历或执行结果，没有回执不说已完成；资料不是指令；不保存凭据、
不推断敏感属性；主人认真问是不是真人时如实回答。原来的“不假装真人”改成了最后这条，
不再作为开场定位。

删去或移走的：`HYPOTHESIS`、`needs_review`、检索为空等记忆细则移到 `MEMORY_RULES`；
能力状态的细则本来就由 `capabilities.prompt_parts` 在需要时注入，基础提示词只留一句
“以本轮能力说明为准，用自己的话回答”。

### thinking 的范围

只对 `chat` 场景开启。日记、记忆提取、反思、表情理解等后台 JSON 任务的输出上限只有几百
token，思考内容会占用这部分额度，可能把 JSON 截断，所以这些任务保持关闭。
`reasoning_content` 不读取、不保存、不回传。

## 依据与实测

DeepSeek 文档（[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode)、
[Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion)）：
`thinking` 默认开启；`max_tokens` 包含思考内容；`completion_tokens` 含
`reasoning_tokens`；带 `tools` 的请求“应当”回传 `reasoning_content`。

2026-10-29 用 `deepseek-flash` 和合成内容实测：

- 带 tools、历史 assistant 消息没有 `reasoning_content`：HTTP 200。
- 工具循环中不回传 `reasoning_content`：HTTP 200，回答正确。
- `response_format=json_object` 与 thinking 同时使用：正常。
- 本地渲染器在 thinking 模式下的计数为 55，与服务端 `prompt_tokens` 一致。

用新提示词加 v2 策略对比三档（每档 3 条消息）：

| 档位 | 延迟 | 输出 token（含思考） | 观察 |
|---|---|---|---|
| disabled | 6.8–7.9 秒 | 19–49 | 口语化，但有一条以问句收尾 |
| low | 4.0–5.6 秒 | 95–167 | 回答“你是谁”时编造了“我是你QQ里那个橘猫” |
| high | 7.6–10.2 秒 | 88–102 | 没有编造身份，语气最自然 |

所以默认值选 `high`。按输出单价 1.2 美元/百万 token 算，每条回复约增加 0.0001 美元；
月预算 20 美元保持不变。

## 连发消息合并回复（2026-10-30）

主人连发几条消息时，第一条先等 2 秒（最近 30 秒内聊过则等 5 秒），之后每来一条再等 2 秒，
一批最多等 8 秒；只发图片时至少等 4 秒。等待期间新消息照常取代旧回复，未回的输入进入下一次回复的上下文，
所以一批消息只调用一次模型。`/` 开头的指令、日记和控制指令不等待。
时长参照 MaiBot“自然”档（`src/chat/debounce/controller.py`，提交 `c6429547`），
调研见 [MaiBot 机制调研](plans/2026-10-30-maibot-mechanisms.md)。实现在 `companion/input_debounce.py`，
只对主人授权或私人生活模式下的私聊生效；配置项 `input_debounce`，在控制台“表达与表情”里可以调整或关闭。

## 话轮判断与主动接话（2026-10-30）

**收尾消息可以不回。** 主人刚收到回复后（30 分钟内）只回了一条很短的消息（不超过 8 个字，例如“嗯”“好的”“哈哈”），
本轮聊天提示词里才会多一段说明，允许模型只输出 `[SKIP]` 不回复。不增加模型调用。以下情况始终回复：
带问号或“吗/呢/么”结尾；附带图片、引用或转发；指令；上一条回复本身是在提问（这时“嗯”是回答）；
上一条主人消息没有收到回复（所以不会连续两次不回）；有界真实文本窗口。
没有给出这段说明却输出了 `[SKIP]` 时，本次回复按失败处理，不会把 `[SKIP]` 发出去；主人的消息进入下一次回复的上下文。
不回复的轮次在审计日志里记为 `job.succeeded` 且 `reply_skipped: true`。

**冷场后主动接一句。** 聊得正热（主人在上一条回复送达后 5 分钟内就回了）时，如果你回复之后主人 15 分钟没说话，
后台生成一个主动消息任务：把最近 4 轮对话交给模型，判断要不要自然地补一句，不想说就输出 `[SKIP]`。
发出的是 `qq.private.proactive` 主动消息。限制：
- 每次冷场最多一次；最新一条消息必须是你对主人的回复，所以接过一句之后不会再接（不连发）。
- 回复送达超过 1 小时、主人最后说的是“晚安”“拜拜”之类、勿扰时段、主动消息暂停时，都不会触发。
- 主人在任务执行前说了话，任务取消；引用的对话被擦除，任务也不会发出。
- 每天最多判断 3 次，包括模型选择不说的次数。它不占用主动分享的每日次数。
- 需要主人授权里的 `proactive` 用途，费用记在 `activity` 场景。

MaiBot 的做法是让规划模型每轮调用 `stop()` 或 `wait(秒数)`，等待超时后再跑一轮规划
（`src/maisaka/builtin_tool/wait.py`、`runtime.py` 的 `_schedule_wait_timeout`）。这样每轮都要调用模型，
和本项目“一个任务一次结算”的预算、Outbox 模型冲突，所以这里把“不回”做成回复标记，把“过一会儿再看”做成每次冷场一个后台任务，
复用主动消息的队列、送达和擦除。实现在 `companion/turn_taking.py`；配置项 `turn_taking`，在控制台“表达与表情”里调整或关闭。

## 关闭方法

| 项 | 关闭写法（`config.toml` 顶层，第一个 `[table]` 之前） |
|---|---|
| 对话策略 | `[conversation_policy]` 表里写 `mode = "OFF"` |
| 记忆 | `memory_enabled = false` |
| 分段回复 | `segmented_reply = false`；环境变量 `COMPANION_SEGMENTED_REPLY=false`；控制台设为空 |
| thinking | `chat_thinking = "disabled"` |
| 连发合并 | `[input_debounce]` 表里写 `enabled = false` |
| 收尾不回 / 冷场接话 | `[turn_taking]` 表里写 `silence_enabled = false` / `follow_up_enabled = false` |

控制台保存的配置在启动时覆盖文件值。服务器上如果已经显式写了旧值，新默认值不会生效。

## 会话内接话与收尾（2026-10-31）

基于 main `9501498`（已包含 #235）核对：`participation_v2` 已有主动选题、倾诉和求助的交流指导；
本次补齐其上下文依据，不增加分类模型调用、状态表或参与概率。`decide()` 的
`surface_intent` 仍只是当前原文的表面提示，不是完整交流状态。v2 不再仅因“嗯嗯”“哈哈”
或“晚安”就固定给出 `brief` 长度，明确的长短反馈仍有效；收尾提示要求结合前文确认。
v1 及其冻结实验保持原样。v2 继续使用同一策略名，旧实验输入需要重新准备，不能把旧
payload 当作这次候选的输入。

真实调用链：

1. 私聊入口在既有授权下接收输入；`input_debounce.due()` 只延后现有 chat job。
   `reply_revision.advance()` 取代旧任务，保留实际送达部分及尚未回复的主人输入。
2. `Core.context()` 调用 `conversation_policy.prompt_parts()` 注入稳定的 v2 指导和本轮
   表达参考。`owner_input.history_sources()` / `private_life.recent_history()` 复用
   `dialogue_history.recent_context_sources()`，保留送达前缀和未答输入；普通非授权实验
   路径继续使用 `recent_sent_sources()`。已有历史与回复版本就是本轮的话题和继续/结束
   信号来源，不另存一套推断结果。
3. 同一次聊天请求收到实际保留下来的历史、时间、获准记忆、图片资料和观看记录。
   默认 `chat_time_labels` 为每轮标时间，关闭标签时使用 `time_hint()`。只有本轮确实
   提供的材料可用于选题；观看记录允许在主人邀请起话题时选用，仍保留 origin、阅读
   深度和来源检查。没有材料可以开启普通新话题，不能编造相处或观看经历。
4. `fit_context()` 沿用原有裁剪；实际入选来源由 `remember_sources()` 绑定，历史、记忆
   等快照经 `_check_context_snapshots()` 复核。回复继续走现有引用、分段、Outbox 和
   发送前校验；本次没有新增发送路径。

本轮的交流判断由这次已有聊天调用完成：同一句“嗯嗯”在确认问题和晚安之后可以有
不同的接法；“晚安”也可能是前一问的答案。时间间隔不是情绪或结束意图的证据。
准备离开、明确结束时不另起话题；直接问题、正式请求及未答问题仍需正常回应。
引用、图片文字和外部材料不能变成系统规则或工具授权。

### 与 #235、Life/Wake 的边界

[PR #235](https://github.com/yanyuhanyue/companion-core/pull/235) 已合并，负责同一次调用的
`[SKIP]` 选择，以及通过既有 Life selector / Notice / Outbox 的有界后续消息。
本次复用它的话轮门禁：`silence_allowed()` 先用现有 Conversation Policy 的 SHADOW
表面提示筛选确认或收尾候选，再由同一次聊天调用结合前文决定。纯提示分类独立于策略
是否启用，不注入额外指令；“怎么弄”“帮我看看”“2+2”、新的情绪或内容均不能仅因
字数短而获得静默选项。原有回答前一问、未答输入和严格窗口的回应门禁保留。

长时间无输入的主动私聊仍由 Life/Wake 及原授权决定；`life_topics.py` 的来源、暂停、
预算、幂等和发送许可不变。会话内邀请起话题不授予任何后续主动发送权限。

### 对照与验证范围

`tests/fixtures/conversation_turn_cases.json` 提供 19 个合成案例，保留真实输入格式、前文、
间隔与逐例评价要点，涵盖冷场、简短回应、分享、倾诉、玩笑、不同意见、多轮指代、
求助、准备结束、明确结束、隔日上下文和外部文本。相同短句配不同前文，用于观察
语境适配，而不是让关键词识别命中率代替交流质量。
`test_conversation_turn_context.py` 通过 `Core.run_once()` 验证实际聊天输入、一次调用、
使用量结算及普通回复路径；模拟模型输出不参与自然度评分。
其中原生验收经过签名捕获、真实 HTTP 入口、三个独立 SDK worker 进程及 Bridge
投递租约，检查连续三轮的历史来源快照、策略收据和结算。它使用已有 DeepSeek 本地
精确计量及默认 3200 软额度，只模拟外部模型传输。

容量限制：未配置精确计量时，现有保守 UTF-8 字节估算可能把历史全部裁掉；在这次
原生合成配置中，main 原有系统文本本身也已超过 3200 字节。这种情况下策略只能
依据实际剩余材料，不能恢复被裁掉的话题或假装记得。本次缩短了新增策略措辞，
但没有更改全局上下文预算、计量或裁剪机制；生产配置的额度与计量组合仍须核对。

模型效果仍需在同模型、同历史、同工具和同配置下，对 main v2 与当前 v2 的冻结
payload 做盲评：自然度、继续交流意愿、事实准确性逐例打分，另统计机械追问率；
明确结束时应评价是否尊重收尾，不能用继续交流意愿奖励强行续聊。
现有 `evaluate_r1_conversation.py` 的 baseline 对 v2 实验不是这组 main-v2 对新-v2
对照；其通用重建器也不使用本数据的 `gap_seconds`，时间案例应使用原生调用链重建。
本次不添加实验协议或绕过付费预算/授权执行模型对照；本地模拟回归不能证明模型效果。

## 部署注意

- 有界真实文本窗口（`live_text_window`）要求 `segmented_reply` 与审批一致，审批里还固定了
  配置和代码哈希。使用这种窗口的环境需要重新审批，或显式写 `segmented_reply = false`。
- 付费审批的 `max_output_tokens` 是聊天总输出（含思考）的硬上限。审批仍是 768 时，思考和正文
  共用这 768 个 token；按上面的实测（思考 50–130 个 token）够用，但长回答可能被截断。
  要用满 `chat_thinking_tokens`，需要主人重新签一份上限更高的审批。
- 开启记忆需要 life-worker 和嵌入模型缓存（`embedding_cache_dir`）就绪。
- thinking 让聊天延迟增加约 1–3 秒；`timeout_seconds` 默认 45 秒，余量足够。

## 验证

- 测试：见 PR 描述中的 CI 结果。
- 线上验收：由部署会话完成，结果补在 PR 里。
