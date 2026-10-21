# 已停止文字窗口的显式交接

受控文字窗口停止后，只有新的独立批准可以建立后继窗口。
交接沿用最新 Core DB、Spool、删除 Ledger、稳定 protocol epoch 与原 Budget Journal；
旧窗口及其输入、发送尝试、费用和停止记录继续保留。

后继计划的 `live_text.handoff` 固定前一窗口的 plan/window、两个 Journal 链尾、
身份与 epoch、当前 privacy epoch、最新 Ledger 摘要和新授权证据摘要。
这些字段参与计划、Core 配置批准和 Bridge 授权的摘要计算。
HTTP 请求只引用已配置的新计划，普通 Bridge token 不能另外提交一份替换范围。

## 两个独立步骤

先使用后继计划执行 `window-handoff`。Bridge 核对旧 STOP、Core 停止回执、
最新日志与 Spool，一切在途输入、发送或结果均须已收敛；Core 另查未结算费用、
作业、Outbox、删除记录及原账本身份。任一项不明确就拒绝交接。

Core 在原 Budget Journal 追加交接记录，再提交 PREPARED 历史。
Bridge 先记录交接请求，获得 Core 回执后，在原 Window Journal 追加退役记录、
建立有前驱引用的新 Journal，并保留旧窗口快照。此时后继仍关闭。

随后单独执行 `window-open`。Bridge 使用独立接收密钥签署已经持久提交的交接回执，
Core 验证该回执与已批准的新计划、Core PREPARED 回执一致后才开窗。
正式开窗在写入窗口记录、调用 Core open 之前，先通过现有 OneBot HTTP 的
身份、online/good 和版本探测；探测不打开 WS，也不发送消息。
宿主操作仍须在停旧服务、复制和物化预算之前另外检查协议就绪。

命令沿用原 `--config`、`--window`、`--inventory` 和 `--authorization` 私密文件参数。
具体计划、时间与限额必须来自本次批准，不能重复使用已经结束的运行批准。

## 保留与失败处理

Core 的窗口历史保存在现有 metadata 中，独立 Budget Journal 的生命周期与数据库互相核对，
包括没有任何模型调用的窗口。旧库缺少已经持久化的开窗、停止或交接记录时，仍会被拒绝。
原窗口的未结算责任、日/月费用与次数不会因交接减少。
恢复包含文字窗口绑定或生命周期的备份，即使模型 admission 为零，也会标记需要预算恢复核对；
备份中的 OPEN 或 PREPARED 状态不能自行恢复未来开窗权限。

Bridge 为输入与发送尝试记录 plan 归属，额度按当前窗口计数；event/message ID 去重仍跨窗口保留。
第一轮的 `window.stop` 保留，后继使用各自的停止文件。旧停止命令不能重新开启旧窗口或改变新窗口额度。
历史对话仍仅来自当前窗口批准且已经 SENT 的完整轮次，交接不自动授予旧窗口正文的使用权。

只出现 CAPTURE_STARTED 而缺少完整终态，或 Journal 与数据库链尾不一致时，不能交接。
协议探测失败后已完整写入 CAPTURE_FAILED_CLOSED 停止记录、Core 停止回执，且没有待确认输入/发送，
属于已结束的捕获；保留这段失败历史后可以申请后继窗口。

跨 Core 与 Bridge 的操作不是原子事务。日志 fsync 后数据库提交失败、HTTP 回执丢失或缺少
后继开窗回执时，状态保持关闭或未确认，不自动重试、补发、删除标记或重建额度。
同时回滚所有数据、独立日志及外部托管证据仍超出本地检测能力；缺少最新保全材料时不能继续。

旧 Core 的零输入窗口没有独立保存 bot/协议 epoch。该来源由 Bridge 的旧计划与稳定 Spool 身份核验，
再通过签名交接回执绑定给 Core；Core 不从不存在的字段推断旧身份。
本实现的合成验证与 [真实运行记录](p10-runtime-observations.md) 分开记录。

## 验证

父级完整 `./scripts/check.ps1` 为 1559 passed、6 skipped、1 个既有 warning，330.50 秒，Ruff 通过。
随后独立复核补齐了探测前最终时限/停止检查和零调用窗口的备份恢复阻断，并对原失败反例及受影响范围做了定向复验。
最终提交由 GitHub 全套 CI 再验；各定向范围有交集，不相加成另一个测试总数。

另用固定上一版 `1e10535fd11a66b5b4e0fbca724c20a1b77ce2cc` 的实际代码，
生成旧 schema 的零输入捕获失败窗口，再用新代码完成交接、单独开窗、旧停止隔离和新窗口停止。
该合成检查保留旧停止与同一预算身份，没有调用真实协议、模型或 QQ。
