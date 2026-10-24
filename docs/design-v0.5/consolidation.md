# v0.5 夜间整合与反思

`life-worker` 独立于聊天和日记 Worker。按本地自然日协调记忆抽取、已有日记任务及反思候选；每个日期、时区和协调器版本只有一条记录。步骤进度、租约、隐私版本及来源摘要持久化。恢复时复用已完成步骤，日记继续遵循原有日期唯一键和删除标记。

Nightly 与独立记忆 lane 共用 `Core.extract_step()`、原跨进程提取租约和一个
`runtime_meta.memory_extraction_checkpoint`。`NightlyConsolidator.run_step(worker_id)`
返回 `UnitResult(progressed, next_deadline)`；每次最多一次模型 admission，或一轮非模型推进。
模型等待发生在事务外。原 `run_once` / `extract_once` 是同一实现的批量便利入口，
单 Life selector 必须调用 `run_step` / `extract_step`，不能把批量入口算作一个模型量子。
原始事实、FTS5+BGE、原生预算和自然日协调器仍是唯一权威，没有新增表、夜间调度器或事实库。

| 阶段 | 实际推进及持久位置 |
| --- | --- |
| N1 | 原自然日 claim 核验已结束日期、时区、主人权限、有效来源、来源摘要及隐私版本；`steps_json.n1` 保存进度。空日直接 SKIPPED，无模型 reservation。每个后续模型调用仍在原预算入口即时核验额度与权利，不预先占用整夜预算 |
| N2 | 复用仍有效、完整来源可核验的原 Episode；只为尚未覆盖的主人原文生成最多 20 条一批的原生情景。`mark_processed=False` 不提前消费事实提取输入。`warm_episode_ids` 是引用，不复制摘要；原 MemoryContext.continuation 可立即读取这些情景 |
| N3 | 持久化 EXTRACT/REVIEW 检查点，分别完成一次事实提取和至多一次冲突复核，再原子提交 Memory/来源/情景。随后独立步骤消费目标日期的 Diary、Reflection 和原 Goal 建议。`steps_json` 保存阶段与原生引用，待复核候选只存在共享提取检查点中 |

`extract_step(local_day=None, *, on_checkpoint=None)` 的可选日期约束用于 Nightly；
独立 lane 可恢复同一共享批次。`on_checkpoint` 仅把原生状态引用和完整来源闭包登记到
`consolidation_evidence`，与同一次检查点写入共用事务。日记的可选 `run_id` 限定消费该日期，
不会意外执行另一天待处理日记。阶段间释放协调器租约；其他 worker、重启或聊天优先让出后，
从持久检查点继续。原 `PARTIAL` 等待时间和 Diary/Reflection 原生下一次时间作为 deadline 返回。

提取与复核调用前先保存预分配 request ID。没有 reservation 的中断可恢复准备状态；
已结算但未保存结果的中断遵循原有有界重试；RESERVED/UNKNOWN 的遗失结果保留 UNKNOWN，
不会再次执行该批次。失败或未知的批次仅标记提取工作耗尽，不生成事实，也不释放费用责任。
返回内容缺少有效 usage 时，Memory、Diary、Reflection 都不会提交衍生结果。
共享检查点在终态移除候选正文和来源指纹，只保留原始 ID、阶段及错误元数据。

`memory-work` 通过原 `erase_derivatives` 注册。源撤回会删除共享检查点，候选写入使用
原始值比较，旧在途调用不能把已删除候选写回来。旧备份按原 Ledger 重放同样清理；
账务、模型责任和执行租约不由擦除处理器释放。N1 的日记展示来源上限不截断记忆批次的
擦除依赖，额外原生提取来源同样登记。既有完成日期不会因新增阶段而重跑模型。
未注册该擦除合同的部分集成会在创建候选前拒绝执行。时区变化使旧待处理批次退出，
保留已经发生的费用与 UNKNOWN 责任；下一步按新时区建立日期范围，不挪用旧日期身份。
独立 lane 完成批次后，等待中的 Nightly 按原 batch ID 或相同日期/时区读取终态引用继续，
不会重复提取或一直停在旧 REVIEW 标签。

