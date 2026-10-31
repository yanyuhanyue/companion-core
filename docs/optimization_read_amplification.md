# Life 完成事务与查询成本

`Store` 使用 SQLite WAL 和 `BEGIN IMMEDIATE`。目标完成、实际回执和兴趣来源必须
在同一事务提交；`GoalEngine.finish()` 的直接调用也遵守这个合同。将
`record_exploration()` 整体移到事务外，会留下 DONE 已提交、兴趣来源尚未写入的
崩溃窗口，并允许来源撤回插入两者之间。

SQLite 的读取页面不会因为被读取就写入回滚日志。WAL 追加的是修改页；长读取事务
可能阻碍 checkpoint 前进，但这不等同于“每次 SELECT 扩大回滚日志”。参见
[SQLite WAL](https://www.sqlite.org/wal.html) 和
[rollback journal 的写入流程](https://www.sqlite.org/lockingv3.html#the_rollback_journal)。
因此不以减少 SELECT 数量为理由拆开完成与来源的原子提交。

当前代码复用 `interest.exploration_evidence()` 核对完成的 self 目标、真实读取状态、
内容 hash、规范 URL、来源闭包、保留期和模拟标记。Life 决策上下文还要求目标根来源
和最终回执都在本次授权来源内。日记或自主探索决定不参与事实/兴趣投票。

查询优化应根据已有 `Store.transaction` 的无正文锁等待/持有时间遥测和实际查询计划
定位成本，并保持提交时的来源、租约、隐私 epoch 和执行结果校验。耗时外部 I/O
继续在事务外完成。本轮没有性能基准，不能给出事务体积或吞吐量提升百分比。

`tests/test_life_autonomy.py` 注入兴趣写入后的异常，验证目标完成、步骤状态和兴趣
来源一并回滚，再用原有效租约提交一次。原 `test_goals.py`、
`test_growth_foundations.py` 和独立 Reader/Life 计划测试覆盖真实回执和去重。
