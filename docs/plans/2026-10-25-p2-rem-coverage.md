# P2、N1–N3 与 REM：实现和验收边界

依据：P1–P4 v3 任务书，2026-10-25 源码候选包含 Root `77687e7`、
REM 测试 `21ab195`、连续 Life 入口 `c050e98`、生产记录 API 检查 `44fcb16`。
此页描述实际覆盖，不把合成时间或模型返回值当作真实自然日体验。

## P2 源码覆盖

| 要求 | 已有机制 | 真实缺口或验收限制 |
| --- | --- | --- |
| Hot/Warm/Cold 与重新激活 | `memory_projections.py` 重建温度；`memory_context.py` 有界候选、完整来源证明；`core.py` 授权历史预算和上下文集成 | 无独立三库。近期重要反馈只在已有表达学习/当前来源路径中体现，没有单独的反馈热度排名；不能声称所有重要反馈均能重新激活旧 Episode |
| 事实召回、冲突、修订、遗忘 | 原 FTS5+BGE、来源验证、Memory 冲突/确认与 ErasureLedger | 间接问法、跨日时间关系和上下文相关性的质量仍需同模型对照及真人使用；合成案例证明边界，不证明总体召回质量提升 |
| 经历/目标状态 | 投影区分 discussed/planned/running/done/failed/UNKNOWN/told/closed，检查原 Reader 回执与承诺反馈 | `goal_steps` 当前仍是每 Goal 一步；完整多步结果聚合尚未实现。此限制必须随多步改造修复，而不是直接选第一步 |
| 人格/兴趣 | `growth.py` 从多自然日明确反馈重建表达偏好；`interest.py` 从实际探索证据形成独立兴趣；Core 消费相应上下文 | Social 正负责兴趣、失败及跨渠道反馈进入下一次 Life 选择；本片不重复编辑。长期行为改变与自然交流改善尚无真实自然日验收，不能从 STABLE 标签宣称稳定人格 |

## N1–N3 源码覆盖

`consolidation.py` 与 `nightly_stages.py` 使用原自然日协调器、租约、授权及来源摘要；
`memory_work.py` 是独立 memory lane 与 Nightly 共用的提取/复核检查点。

| 阶段 | 已实现 | 仍不能宣称的能力 |
| --- | --- | --- |
| N1 | 已结束本地日、有效事件、来源/隐私/用途校验；空日跳过；每次 admission 即时检查预算 | 不会预留整夜预算。未完成事项主要由 Goals/Hot 投影保留，没有另一个 N1 模型语义分类器 |
| N2 | 完整来源去重；只编码未覆盖文本；复用 Episode ID；温记忆引用可立即接续 | `Memory._encode_episode()` 的摘要是每条原文前 100 字的有界摘录，并非跨情景语义合并/重写。活跃程度由读取时投影计算，不是 N2 的独立评分写入 |
| N3 | EXTRACT/REVIEW 分步，来源与冲突复核，原子提交；同日 Diary/Reflection/Goal 候选；终态清除候选私有正文 | `Memory.add()` 的 importance 当前初始值仍为 0.5；不存在已验证的长期价值学习或按成功召回调整排序算法。候选意义与反思价值仍需真实模型验收 |

已验证原生独立进程恢复、每步有限 admission、来源撤回、UNKNOWN、旧备份擦除重放，
以及真实签名输入进入各独立用途。相关原回执与 Root/Social 的组合回执复用，未重复运行。
这些证据不覆盖连续多个真实自然日的可靠性、长期摘要质量或有用反思比例。

## REM 源码覆盖

`rem.py` 从至多 20 个候选选择至多 3 个已确认 Memory，固定原始证据证明；
原 `jobs(kind=rem)`、预算、selector、擦除、恢复和 cognition 元数据读取已接通。
DREAM/SKIP、FICTION/UNVERIFIED、次数/token/费用/保留期约束有原生验收。

需要保留以下限制：

- 当前素材仅来自已确认 Memory，不会直接选择 Interest、原始 Episode 或观察正文。
  已有证据可通过 Memory 入选，但不能宣称已实现多种素材联合选择。
- 问题保存在私人虚构记录中，尚无送入真实探索候选的消费者；任务书允许成为候选，
  并未授权把虚构问题或正文直接作为 Goal 完成证据。
- cognition 只读接口提供元数据和原费用引用，刻意不返回作品正文。
  原生 `rem.records()` 可读正文，但本轮没有新增 UI 或作品正文 HTTP 入口。
- 已结算无效 REM 不阻塞独立 Diary，selector 仍推进其他 lane；
  UNKNOWN 按共享预算合同可能阻止后续付费调用，不能为了“REM 不影响 N1–N3”绕开对账门禁。
- 创造性、问题是否有意义、真实自然日调度和真实 QQ 使用均未验收；未启用真实 provider/QQ。

## 增量证据与依赖

- Memory 两个旧 fixture 已由 `ce8381a` 迁移为真实 OwnerCapture/HTTP 与原 SDK 工具。
- `21ab195`：18 项相关检查、199 项文档检查；源 `0e1b3bc` 与共享接线 `36335d4`。
- Root 对应域源为 `407054b`，共享接线为 `0448938`；selector `134ef0c`
  含 Social `0c6773e` 的双 purpose 检查；cognition reader `365b34f` 来自 `8f073df`。
- `c050e98`：只新增连续 HTTP → 独立 Life worker → selector → REM 边界，1 项通过。
- `44fcb16`：既有 DREAM/SKIP 成功案例增加实际生产记录的 cognition HTTP 精确读取，2 项通过。

统一回执位于集成交接的 `rem-record-contract.json` 所引用路径。
完整方案的真实体验验收与上述确定性边界测试分开记录。
