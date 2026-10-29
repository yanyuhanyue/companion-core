# CC-STYLE-00：B站评论区表达观察的基线与冻结评测范围

状态：`BASELINE_FROZEN`。本文件只做核对与预注册；本阶段**模型调用 0 次、B站网络调用 0 次**，
未采集评论、未修改生产配置。所有真实外部结果均为 `NOT_RUN`。

任务书：`Companion_Core_CC-S1-02-BILI-STYLE_Taskbook_2026-10-29.md`（主人提供，不入库）。

## 1. 精确基线

| 项 | 值 |
| --- | --- |
| main | `dad490826e88aa3d6da022911f2f701c2504a810`（#189 合并） |
| 开放 PR | 无（核对于 2026-10-29） |
| 生产 | Core/Bridge 为 `dad4908` 镜像，OwnerAuthority ACTIVE33，schema `0049_evaluation_ordinals` |
| 生产表达策略 | `conversation_policy` 未配置，即 `mode="OFF"`；线上不注入 v1/v2 |
| 生产 B站 Reader | `--source bilibili --protocol reading-v1 --discussion --video-media`，账号授权有效 |
| 付费授权 | 不限时；OwnerAuthority 目前**没有** `evaluation` purpose |

## 2. 复用组件的实际边界

### 2.1 评论取样：`source_reader/bili_discussion.py`

- 评论接口 `/x/v2/reply?type=1&oid=<aid>&pn=1&ps=10&sort=2`，**只读首屏**，不翻页。
- 根评论 = 置顶 + `replies[:20]`（实际 `ps=10`，最多 10 条非置顶根）；每根最多 3 条内嵌楼中楼。
- 全局上限：40 条、总 6000 字、单条 1000 字；字段 `comment_id`、`reply_to_id`
  （父评论 `parent` 或所在根）、`floor`、`author`（≤80）、`text`、`image_count`、`truncated`；
  整体 `reported_total`、`partial`（实际几乎总为 True）。
- 弹幕：`/x/v1/dm/list.so`，最多 120 条、总 6000 字，`scope="current_pool_sample"`。
- 状态 `available/unavailable/restricted/budget_limited`；授权与 grant 错误直接上抛。
- 评论**不进入** Reader 的 `text`，只在 `reading.comments` 证据里。
- `bili_tools.bili_comments` 只返回字符串列表，无 ID、无回复关系，**不作为对话链来源**。

### 2.2 证据存储：`companion/observation.py`

- `SourceComment` / `CommentEvidence` 严格模型（上表上限一致；ID 唯一、不得自回复；
  `partial=False` 时总数必须相符且无截断）。
- 写入 `observations.evidence_json.reading.comments`；对应 `events` 行
  `memory_processed=1`，**记忆抽取天然跳过**。
- 投影时标记 `external_untrusted`（reading-v1 为 `untrusted_external`）。
- 删除：`erase_observations` 清空 `evidence_json`，并在 `erasure.py` 注册；
  单条遗忘 `POST /internal/v2/observations/{event_id}/forget`；来源撤销
  `SourceGrantService.forget` → `ledger.forget_source_grant`；旧备份恢复后重放删除。
- 留存默认 `raw_event_retention_days = 365`；本实验另设更短的 7 天上限（见 §4.3）。

### 2.3 评论文字现有的去向（防串用核对）

| 消费方 | 是否读评论文字 | 本实验要求 |
| --- | --- | --- |
| `content.py` 聊天投影 | 是（`kind: comments`，≤6000 字） | 不变；与本实验无关 |
| `agent_reading.reading_result` | 是（完整 `evidence_json`） | 不变 |
| `diary.py` | 否（只读 `body_json.text[:500]`） | 不得新增读取 |
| `interest.record_exploration` | 否（只用 goal topic） | 不得新增读取 |
| Memory / Affect / growth | 否 | 不得新增读取 |
| `growth.py` `owner.expression_preference` | 否（只读主人反馈的两个维度） | **禁止**并入网友评论 |

