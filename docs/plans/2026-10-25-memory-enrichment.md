# N2/N3 的温摘要与长期保留价值

状态：Root 于 2026-10-25 确认实施范围；合成验收和完整候选 CI 尚未完成。

已有 EXTRACT/REVIEW、自然日协调器、Episode、Memory 和擦除日志继续拥有工作与数据。
本片补齐语义温摘要和有来源的保留价值估计。每个 selector 单元仍至多一次模型准入。

## 已检查的一手实现

- [DSH compaction-basic](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/compaction-basic/src/index.ts)
  把摘要与原会话范围、持久重放和实际模型调用关联。本项目复用这些边界，摘要随既有
  `memory_extract` 一起产生，保留原始事件及原费用，不增加单独压缩服务。
- [Letta memory](https://docs.letta.com/configuration/memory)
  在活动交互之外整理已有记忆。本项目继续使用同一个 Core、SQLite 和 NightlyConsolidator。
- [Generative Agents §4.1](https://arxiv.org/html/2304.03442v2#S4.SS1)
  将模型评估的重要性与相关性、近期活跃度分开；其
  [原检索实现](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/retrieve.py)
  保存逐事件的重要性。本项目采用有界 1–10 估计并绑定实际来源；保留现有 FTS5/BGE
  的相关性门槛和主排序，只在目录及同等相关性下使用保留价值。

## 冻结合同

1. `Extraction` 可带最多四个 `EpisodeSummary`：标题、最多 600 字摘要、1–20 个来源 ID。
   来源必须是本次原批次中的同一会话完整文本组；模型不能指定已有 Episode ID 或改原始事件。
   复用原 Episode 身份，只有来源集合完全相符才更新摘要。旧摘录仍可使用。
2. `Candidate.salience` 为可选的严格整数 1–10；它表示模型对保留用途的估计，
   不改变 CONFIRMED/HYPOTHESIS、主体、冲突、权限或温度。
3. 原提取检查点保存摘要候选；REVIEW 后在原事务提交。事实去重/冲突仍由现有流程完成。
   每条估计绑定原已 SETTLED 的提取调用、正证据、完整来源指纹及事实内容哈希。
   同一来源只贡献一次；每条 Memory 最多保留最近 20 条估计，原 importance 为它们的均值/10。
   没有有效估计时回到 0.5，不把模型评分当作置信度。
4. 元数据存入既有 `runtime_meta`，只是可失效的派生投影。摘要与估计都不产生新事实事件、
   计费计数器、权限或执行队列。摘要读取验证原调用、内容哈希及全部来源，仍标为模型归纳。
5. 擦除处理按完整来源闭包清除摘要/评分投影并重算余下评分；重放同样生效。
   过期、修改来源、缺失原调用或不匹配内容均不能参与读取与排序；费用记录保留。
6. `Memory.episode_summary(episode_id, source_event_ids, now)` 为只读接续接口。
   MemoryContext 在原来源检查之后可附上该模型归纳，同时保留来源和原文摘录。
   Core 原字节、Token 和来源数量预算继续生效。

## 所有权及验收

Root 拥有 memory.py、memory_work.py、nightly_stages.py、新 enrichment 模块与测试，
以及 contracts.py 的 Candidate/Extraction 与 model.py 顶部提取规则局部。
Life 拥有 MemoryContext 的接续消费者；R1 拥有擦除注册；共享文件只通过冻结接口集成。

验收包含真实签名 HTTP 输入、独立 Life 进程、提取后重启及第三次零重复；
原费用、来源撤回/过期、旧备份重放、错误摘要来源、冲突/真值不变和相关性不被评分覆盖。
摘要质量、长期价值估计和跨日体验仍需实际模型及用户验收。
