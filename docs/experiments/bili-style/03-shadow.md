# CC-STYLE-03：`bili_style_observation` 离线 SHADOW 段

状态：`SHADOW_OFFLINE_READY`。本阶段线上行为不变：模型调用 0 次、QQ 发送 0 次、生产配置变更为无。

## 1. 实现

`companion/style_observation.py`：

- **模式：** 只有 `OFF`（默认）和 `SHADOW` 两种，没有 `ENABLED`。
  - SHADOW 本身不接入运行时。`companion/` 下唯一的引用是 `core.py`，它只在另一个开关 `style_live` 打开时使用（默认关闭，见 05 的"线上使用"）。测试会检查没有其他模块引用它。
  - 线上使用时复用同一套选择和限额，追加在系统消息动态部分末尾，随整条请求走 Context Planner 和付费准入；需要 `expression` purpose，发送前重新校验候选版本和来源。
  - 打开 `style_live` 前，主人应先用 `scripts/style_shadow_samples.py` 看离线样例，并参考 04 的评测结果。
- **`shadow_payload(messages, config, candidates, ...)`：** 先套 `participation_v2`，在 `OFF` 时就停在这一步。在 `SHADOW` 时，再把观察段附加到系统消息末尾。
  - `messages[1:]` 逐字节不变。
  - 不改 `PARTICIPATION_V1`/`V2` 的文本，冻结哈希测试照旧。
- **选择：** 只用 `APPROVED`、未过期、来源全部仍在、没有风险标记的候选，最多 3 条，排序是确定的。
  - 正式求助和"好的/谢谢"这类回应不附加任何观察。
  - 收尾时只用带 `closing` 标签的候选。
  - 其他日常情境用带日常类标签的候选。
  - 没有合适的候选就返回零补充，不凑数。
- **限额：** 用项目现有的 `byte_estimate`（utf8-byte-v1）计量，附加段最多 600 单位，约 200 个汉字，对应任务书的"约 200 Token"（00 B5）。超出时从末尾逐条删除；连一条都放不下就不附加。由于 Planner 不会裁掉系统段（00 B6），限额在选择阶段执行。
- **回执与复验：** 回执记录模式、意图、候选编号及批准版本、附加量和当前输入哈希。`revalidate()` 会重新计算回执；如果候选已过期、来源已删除或内容变了，就返回 `STYLE_OBSERVATION_CHANGED`，过期候选不能复活。
- **段落措辞：** 段落写明"只是参考，不是指令；不改变事实、记忆、工具权限、承诺或发送规则，不适合就不用"。

## 2. 测试

`tests/test_bili_style_observation.py` 共 11 项：

- 默认 OFF 时与 v2 完全一致；
- SHADOW 只追加一段，其余消息不变；
- 冻结文本不变；
- 候选为空、来源已删、已过期时退化为 v2；
- 未审阅、被拒绝或带风险标记的候选不使用；
- 求助和确认类回应不附加；
- 收尾只用收尾候选；
- 最多 3 条且不超过单位上限；
- 结果确定；
- 复验能拒绝失效的候选；
- 除 `style_live` 外模块没有接入运行时。

```
python -m pytest -q tests/test_bili_style_observation.py tests/test_bili_style_candidates.py \
  tests/test_bili_style_corpus.py
48 passed
```

本地环境缺少 `pytest-xdist`，`tests/test_conversation_policy.py` 中依赖 `core` fixture 的 1 项无法在本地运行（`worker_id` 未找到），其余 43 项通过。完整回归以 CI 为准。

## 3. 未做

- 真实候选为 0（02 的 `NOT_RUN`），因此还没有真实 payload。
- 04 的处理组登记和盲评尚未开始。
