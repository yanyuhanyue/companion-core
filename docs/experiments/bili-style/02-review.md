# CC-STYLE-02：语用策略候选与风险门禁

状态：`CANDIDATE_GATE_READY`，真实模型提炼 `NOT_RUN`，真实候选 0 条。
本阶段模型调用 0 次、B站网络调用 0 次。

## 1. 实现

`companion/style_candidates.py`，纯函数，只读 01 的语料：

- **候选合同：** 字段按任务书 §5.1。
  - `context_tags`、`risk_flags` 是固定枚举。
  - 四个指导字段各有长度上限（40/60/60/60 字）。
  - 来源 1–8 条，按序且不重复。
  - `candidate_id` = SHA-256（标准化后的策略 + 排序后的来源）。改动策略或来源，编号必然改变，伪造的编号会校验失败。
- **抽取入口可注入：** `distill(corpus, now, live_event_ids, extract)`。
  - 只把 `parent_in_sample` 的父子对交给抽取器，并且只交脱敏后的两段文字和截断标记。
  - 根评论和缺父评论不进入抽取。
  - 调用前会对语料重新跑一遍 01 的风险规则，语料被改过也不直接信任。
- **模型输出越界：**
  - 抽取器只能返回策略字段，多余字段（如 `state`、`source_refs`、`approved_version`）一律拒绝。
  - 指导文字拒绝以下内容：链接、广告、联系方式、辱骂、指令式文本、色情、系统/开发者/主人角色口吻、命令、密钥、引号原句以及“就回…”式模板。
  - 与来源有连续 8 个字相同的，记为 `copied_source`。
- **去重与来源分歧：**
  - 同一策略的多个来源合并成一个候选，同一输入重复运行结果相同。
  - 同一策略在不同来源里的标签或风险不一致时，记为 `source_disagreement`，不生成候选。
- **停止：** 抽取器抛出 `ExtractionStopped`（`budget_exhausted`、`unknown_outcome`、`authority_stopped`）后，整轮立即停止，不重试。
- **审阅：** `review()` 只接受具名的人类审阅者，`model`、`deepseek`、`agent` 等开头的名字被拒绝。
  - 带风险标记的候选只能拒绝，不能批准。
  - 批准版本不可变：同内容重复批准是幂等的，改内容或撤回批准都会报错。撤回靠删除来源实现。
- **失效：** `still_usable()` 只对 `APPROVED` 且未过期、全部来源仍存在的候选返回真。过期时间取自语料，最长 7 天。
- **命名空间：** 候选只存抽象策略、评论 ID 与来源快照哈希，不含评论原文或作者，也不写入任何 Core 表。

## 2. 测试

`tests/test_bili_style_candidates.py` 共 28 项，全部使用合成数据和假抽取器，覆盖：

- 提示注入；
- 个人信息；
- 侮辱；
- 明显抄录；
- 语境缺失；
- 串源；
- 过期与删除；
- 重复样本；
- 来源分歧；
- 模型输出越界与多余字段；
- 费用耗尽与 UNKNOWN 不重试；
- 只有人能批准；
- 不可变版本；
- 伪造编号与状态。

```
python -m pytest -q tests/test_bili_style_candidates.py tests/test_bili_style_corpus.py
37 passed
```

## 3. 审阅结果

| 项 | 结果 |
| --- | --- |
| 真实提炼 | `NOT_RUN` |
| 接受 | 0 |
| 拒绝 | 0 |
| 合成示例 | 1 条（`02-pattern-candidates.json`，来源为合成 fixture） |

不运行真实提炼的原因：

- 01 的真实语料只有 1 个视频、7 对，太少，提炼出的策略没有代表性。
- 付费调用必须走现有 evaluation job、预算与 OwnerAuthority，而生产授权目前没有 `evaluation` purpose（00 §4 B7）。
- 本模块不包含任何 HTTP 客户端，也不包含 Agent 执行循环。

## 4. 下一步所需授权

1. 主动采样清单与预算（00 B3），使语料达到 10–20 个视频。
2. 授权加入 `evaluation` purpose 后，用现有计费管道接入抽取器。
3. 主人逐条审阅候选，只有批准的版本能进入 03。
