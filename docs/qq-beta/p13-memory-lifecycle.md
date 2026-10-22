# P13-B：业务记忆生命周期申请与离线证据

源码基线 `eb5143500bed5395bb45f2a2cc20335da9988491`。本轮业务数据读写、真实 QQ 管理、备份复制、模型调用和手机签收均为 `NOT_RUN`；当前主机 STOP、实际镜像、业务库身份、真实主人来源与最末账本未回读，全部为 `null` / `NOT_RECHECKED`。`BUSINESS_M1_LIFECYCLE_PASS` 尚未取得，私人 Beta 与正式就绪声明保持 false。

原 `Memory`、FTS5/BGE、ErasureLedger、backup、独立 `memory-control` 入口足以承载本轮准备；本包不改生产代码或新增记忆数据库。新增工作是 [逐项申请模板](p13-memory-request.template.json)、事实/删除闭包验收顺序和 [F2 实际候选对照](../experiments/p13-memory/README.md)。模板为 `DRAFT_NOT_EXECUTABLE`，不是 Core 可加载的 grant 或任何运行许可。

## 初次业务 B1 与后续 QQ 管理

现有 `approved_stopped_r5` scope 必须包含已完成业务 B1 的 `b1_acceptance_sha256`。P12 离线通过或 P11 只读召回不能填入这个字段。首次业务 B1 应单独批准原生 `Memory.add` / `Memory.decide` 的准确对象、来源和操作，并完成真实签收；之后才能独立批准一次 QQ 管理命令。不得用合成回执跳过这一顺序。

每份 remember / confirm / correct / forget scope 独立批准，最多 15 分钟、一条准确操作。业务聊天、Worker 和 Bridge 发送保持停止，确认原队列无未决 I/O、费用 UNKNOWN 或 `DELIVERY_UNCERTAIN`；这些状态没有核实前不能用历史 STOP 当作当前证明。管理操作模型尝试为 0、QQ 回复为 0。

QQ 管理时仅当前认证 owner 私聊的纯文本、首字符 ASCII `/` 可消费 scope。Core/Bridge 使用相同 canonical `MemoryControlGrant.model_dump(mode="json")` 完整对象及摘要；当前模板不能直接作为该对象加载。每次重新绑定 candidate/实际 image ID/镜像摘要及证据等级、Core/Bridge 配置与代码 hash、owner/bot 身份、capture key、protocol epoch、privacy epoch、原 STOP、Ledger、全部 Budget/Window Journal/Spool 身份和头、当前 restore generation、操作者与停止人。前一操作成功后重新取当前值，不沿用旧批准。

## 准确事实与操作顺序

先选择一条非敏感、主人确实说过且可人工核对的事实，记录原始 owner 来源、时间、有效期、subject/fact_key 和内容摘要。另选一条独立对照事实，确认其原来源、证据、修订与派生删除闭包均不与目标相交；仅内容不同并不足以证明独立。若共享来源会连带删除，必须在新批准中明确闭包或重新选择对象。

| 操作 | 独立批准与预期证据 |
| --- | --- |
| 原生创建 | 精确 `Memory.add` 输入、真实 owner 引文和 `confirmed` 选择；若明确审批为待确认事实，记录 `HYPOTHESIS`，不得隐藏或制造中间状态 |
| 确认 | 新批准绑定当前 memory ID、目标族 hash 与真实确认来源；原生 `decide(confirm)` 可记录 HYPOTHESIS → CONFIRMED |
| QQ remember | 仅 B1 签收后，准确 `/记住这个 …`；当前实现直接创建 `CONFIRMED` |
| QQ confirm | 另一个 scope；对上述已确认事实是重新确认，不能称作首次状态提升。若需测待确认提升，另批已有 HYPOTHESIS 与来源，不私自降级事实 |
| 纠正 | 新批准绑定旧 ID、完整修订族、准确纠正文本/来源；新值为当前，旧值 `SUPERSEDED`，直接及间接查询分别记录 |
| 删除前备份 | 单独批准精确备份对象、读取/加密/复制范围、目的地和密钥保管；在遗忘前制作，不能由写记忆批准隐含取得备份权 |
| 遗忘 | 新 `memory.forget` 权限、准确完整共享来源/修订/派生闭包；Ledger 先追加 fsync，再删除；记录当前 epoch、回执和独立对照 |
| 隔离恢复 | 单独批准新目录或实例，在启动前技术禁止 QQ/模型/来源服务出站；恢复已批删除前加密备份，先回放最新 Ledger，再重建 FTS/BGE |
| QQ 只读复问 | 纠正后与遗忘后分别申请固定新窗口、准确事实/查询范围和模型/发送额度；phone 可见与 SENT 分列，不复用管理 scope |

