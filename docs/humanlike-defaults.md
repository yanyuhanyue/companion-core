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

## 关闭方法

| 项 | 关闭写法（`config.toml` 顶层，第一个 `[table]` 之前） |
|---|---|
| 对话策略 | `[conversation_policy]` 表里写 `mode = "OFF"` |
| 记忆 | `memory_enabled = false` |
| 分段回复 | `segmented_reply = false`；环境变量 `COMPANION_SEGMENTED_REPLY=false`；控制台设为空 |
| thinking | `chat_thinking = "disabled"` |
| 连发合并 | `[input_debounce]` 表里写 `enabled = false` |

控制台保存的配置在启动时覆盖文件值。服务器上如果已经显式写了旧值，新默认值不会生效。

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
