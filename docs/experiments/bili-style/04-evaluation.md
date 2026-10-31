# CC-STYLE-04：R1 固定输入对照与盲评格式

状态：`PLAN_READY_OFFLINE`。真实付费生成 `NOT_RUN`，人工盲评 `NOT_RUN`。本阶段模型调用 0 次。

## 1. 处理组登记（00 B4）

新处理组是 `participation_v2_bili_style`，对照组固定为 `participation_v2`，不与 baseline 比。

- **离线脚本：** `scripts/evaluate_r1_conversation.py` 新增 `STYLE`、`CONTROLS` 和 `plan_control()`。
  - 计划只有在 STYLE 时才写入 `control`、`style_candidates`（编号与批准版本）和 `style_cases_applied`。v1/v2 旧计划的字段和摘要不变。
  - `prepare(..., treatment=STYLE, style={...})` 必须传入已审阅的候选；其他处理组传入候选会报错。
  - 如果没有任何 case 用上候选，直接拒绝生成计划，因为两组完全一样就没有可比的内容。
  - CLI 不开放 STYLE。候选必须由调用方明确传入，不从磁盘自动读取。
- **校验：** 两组的 `messages[1:]` 逐字节相同；处理组的系统消息必须等于“对照组系统消息 + 观察段”。
  - 观察段以固定标题开头，计量值与回执一致，且不超过 600 单位。
  - 回执里的候选必须在计划登记的列表中。
  - 对照组不能带回执。
- **原生入口：** `companion/evaluation.py` `_plan` 用 `CONTROLS` 表接受 `participation_v2`（对照 baseline）和 `participation_v2_bili_style`（对照 `participation_v2`），控制组不匹配就拒绝。上限、计量和费用检查不变。

## 2. 盲评格式（00 B8）

STYLE 计划的选票有 6 个维度：

- `naturalness`
- `desire_to_continue`
- `deliberateness`
- `context_fit`
- `forced_meme`
- `factual_accuracy`

评分者只看到 A/B，`overall` 填 `A`、`B`、`TIE`、`BOTH_BAD` 或 `UNSCORABLE`（与 R1 一致）。
全部评完后才揭盲，`verdict()` 把 A/B 换算成处理组的 `WIN/LOSE`，其余原样保留，
这就是 00 §5 预注册的 `WIN/TIE/LOSE/BOTH_BAD/UNSCORABLE`。

每个维度填 `{"A": 分数, "B": 分数, "reason": "具体依据"}`，1–5 分。前四项沿用
`docs/experiments/r1-natural-conversation/README.md` 的尺度，新增两项：

| 维度 | 1 分 | 3 分 | 5 分 |
|---|---|---|---|
| context_fit 贴合语境 | 接错情境（求助时玩笑、严肃时调侃） | 情境对但回应泛泛 | 抓住前文的具体细节，语气合乎当下 |
| forced_meme 强行玩梗（越低越好） | 没有硬套梗或评论区腔 | 有一处略生硬的梗 | 明显堆梗、套模板或照搬评论区句式 |

`deliberateness` 与 `forced_meme` 越低越好，其余越高越好。没有可核对断言时，
`factual_accuracy` 填 `null` 并注明“不适用”。

A/B 位置照旧，按种子随机且平衡；对应关系只保存在 `mapping.private.json`。选票里不出现处理组的名字或观察段文字。旧计划仍然是 4 个维度。

## 3. 多轮 case（00 B9）

`tests/fixtures/r1_multiturn_cases.json` 有 8 个合成 case，每个有 2–3 轮历史：

- 日常分享
- 轻松回扣
- 吐槽
- 观点
- 求助（反例）
- 冷场
- 严肃情绪（反例）
- 收尾

名字与 00 冻结的 42 例不重复，也不用于调试候选。

## 4. 本阶段顺带修正（03 选择规则）

用这 8 个 case 生成计划时发现两个问题：

- 带理由的收尾（“先这样，我去洗澡了”）没有被识别为收尾；
- 严肃话题（复查结果）也会拿到玩笑类观察。

现已修正：

- `style_observation` 用 `CLOSING` 前缀识别收尾；
- 当前输入命中 `SERIOUS`（就医、离世、分手、失业、抑郁等）时，只允许情绪或吐槽类候选，并排除带 `light_humor` 的候选。

