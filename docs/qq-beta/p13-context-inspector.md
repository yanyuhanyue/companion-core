# P13-F3：只读上下文选择诊断

沿用 `companion inspect-context --trace-id`，只在受鉴权本地材料范围读取现有 SQLite。
本轮未执行任何真实数据库读取；测试使用合成资料与本地认证协议。
没有 Console、新索引、检索重跑、模型调用、日志回填或运行授权变化。

原 Inspector 已展示冻结历史/记忆/图片的匿名引用、模型尝试、实际 Token 与费用责任。
P13 新增每个聊天 job 的 `memory_selection`，使用原 `live_text_memory:<job>` 记录，
核对事件、privacy epoch、候选/选择/历史摘要及对应已结算 memory_review/payload。
最终 chat context 仍须与同事件、计划、epoch 和选中记忆一致；所有校验在一个只读事务中。

`trace.recorded_window` 从事件原输入记录的 window 摘要找到原历史批准，核对保存的批准
摘要、计划和 privacy epoch，仅投影候选/计划/批准摘要与执行模式。它与报告顶部明确标为
`configured_*` 的当前配置分别展示；旧事件不能被当前窗口重新归属。缺失、损坏、过期或
redacted 状态都不补猜历史批准，仍不验证外部授权原件。

| 状态 | 准确含义 |
| --- | --- |
| NOT_RECORDED | 没有持久选择记录；不能推断没检索或没记忆 |
| REVIEW_PENDING | 已冻结非空候选，尚无完成选择记录 |
| NO_CANDIDATES | 记录中的候选为空；不推断整个事实库为空 |
| SELECTION_RECORDED | 有同 job 已结算复核与一致的选择摘要；不证明供应商或真实授权 |
| STALE_EPOCH / REDACTED | 已撤销或版本变化，隐藏候选、旧上下文引用及内容 |
| INCONSISTENT | 记录损坏、超界、摘要/事件/复核/上下文不一致，不展示候选 |

每个有效候选仅导出 memory ID 的 SHA-256 与记录内的去向：复核待定、复核未选择、
已选择但 chat context 尚未记录、进入最终 chat context、或未进入最终 chat context。
候选/选中/最终数量分别记录；尚无最终上下文时最终数量为 null，不冒充零。
选择模型的语义理由没有持久化，`selection_reason=NOT_RECORDED`；未保存的历史淘汰
列表同样 `discarded_history=NOT_RECORDED`。不能猜成 Token 超限、相关性低或来源无效。
历史最终引用仍由原 `context.history` 展示，模型 Token 与尝试数由原 usage 展示。

`source_validity=NOT_REVALIDATED` 始终保留：诊断不重新验证授权、来源或独立账本，
不替代运行准入。UNKNOWN/RESERVED 仍只显示责任金额，不伪称实际 Token 或供应商账单。
输出不含明文、旧签名、URL、账户或路径；散列仍是可关联标识，对外分享前需再匿名化。
遗忘后当前输出抑制旧引用；已有私人导出不会自动删除，应按既有保留/删除合同处置。

回归覆盖空/待定/选中/最终差异、被遗忘/epoch变化、错误摘要/事件/重复候选/外来选择、
未结算或错误 payload、损坏/超长记录及数据库不变；另在原本地认证记忆召回链中验证
实际冻结记录的投影，仍使用 Mock provider，不是付费或 QQ 真人证据。