### 2.4 表达策略：`companion/conversation_policy.py`

- `PARTICIPATION_V1`（6 行，哈希冻结）与 `PARTICIPATION_V2`（7 行，主人 2026-10-28 批准）。
- 冻结测试 `test_frozen_v1_strategy_and_transformation_are_identical`：
  `PARTICIPATION_V1` = `c6978d05…dab6940`，变换后 = `f07a941f…85f7dc1`。v2 无哈希，只校验 7 行与关键措辞。
- `prompt_parts` **只在 `ENABLED` 时改动系统文本**；`SHADOW` 运行时不注入任何内容。

### 2.5 评测：`scripts/evaluate_r1_conversation.py` 与 `companion/evaluation.py`

- 处理组白名单：脚本 `TREATMENTS = ("participation_v1", "participation_v2")`；原生入口
  `_plan` 只接受键存在时等于 `participation_v2`。
- 两组的 `messages[1:]` 必须逐字节相同，**处理只能改系统消息**。
- 原生上限：主人 pilot 6 次 / 1 case；合成 216 次 / 36 case / 1 美元 / 单请求输入 8000。
- 盲评目前 4 个维度：`naturalness`、`desire_to_continue`、`deliberateness`、`factual_accuracy`。

### 2.6 计量：`token_measurement` / `context_planner`

- 无精确计数配置时为 `utf8-byte-v1`：每条消息 UTF-8 字节 + 24，再 + 32；中文约 3 单位/字。
- 追加到系统消息的文本**不会被 planner 舍弃**（planner 只丢 references 与最旧历史）。
  因此额外表达参考必须在选择阶段自行限额，不能依赖 planner。

### 2.7 已有“表达/黑话”实现

- `expression.py` 只管表情包类别提示；`growth.py` 只学主人反馈的两个维度。
- main 上没有黑话或风格学习实现；`.local/design-drafts/policy-editor-and-slang.md` 只是未实现提案。
- 结论：本实验不与现有引擎重复，候选应放在**独立命名空间**。

## 3. 最小数据流与权限流

```text
主人分享 / 已授权目标的 B站读取（现有 Exploration → Reader 租约、预算、UNKNOWN）
  → observations.evidence_json.reading.comments（external_untrusted，可遗忘、可撤销）
  → [01] 只读语料构建：查询已有观察，不发起新 I/O；父子匹配、context_missing、过滤
  → [02] 抽象候选（独立 JSON，CANDIDATE；不入 Memory/Interest/Affect/Diary/growth）
  → 人工审阅 → APPROVED（不可变版本）
  → [03] 选择 0–3 条，组成 bili_style_observation 段，附在 v2 系统指导之后
  → [04] R1 离线计划：A = participation_v2，B = v2 + 段；付费执行需另行授权
```

权限：Core 决定谁能看、能做什么；Reader 只取样并回执；评论没有指令权；候选可删除；
记忆、Life、Affect、OwnerAuthority、费用与发送由原组件负责。

## 4. 阻断项与偏离

| # | 项 | 影响 | 处理 |
| --- | --- | --- | --- |
| B1 | `SHADOW` 运行时不注入 | 任务书的“SHADOW 集成”无法在线进行 | 03 的 SHADOW 定义为**离线完整 payload 生成**，不接线上聊天；线上仅 `OFF` |
| B2 | 首屏 ps=10、每根 ≤3 楼中楼 | 单视频最多约 40 条，父评论常不在样本中 | 不翻页；缺父即标 `context_missing`；不足如实报告 |
| B3 | 没有“按清单读取视频”的现成受控入口 | 主动采样需要新的操作入口或目标计划 | 01 只做**已存在观察的只读语料构建**；主动采样另行设计、另行授权，标 `REAL_SAMPLING_NOT_RUN` |
| B4 | treatment 白名单在两处写死 | v2 + 观察不能直接跑 | 04 窄范围登记新 treatment，并同步验证器与测试 |
| B5 | 字节估算下中文约 3 单位/字 | 「≈200 Token」需要换算 | 03 用现有计量函数实测追加量，并设硬上限 |
| B6 | 追加的系统段不会被 planner 丢弃 | 超限不能靠 planner 兜底 | 03 在选择阶段限额，超限即返回零补充 |
| B7 | OwnerAuthority 无 `evaluation` purpose | 真实付费评测不可执行 | 另行改授权；04 只做离线计划 |
| B8 | 盲评只有 4 个维度 | 任务书要求 6 个维度及胜负 | 04 扩展投票格式，旧计划保持兼容 |
| B9 | 现有 R1 多轮 case 只有 8 个、每个仅 1 轮历史 | 「≥8 个多轮」偏弱 | 04 新增至少 8 个 2 轮以上的合成多轮 case |