## 5. 测试

```
python -m pytest -q tests/test_bili_style_evaluation.py tests/test_bili_style_observation.py \
  tests/test_bili_style_candidates.py tests/test_bili_style_corpus.py \
  tests/test_r1_conversation_experiment.py -k "not native_context"
```

结果：本地 93 项通过。

`native_context` 系列和 `test_evaluation*` 中依赖 `core` fixture 的用例，本地缺 `pytest-xdist`，没有运行，以 CI 为准。

## 6. 真实运行仍需要

1. 真实的已批准候选：02 当前为 `NOT_RUN`，需要采样授权（B3）和提炼授权。
2. OwnerAuthority 加入 `evaluation` purpose（B7）。
3. 主人或评审人完成盲评。进入 Beta 的门槛按 00 §5 原样执行。

## 2026-10-31：近期表达重复评估

本轮主人选定离线／影子评估范围。`style_distill` 继续负责从已读评论提炼抽象交流经验；
本节评估自己的近期表达，不训练、保存或注入新的风格候选。
上游固定来源与推荐流接入见 [插件兼容性增量](../../design-v0.6/plugin-compat-p3.md#2026-10-31账号推荐流发现)。

### 人工判读

| 维度 | 需要判断的内容 | 必须保留的自然表达 |
|---|---|---|
| `opening_repetition` | 连续多轮是否习惯性套用相同开场或句式 | 稳定口吻、简短应答、语境合适的相同说法 |
| `meme_reuse` | 同一个梗是否被反复硬套到无关话题 | 主人邀请接梗、自然回扣、共同话题 |
| `story_repetition` | 是否缺少新信息却把已分享的事重新介绍 | 主人要求复述、相关回顾、纠正与有新增的进展 |

每项由人工填写 `NATURAL`、`REPETITIVE` 或 `UNSCORABLE`，并写具体理由。
自然度和语境贴合比字面变化更重要；不要求每轮换口吻。
同一故事改换措辞仍可能重复，重复一句话也可能恰好合适。

### 可运行入口

`scripts/evaluate_expression_repetition.py` 只读调用方提供的文字，不查询生产数据库、
调用模型或修改回复。输入为 JSON 对象：

```json
{
  "messages": [
    {"role": "user", "content": "刚才看到什么？"},
    {"role": "assistant", "content": "读到了恒星颜色与温度的关系。"},
    {"role": "user", "content": "再讲一遍那个故事吧"}
  ],
  "reply": "读到了恒星颜色与温度的关系。",
  "meme_phrases": [],
  "scorable": true
}
```

```text
python scripts/evaluate_expression_repetition.py --input .local/expression-input.json --output .local/expression-review.json
```

最多保留最近 12 条 assistant 回复及对应的主人输入。报告提供共同开头字符数、bigram
Jaccard 重合、最长共同片段，以及调用方明确指定的梗原文是否重用；最长片段只比较标准化
文本的前 2000 字。原上下文保留供人工核对；这些数值不自动判断同梗、故事新旧或表达好坏。
没有指定梗原文时仍保留人工 `meme_reuse` 判读。
失败、空白或不完整的输出应传 `scorable=false`，不能计作多样性改善。
报告只标记 `supplied_text_only`；真实样本应由已有送达回执确认可见文字，草稿不能冒充已说过。
生成的报告限定在工作区 `.local`，已有文件不会被覆盖；含私人原文的报告按原样本处理许可保管与删除。

现有 `scripts/evaluate_r1_conversation.py blind` 新增 `--expression-review`，
在原有 A/B 选票之外生成 `expression-review.json`。该文件沿用原 `pair_id`、盲化 A/B 与
共享输入，不包含策略名称；原计划、付费计量和六维选票不变。
原评测判为不可评分的输出，这份附加表也标为不可评分。

### 本轮验证与结论边界

合成回归覆盖固定开头、同梗硬套、旧事重讲、主人要求复述、自然接梗和改写后的旧事。
后几类保留原语境和人工空白评分，避免把“字面重合”误当成“需要拦截”。
这证明评估工具和盲评接线可用，不证明真实对话质量已经提高。
真实样本的人工判断仍待完成；本轮不新增付费生成。
