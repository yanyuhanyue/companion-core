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

选票带 `overall_choices = WIN/TIE/LOSE/BOTH_BAD/UNSCORABLE`，WIN 表示 A 更好。

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