## 5. 冻结评测范围（预注册）

- **对照：** A = `participation_v2`，B = `participation_v2` + 已审阅的 `bili_style_observation`。
  baseline/v1 只作历史参考，不进入主比较。
- **独立情境：** `tests/fixtures/r1_conversation_cases.json`（36 例，7 类）+
  `tests/fixtures/r1_expression_cases.json`（6 例）= 42 例；04 再加 ≥8 例多轮。
  任何用于调试候选的样本不得进入这个测试集；新增 case 在冻结时记录数据集 SHA-256。
  当前冻结的原始字节（git blob）SHA-256：
  - `r1_conversation_cases.json`：`43f2a9289f826b0aea815554d2cea9b8d2b9e0e9bed540e4455f16d0db91d663`（36 例，其中 8 例有历史）
  - `r1_expression_cases.json`：`896cd3b14b3714787bece2ca13c608233c2041e11c5c60be5f76b8699397f21b`（6 例，其中 1 例有历史）
- **模型与条件：** `deepseek-flash`，非 thinking，输出上限 2048，软上下文 16000；不发 seed 与
  temperature，不宣称采样可复现；两组非系统输入逐字节相同。
- **费用与次数上限：** 走原生合成入口：≤216 次、≤36 case/计划、≤1 美元/计划、单请求预留
  ≤5000 微美元；超限 `NOT_ADMITTED`，不删减某一侧的历史。
- **失败处理：** UNKNOWN 中断后续且不重试；失败与不可评分项留在分母。
- **盲评维度：** `naturalness`、`desire_to_continue`、`deliberateness`（越低越好）、
  `context_fit`、`forced_meme`、`factual_accuracy`；结论 `WIN/TIE/LOSE/BOTH_BAD/UNSCORABLE`。
  位置随机且平衡，按 case 分组，不把同一 case 的重复采样当作独立样本。
- **进入 Beta 的门槛：** 照任务书 §7 原样冻结（非平局偏好 ≥60%、强行玩梗增加 ≤5 个百分点、
  真实性与边界 0 违规、日常聊天不新增模型调用、0 次重复发送、撤销后不再注入）。

## 6. 样本选择与语料规则（冻结）

- 10–20 个不同主题视频，目标 150–300 条有效评论；不足如实写。主题覆盖日常分享、动漫/游戏、
  科技、宠物、生活吐槽、个人感受、观点分歧；单一 UP 主或圈子不得超过三成。
- 只用已授权首屏；父评论不在样本中时标 `context_missing`，不推断上下文。
- 纳入与排除按任务书 §4.2；点赞数不作为质量标准，首屏热门偏差写入报告。
- 原文只存于非仓库、访问受限的实验目录，最多 7 天；候选只存抽象策略、评论 ID 与摘要指纹。

## 7. 本阶段实际结果

| 项 | 结果 |
| --- | --- |
| 模型调用 | 0 |
| B站网络调用 | 0 |
| 真实评论采样 | `NOT_RUN` |
| 人工盲评 | `NOT_RUN` |
| 生产配置变更 | 无 |
