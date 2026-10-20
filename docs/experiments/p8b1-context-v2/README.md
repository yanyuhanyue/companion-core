# A5 下一轮上下文合同合成基准

结论：`CONTRACT_PASS_NO_PRODUCTION_BENEFIT_PROVEN`。30个公开合成样本，必要参考19/19，禁止参考可见0，装配违规0。只验证本地路由、SQLite内存目录、来源过滤和资料装配；不是生产语义评测、降费结果或设备E2E。

2026-10-20 12:35:28 UTC / 20:35:28 Asia/Shanghai，工作树观察HEAD为 `bbd712c1a79497762275e718e9f76959bedf6583`。父流程负责当前集成候选冻结/CI与全量检查；本轮不修改产品源码、迁移、CI、原#26样本/脚本/结果，不启用功能。

## 输入和边界

- [legacy-cases.json](legacy-cases.json)：原#26 `1346fa29cb1f63651377dc33525a09fc96cd526a:tests/fixtures/p8b_context_cases.json` 的独立只读快照，12例完整保留。canonical SHA256为 `12a73ddac13a1188113c2c8a4840a6a19959a75425961e6d7a3eefd62f374d4c`，启动时强制校验。当前原fixture与快照结构相同。
- [next-cases.json](next-cases.json)：18个新增正负例与有来源的一跳关系。全部人名/任务/证据ID为公开合成；不是匿名真实聊天。
- [run.py](run.py)：只用Python标准库，SQLite `:memory:`，不import Core/模型，不创建provider、encoder或QQ client；CLI审计钩子拒绝socket连接/解析/发包和子进程。
- [test_benchmark.py](test_benchmark.py)：8条独立回归覆盖原失败、prospective正负例、真实指标空值、gold隔离、未见句式的保守升级、候选/参考上界、可成立的二跳拒绝及多证据依赖撤销。
- [results-final.json](results-final.json)：逐例、聚合、脚本与数据hash、历史失败。没有字符/字节到token的换算，没有演示费率或假成本。

runtime候选ID是fixture提供的检索替身，`owner_text`是fixture已分好的主人片段；旧fixture仅用明确`引用：`前缀和首个句号之后的后缀做受限合成分段，不按case ID路由。本次没有证明真实FTS/BGE召回或真实引用解析。未知问题有候选时保守升级L3，可能增加不必要核对；无候选则澄清/说明未核实，不为了空候选调用review。L3仅提出核对，实际没有模型核对/答案生成。保留两条冲突参考不代表模型最终答案正确。

旧规则回放只复制原#26的`planned_load`分支，用于隔离路由漏例；两臂共用新合同的来源过滤/装配与同一组输入。它不是原Core baseline重跑，尤其没有复现Core soft-bound裁剪或原oracle review。原#26 baseline压力漏例继续作为历史失败保存，不能被新装配通过覆盖。新旧四层含义不同，v2层级断言只评新臂。

派生边只支持有共同证据的`co_documented`关系，不从标签创造边；只遍历初始种子一跳，最多6种子+2邻居、最终6参考、6个合成SENT历史槽。槽数不是实际turn配对、tokens或费用上界。当前输入与规则保留，先丢旧历史；这里只检查结构，硬token/费用准入仍必须由生产预算合同执行。

`read_revoked`历史继续有效，原`revocation`例要求保留`retained`并拒绝`forgotten`。`processing_revoked`仅为未来“禁止历史处理”提案的合成阻断标记，不是现有SourceGrant枚举；当前真实`revoke()`保留历史，另行`forget()`才写ErasureLedger删除闭包。恢复/并发样本只是set/epoch变化模型，没有读写真实Ledger、预算或恢复卷。

## 配对结果与保留的失败

| 数据/方法 | 必要参考 | 失败/违规 | 说明 |
| --- | --- | --- | --- |
| 原#26实际baseline，历史未重跑 | 7/8 | `pressure:tea`缺失 | Core先裁参考，原读数不变 |
| 原#26实际experiment，历史未重跑 | 7/8 | `indirect:tea`缺失 | “饮料还是老样子吗？”误判L0，原读数不变 |
| 原12例的旧规则回放 | 7/8 | `indirect`缺失 | 仅路由模型，与上面历史原运行分栏 |
| 原12例的v2合同模型 | 8/8 | 无缺失/禁止参考 | 未运行Core或真实检索 |
| 全30例的旧规则回放 | 12/19 | 6例有缺失、1项禁止参考可见 | 新查询/引用片段下的已知反例；不是原#26原始成绩 |
| 全30例的v2合同模型 | 19/19 | 缺失0、禁止参考0、装配违规0 | 仅这组公开合成合同 |

v2提出额外核对的样本3，旧规则回放为2；这些是规则标记，不是实际调用。不能把修补回忆后的合同通过解释为成本下降；当前显式接受更保守的核对取舍，后续必须测误升级率与真实费用。

新增正例：间接改写、未知句式有候选、多指代、任务接续、引用后主人追问、有据一跳。负例：仅引用、同标签、无支持边、有效二跳、其他owner/public/缺证据、历史处理撤销提案、最后装配前forget/epoch变化、恢复未重放/已重放删除模型、无记录与不确定送达历史。压力输入只在装配模型中检验参考保留，不宣称修好了生产压力缺陷。

所有真实provider usage、input/output/cache-hit、费用/核账、费率/币种、tokenizer均`null`；provider与手机端延迟均`n=0,p50=null,p95=null`。没有本地CPU/RSS或时延基准，本轮不生成p50/p95代理数字。

## 可重复执行

从本worktree根目录，用已有Python≥3.11；无依赖安装、模型下载或网络前置。`--output`只能在该worktree内，且必须尚不存在，避免覆盖证据。选择新的输出文件名：

```powershell
python -B docs/experiments/p8b1-context-v2/run.py --output docs/experiments/p8b1-context-v2/rerun-local.json
python -B docs/experiments/p8b1-context-v2/test_benchmark.py
python -B -m ruff check --no-cache docs/experiments/p8b1-context-v2/run.py docs/experiments/p8b1-context-v2/test_benchmark.py
```

冻结`results-final.json`由实际CLI生成，脚本退出0；30例/19参考通过、provider HTTP=0、QQ尝试=0。标准库回归8项通过；Ruff通过。第一次Ruff指出两处行长，修复后重跑通过；独立复核要求移除按case ID分段后，以新路径生成最终结果。未隐藏失败或将反例改为预期失败。初次29例与定稿前30例的自有可再生结果已清理，仅保留最终意图结果，不覆盖原#26材料。

最终定向检查实际输出：`Ran 8 tests in 0.168s / OK`；Ruff `All checks passed!`。另以标准库重新计算完整结果，确认与冻结artifact完全相同、原fixture结构未改、所有本轮文件无行尾空白且没有`__pycache__`。最终脚本SHA256为 `86a4d64d75b6e25e634684550d08efb38897cb15d9bb080acc5e3a476d2b2400`；新增fixture canonical SHA256为 `650536d3f66aacb75f2df2d9e7c5fea2875f5a4afd3e7d572c5f47652f59ab1c`。

本轮未运行产品全量`check.ps1`、真实embedding、provider、QQ、VPS、Reader账号、Windows/Android/DSH或真实恢复；完整集成检查由父流程独立记录。没有生成磁盘数据库、模型缓存、pycache或pytest临时数据，`-B`保持该实验不写编译缓存。RFC与后续具体执行入口见 [设计](../../design-future/p8b1-future-rfc.md) 和 [依赖/验收backlog](../../design-future/p8b1-backlog.md)。