`ReflectionService` 每个自然日最多尝试一次模型请求。独立日预算、月预算、每日调用数量及输出 Token 上限同时生效，预算默认 0，使用量不明保留 UNKNOWN 预留。反思最多三条，逐字引用原始主人消息、已发送回复或已验证观察；不引用日记正文。观察性内容带主体标注，其余是待审假设。主人可接受为假设、拒绝或要求更多证据；这些决定不会直接改写 Memory 或 traits。

所有提供给模型的来源都记录为依赖。源事件删除、保留期限到期、生成期间隐私版本改变均使对应候选失效；恢复旧备份回放相同删除。已提交的独立日期不因删除另一篇日记而失效。发生隐私变更的在途任务取消。

日记现在可使用观察来源，明确标注“发现的标题”“获取的来源内容”“视频分析记录”。失败和未知结果不当作完成经历；引用仍须逐字匹配。媒体原件不会进入日记。

默认配置保持全部新模块关闭。Docker 的日记及生活 Worker 分别使用 `diary`、`life` profile，显式运行 `docker compose --profile diary --profile life up -d` 才启动这些进程；对应功能还受配置开关和预算约束。

管理接口使用主人 control token：`GET /internal/v2/consolidation`，`POST /internal/v2/consolidation/{day}`，`GET /internal/v2/reflections`，`POST /internal/v2/reflections/{suggestion_id}/decision`。任务入队与付费执行分开，HTTP 进程不会直接等待模型。

验证包括日期重复调度、已完成日记复用、进程中断后的恢复、并发领取、等待模型时聊天可用、来源遗忘竞态、旧备份恢复、预算边界、过期记忆租约、DST/闰日及标题级日记归属。测试使用可控模型；实际真人反思质量继续作为生产试运行门禁。

## N1–N3 接线与依据

本片的 Core 与 erasure 注册由共享文件负责人接入独立补丁；本分支不修改这两个文件。
过渡验证显式加载 `tests.support.nightly_core_patch`，只安装补丁对应的方法委托、构造绑定及
擦除注册，原 DB、admission、模型客户端和 worker 实现均未替换。组合候选接线后应去掉该测试插件，
重验真实入口；测试插件不能当作产品接线。无需 0041 migration。

`tests/test_nightly_process.py` 从真实认证 TCP inbox 进入原聊天提交，再运行独立 Life worker。
provider 仅用 HTTP MockTransport；第一次进程在提取检查点退出，第二次复核并完成整理，
第三次启动无新增模型请求。每一步检查 admission 增量，期间不增加 QQ 投递。
N2 原生回归覆盖既有分批情景、后来新增来源、再次 Nightly、以及先 N2 后提取，不删除旧摘要。

实施前检查的一手实现及采用范围：

- [DSH compaction checkpoint](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/compaction/src/checkpoint.ts)
  （blob `5cba6ca14cf6929030eba1f0518d1f61cd5bacc3`）把检查点与原 compaction identity 关联；
  本片同样保存原 request/来源身份，而不把生成文本当作完成凭据。
- [DSH one-shot summarizer](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/compaction/compaction-basic/src/summarizer.ts)
  （blob `2d258fa410a7a87258f8d3a45c548cde0d383014`）区分一次辅助模型调用及其实际 usage；
  本片复用 Core 原 reservation/settlement，不引入 DSH 执行器。
- [YuriOS DreamConsolidator](https://github.com/yuri-os/YuriOS/blob/cd4d1f16ae095f0f694dbbe7b1aac8e33db25367/yurios/mind/dream.py)
  的已结束日期、最旧优先、可续跑整理用于比较；Core 继续使用原自然日租约和严格预算，
  不采用其首个超大日期可越过名义 tick 预算的例外。这里的整理不等于虚构 REM。