纠正后的只读复问应安排在遗忘之前；遗忘后的复问应回答未知/无证据，且不得泄漏旧值或纠正值。二者若分处窗口，各自重新批准，合计费用与 UNKNOWN 沿用原责任日志，每窗口仍最多五次模型尝试，不能靠重开窗口重置责任。

成功必须同时记录原始事实状态、当前 ID 和替代链、直接/间接检索结果、Ledger chain、privacy epoch、幂等回执、STOP 与独立对照。公开只发布匿名统计/摘要；真实命令、正文、原 ID、路径、账号、账单和审批原件只保存在分别获准的私密目录。

## 删除和故障处置

旧备份恢复先核对原 Ledger identity/head、schema、恢复 generation 与完整 Budget/Window/Spool custody。恢复是新的隔离目标；原库、原卷和原预算/窗口日志不覆盖，旧 grant 不复用，UNKNOWN 不退款，旧窗口不恢复额度。目标事实、旧修订、原提示/history/summary、向量、traits、时间线/标签/Inspector 派生视图均按实际删除闭包检查；导出的私有材料按其独立保留/删除合同处理，不能由数据库删除假定已全部消失。

| 故障 | 预期与验收方式 |
| --- | --- |
| 成功回执丢失、认证重连、同一原始输入重放 | 私下核对同一幂等回执；不产生第二次写入、模型或回复 |
| 不同原文复用旧批准、错误 owner/事实 ID、跨主体、未知来源 | 在提交前拒绝，不写无来源事实 |
| 目标/epoch/配置/STOP 漂移或 scope 到期 | 当前操作停止，新批准重新绑定；不能自动续签 |
| Ledger 已 fsync 但 DB commit 失败 | 不报成功；先应用最新 Ledger，旧 epoch/scope 拒绝 |
| 并发遗忘、旧备份缺回执或旧 generation | 原删除语义保持幂等；恢复后的旧 grant 不重新消费 |
| 不确定费用、发送、停止或恢复证据 | STOP，保全原日志，按准确责任核对，不盲重试 |

上述边界由既有 `tests/test_memory_control.py`、`tests/test_p12_memory_lifecycle.py` 等回归覆盖。本轮未发现需要更改该生产入口的具体缺口，继续复用原 owner/private 认证、HTTP v1、单次 scope、租约、队列和删除链。

## 证据分栏

| 层级 | 本轮状态 |
| --- | --- |
| 实现 | 原 Memory/控制/备份路径复用；本包生产代码变化 0 |
| 离线 | 新 F2 合成候选对照与纠正/遗忘/旧备份重建已实际执行；结果见报告 |
| 协议 | 原认证本地 WS/HTTP 管理回归；不等于真实 SnowLuma/QQ 验收 |
| 供应商 | 模型 HTTP 尝试 0，实际 Tokens/金额/账单 `null` |
| 手机 | 记住/确认/纠正/遗忘/复问签收均 `NOT_RUN` |
| 跨日 | 真实自然日和长期召回样本 `null` |
| 恢复 | 本地合成新路径与最新 Ledger 重建通过；业务旧备份/QQ 卷/主机恢复 `NOT_RUN` |
| 发布 | 独立 Draft PR 和准确 HEAD 完整 CI 由 P13 集成交付记录；未部署、未合并 |

F2 本轮 15 条查询中，原 FTS+BGE 找回 13/13 必要记忆、误报 15 条；标签/一跳同为 13/13、误报 21 条，候选字节增加约 20.1%。无净收益，保留原路径。原 P12 6/7 和历史 P8 7/8 指标不改写，真人效果仍需后续独立实测。

## 本地验证

Windows / Python 3.12，已有锁依赖和 pinned 本地 BGE 缓存。新实验运行器完整执行一次，结果含输入 hash、逐例候选和全部删除/恢复读数；临时数据库、备份和恢复库退出时清理。

`./scripts/check.ps1 -TestFiles tests/test_p13_memory_research.py,tests/test_memory_control.py,tests/test_p12_memory_lifecycle.py,tests/test_memory_commands.py,tests/test_memory_workflows.py,tests/test_erasure_coordination.py,tests/test_live_memory_recall.py`：全仓 Ruff 通过，**131 passed**（58.18 秒）。已有 Starlette/httpx 弃用提示 1 条，无失败。全部 7 个本包公开 Markdown/JSON 文件另做 JSON 解析和现有 privacy-category 扫描，未发现命中；`git diff --check` 通过。完整组合与各 Draft PR exact HEAD CI 另行记录，不把离线结果写成真人通过。
