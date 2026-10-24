# R1 固定上下文交流指导实验

当前交付是离线实验准备工具。真实模型回复、人工评分、体验改善和 QQ 验收均为 PENDING。
诊断、源码基线与一手参考见 [diagnosis.md](diagnosis.md)。

## 比较对象与冻结条件

`baseline` 保留原有指导；`participation_v1` 仅替换 Expression 的完整回复措辞，
再追加脚本中可审阅的交流指导。这是一个组合策略比较，不能据结果单独归因于其中一句。
它不包含示例答案，也不改变生产 Prompt。

先由原生 `Core.context` 构建上下文，再生成两个系统指导版本。每对的非系统消息、
事实、历史、当前输入、模型和输出上限相同。追加指导发生在上下文选择之后，不重新裁剪，
因此比较不混入历史缺失差异。运行前须检查两侧输入估计均在获批上限内；超限就停止，
不能单独删减一侧历史。计划记录 payload、共享上下文与整个计划的哈希。

使用现有 `deepseek-flash` 非 thinking 配置，输出上限 2048，软上下文 16000。
请求没有 seed 或 temperature；重复结果可能不同，不声称采样可复现。
36 个仓库 case 全是 SYNTHETIC，覆盖 7 类场景。合成事实只用于构建实验输入。

真实 pilot 使用本地受保护的 5 个连续主人 turn 与实际 SENT 片段。前 4 个 turn 构成
可见历史，第 5 个主人输入为比较目标；第 5 个旧回答不进入输入。未发送内容不成为历史。
没有历史完整 provider payload，因此这是可见对话重建，不能声称复现旧请求。

## 离线使用

在本工作树运行 Python；可用仓库已安装的虚拟环境。输出目录必须是本工作树 `.local`
内的全新目录，不能覆盖已冻结计划。已有 pilot 和合成计划应直接复用。

```powershell
python scripts/evaluate_r1_conversation.py prepare --output .local/r1-synthetic-plan-new
python scripts/evaluate_r1_conversation.py prepare --private-baseline <本地受保护原文路径> --repetitions 3 --output .local/r1-private/pilot-new
python scripts/evaluate_r1_conversation.py blind --plan .local/r1-private/pilot/plan.json --observations .local/r1-private/observations.json --output .local/r1-private/blind
```

`prepare` 不调用模型或发送消息；`blind` 只检查输入绑定并导出已有观测。该脚本没有真实
执行子命令，不能用准备成功替代付费准入。私有输出继承目录的访问控制；`.local` 被 Git
忽略并不等于访问控制，原文、请求、回复、映射和凭据只保存在受保护本地目录。

观测文件是 JSON 数组，每个声明的请求必须恰好有一行：`case`、`repetition`、
`strategy`、`payload_sha256`、`evidence_type`、`state`、`text`。
`evidence_type` 为 `MODEL_RESPONSE` 或 `FAILED_ATTEMPT`；`state` 为 `SETTLED`、
`UNKNOWN`、`FAILED` 或 `NOT_STARTED`。失败项的文本为空；不得用合成回答补齐。
观测身份必须来自实际执行收据，导出器的字段检查本身不证明远端调用发生过。
完整账本另行保留 request ID、usage、成本与错误；盲评输入不暴露策略标签和账本身份。

## 预先登记的盲评规则

先冻结计划与以下尺度，再生成回复。按计划交错运行策略；导出器平衡 A/B 位置，并将
策略对应表单独写到 `mapping.private.json`。评分者只看 `ballot.json`，完成全部评分
并保存后才揭盲。相同 case 的重复不是独立样本，汇总时按 case 分组。

每项 `ratings` 填入 `{"A": 分数, "B": 分数, "reason": "具体依据"}`，使用 1–5 分：

| 维度 | 1 分 | 3 分 | 5 分 |
|---|---|---|---|
| naturalness 自然度 | 明显生硬、错接意图 | 能接话但有模板感 | 贴合当下语境、表达自然 |
| desire_to_continue 交流意愿 | 明显降低继续交流意愿 | 无明显影响 | 愿意继续说；收尾时也尊重结束 |
| deliberateness 刻意感 | 无明显表演或迎合 | 有些用力 | 明显说教、表演或机械追问 |
| factual_accuracy 事实准确性 | 编造或严重误归属 | 有不确定或小误读 | 可核对事实、主体、记忆与边界均准确 |

刻意感越低越好，其余越高越好。没有可核对断言时，准确性填 `null` 并注明“不适用”，
不能自动给满分。`overall` 填 `A`、`B`、`TIE`、`BOTH_BAD` 或 `UNSCORABLE`。
`failure_notes` 记录错误记忆、虚构亲历、主体混淆、无必要解释/建议/反问、忽略收尾等。
不得因回复短就判好，也不得因评分者喜欢某种语气而忽略事实错误。

只有两侧均 SETTLED 且有实际文本的 pair 可评分。失败、UNKNOWN、未启动、截断或空回复
保留在总分母及失败表中；不能隐去失败后只展示成功样本。截断由执行收据中的结束原因
人工复核，导出器并不检测截断。UNKNOWN 停止后续真实调用，不自动补跑。

6-call pilot 是同一 case 的 3 对重复，只用于检查策略方向、输出与评分流程。
后续至少 30 个独立 case 的配对（当前计划 36 对、72 请求）需要独立费用授权。
完整报告列出各维度分布、配对差、胜/平/负/两者都差、不可评分数、类别失败与实际费用；
不得将 3 次重复当作 3 个不同真人样本，或将模型自评当作主人反馈。

在看到全部评分前不决定上线。新增事实/权限错误须先解决；自然度与交流意愿改善且刻意感
不恶化才有理由讨论最小可独立关闭策略。结果混合则报告具体失败类别并重新登记实验，
不能只挑成功回复。生产策略、部署、主动交流与真实 QQ 验收仍分别待后续决定。

## 六次真实 pilot 的拟授权与执行方式

拟请根聊天统一申请：将上述有限私聊重建输入发送到现有 DeepSeek provider，固定现有
模型，只执行已冻结计划的 6 次请求，顺序执行，无自动重试；估计合计约 $0.0183，
拟批准窗口额度 $0.05。估计不是账单，也不是 provider 的硬费用保证。
当前并未获批；72 次合成计划不包含在这份申请中。

授权后才准备真实 executor，并先用 mock provider 验证。复用 `PaidModelApproval`、
`budget.reserve` → `budget.complete` → `ModelClient`；不直接另写 HTTP 计费旁路。
使用专属实验数据库与追加式 custody journal，不能重置或借用生产账本来绕过累计额度。
若现有账户有累计预算合同，继续遵守并绑定其 custody，不能用新数据库清零已花费用。

具体限制映射：`window_calls=daily_calls=6`、`concurrency=1`、`per_request_calls=1`，
`window_micro_usd=daily_micro_usd=monthly_micro_usd=50000`，
`per_attempt_micro_usd=per_request_micro_usd=5000`，`max_input_tokens=2400`，
`max_output_tokens=2048`。沿用计划的配置费率与 125% 输入估计余量。
批准时间窗、费率有效期、账户累计约束及凭据绑定必须由真实授权材料确定，不伪造已批准字段。

执行前核对 plan hash、每项 payload hash、共同历史 hash 与实时来源有效性；每项用稳定
logical request ID 绑定计划和序号，原样发送冻结 messages。每次结算后检查 usage 和剩余额度，
UNKNOWN、异常 usage、来源撤回、到期、预算拒绝或停止要求均中止，不补偿性重试。
重启时先核账本；已 dispatch 却缺失本地回复的项保留不确定证据，不再次付费生成。
实际回复只写本地观测，不进入 Outbox，也不写回生产记忆。

这段是可审阅的复用方案，不是已实现或已验证的真实执行器。mock 验证与授权材料齐备后
才能安排真实调用；当前离线 PR 不引入新的预算、任务或发送机制。
