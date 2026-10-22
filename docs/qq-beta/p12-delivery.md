# P12 A–E 离线交付与准入状态

P12-00、A–E 的五份 PR 已按主人明确批准全部合并，分支清理已完成。
合并后的完整产品 main 为 `7568a2377806f98f286efe0aeac4b69a0b7eae73`，
文件树与已通过本地完整检查的集成候选完全一致。
其 [main CI 39718418701](https://github.com/yanyuhanyue/companion-core/actions/runs/39718418701) 全部十项检查通过，工程合并验收完成。
真实首批验收、多自然日和持续运行均未执行。

`private_beta_ready=false`，`production_ready=false`。
本轮未连接 VPS、读取或复制业务库、下载真实 QQ 图片、调用付费模型或发送 QQ。
P11 的 6 输入／8 调用／6 手机确认仍只属于原已结束窗口。

## 固定候选与变更范围

开发基线 main 为 `c6493d481676e1e6684fb49564c2557224950d20`。
该基线文档 CI 为 [39707468712](https://github.com/yanyuhanyue/companion-core/actions/runs/39707468712)；
P11 已合入候选与历史 CI 仍见 [基线](p12-baseline.md)。

| 包 | 已合并 PR／最终 HEAD | 行为与主要文件 | 本轮证据 |
|---|---|---|---|
| A | [#58](https://github.com/yanyuhanyue/companion-core/pull/58) · `96f697b8e565534a2cefd78031674b7792f6e8ec` | Bridge `images.py` 限定普通原图字段；Core `qq_rich.py` 明确 ID/元数据语义；媒体测试与申请模板 | `OFFLINE_PASS`；图片真人 `NOT_RUN`，全文深读 `DISABLED` |
| B | [#60](https://github.com/yanyuhanyue/companion-core/pull/60) · `4c811d4978f533f19cd0f4ddf8eb13e827ab2a20` | Core/Bridge CLI、`api.py`、`memory_control.py`；`memory.py` 原子事务；`backup.py` 恢复后重新批准；原生生命周期和检索评测 | `OFFLINE_PASS`；认证本地 WS/HTTP；业务 R5、QQ 控制 `NOT_RUN` |
| C | [#57](https://github.com/yanyuhanyue/companion-core/pull/57) · `a400ae1bd5a4b43c4e0aa0384507670cbe724e42` | `affect.py` 明确反馈；`interest.py` 模拟来源；`reflection.py` 祖先来源 TTL 与 Ledger | `OFFLINE_PASS`；真人反馈与独立自然日 `NOT_RUN` |
| D | [#59](https://github.com/yanyuhanyue/companion-core/pull/59) · `1af744c0db9e62aaa4fb2c75cf38a0e825ed4bea` | `api.py` 私有视图 no-store；`goals.py` 来源与执行/送达读视图；`social.py`、`transport.py` 最终分享重验 | `OFFLINE_PASS`；真实日记、Reader、主动分享 `NOT_RUN` |
| E | [#61](https://github.com/yanyuhanyue/companion-core/pull/61) · `6c2b0098fd06c9ef48607d0f979faf5967188451` | `beta_observation.py`、原反馈 schema/validator、`summarize_beta_feedback.py`；门禁、配置、恢复与长期限额设计 | E3 `OFFLINE_PASS`；E1/E2/E4 文档 `DONE`；常驻代码与恢复实机 `NOT_RUN` |

上表记录合并前最终 PR HEAD；对应 squash 提交和 main CI 分别记录。
后续交付文档提交不改变已验证产品代码，其精确 HEAD/CI 见合并清理回执。
完整逐项状态、实际历史镜像 ID、最新已知三类日志摘要和未批准项见 [p12-gates.json](p12-gates.json)。
没有新部署镜像或运行配置，P12 实际镜像/配置为 `null`；历史 Docker image ID 不冒充 registry digest。

## 验证与保留的失败

| 验证 | 实际结果／范围 |
|---|---|
| A 本地完整 `./scripts/check.ps1` | Ruff、2021 passed、6 skipped，548.24 秒 |
| C 本地完整 `./scripts/check.ps1` | Ruff、2002 passed、6 skipped，562.20 秒 |
| B 定向与最终增量 | 189 项相关回归；两个新增文件 46 passed；修正旧迁移断言后 52 passed（55.74 秒）；范围重叠，不相加 |
| D 定向与最终增量 | 167 项相关回归；最终分享修复后 62 passed；范围重叠，不相加 |
| E 最终定向 | Ruff、39 passed，4.02 秒；空清单真实 CLI 成功 |
| A+C+D+E+B 组合完整检查 | `24423869d31c2a2aab670e3bc4847e33315702cb`：Ruff、2116 passed、6 skipped，748.09 秒 |
| 五份 PR 完整 CI | 上述实现 HEAD 全部 SUCCESS；Linux/Windows 两分片、quality、container、browser-reader、plugin-native、preflight、最终 ci 均通过 |

B/D/E 未另行重复单分支本地全量；最终组合全量和各 PR 完整 CI 覆盖运行交互。
没有把多轮相同测试相加。该本地检查当时使用独立工作树；合并产品提交 `7568a23` 的文件树与该交付候选一致。首轮组合检查在发现旧迁移断言遗漏后主动中止，未计为通过；上表为修正后的完整重跑。


实现候选完整 CI：[A 39714809883](https://github.com/yanyuhanyue/companion-core/actions/runs/39714809883)、
[B 39716121427](https://github.com/yanyuhanyue/companion-core/actions/runs/39716121427)、
[C 39713815747](https://github.com/yanyuhanyue/companion-core/actions/runs/39713815747)、
[D 39714820874](https://github.com/yanyuhanyue/companion-core/actions/runs/39714820874)、
[E 39717587052](https://github.com/yanyuhanyue/companion-core/actions/runs/39717587052)。
E 最终交付文档 HEAD 的完整 CI 已包含在上表；合并后的 main 单独检查。
B 首轮 CI 39715581335 在两平台暴露旧备份断言遗漏：新恢复标记有意增加一行元数据，
测试却仍要求整表不变。修正后明确验证新增标记并保留全部旧行、删除、预算和投递断言；
没有为通过测试修改产品行为。

独立审查修复了 E 两个反例：同一 trace 在人工记录和 Inspector 记录之间重复计数，
以及部分 STOP 与双端确认混淆。两种输入顺序和部分停止用例均验证闭合。
D 在最终发送前补上候选仍有效时 action/origin/私聊目标变化的拒绝；
已发生 I/O 的 SENDING/DELIVERY_UNCERTAIN 保留不确定，不变成可重发。
B 旧备份没有命令消费回执且 epoch 相同时，原批准可能被重用；
恢复增加独立控制 generation，使旧 scope 失效，原预算/窗口记录保持不变。

B 的 50 条公开合成事实、9 条留出问题包含 7 项必要记忆，召回 **6/7**，
保留“休息日早上我一般去哪锻炼？”漏例；候选误报 0。
3 条历史措辞探针找回 3 项必要记忆，同时多出 1 条候选。
纠正后旧值、来源遗忘及旧备份最新 Ledger 回放后的索引重建泄漏均为 0。
这些是检索候选指标，不能与历史完整上下文 7/8 拼成提高率。
原检索未替换，图增强仍未准入；详见 [检索实验](https://github.com/yanyuhanyue/companion-core/blob/4c811d4978f533f19cd0f4ddf8eb13e827ab2a20/docs/experiments/p12-memory/README.md)。

## 权限、使用与回退

B 新入口仅用于独立的一条主人记忆命令，使用原 `control_token`，默认无 grant。
新 scope 绑定准确操作、事实/目标闭包、当前正文、owner/bot、代码/配置、capture 公钥、
epoch、Ledger、原 STOP 与完整预算/窗口责任，最多 15 分钟／1 命令。
业务模式另需 B1 签收与准确批准；没有启用普通聊天的自动记忆提取。
管理操作不调用模型、不发送消息。普通有界窗口仍最多五次模型尝试；图片占两次。

所有 P12 真实权限为 `null`。新真实模型调用/QQ 发送为 0；
实际 usage、cache、账单、手机确认、E2E P50/P95、日记质量及自然日样本均为 `null`。
没有新运行窗口或预算重建，当前服务器状态为 `NOT_RECHECKED`，
最后保存的双端停止与账本证据保持历史含义。

真实准入材料分别见 [图片](https://github.com/yanyuhanyue/companion-core/blob/96f697b8e565534a2cefd78031674b7792f6e8ec/docs/qq-beta/p12-single-image-request.template.json)、
[R5](https://github.com/yanyuhanyue/companion-core/blob/4c811d4978f533f19cd0f4ddf8eb13e827ab2a20/docs/qq-beta/p12-r5-request.template.json)、
[C](https://github.com/yanyuhanyue/companion-core/blob/a400ae1bd5a4b43c4e0aa0384507670cbe724e42/docs/qq-beta/p12-growth-shadow.md)、
[D](https://github.com/yanyuhanyue/companion-core/blob/1af744c0db9e62aaa4fb2c75cf38a0e825ed4bea/docs/qq-beta/p12-diary-life.md) 和 [恢复](p12-recovery-request.template.json)。
模板均缺实际目标与新授权，不可执行。
E2 只完成日/月/账户累计硬限额的后续合同；E4 只完成准确对象的恢复补足计划。
QQ 登录卷、原宿主服务、整机恢复和 3/7/14/30 天自然日体验仍须新范围与真实证据。

回退先停新增动作并关闭相应能力，保存不确定投递与 UNKNOWN 责任。
保留最新 ErasureLedger、全部 Budget Journal、完整 Window Journal/Spool/epoch；
旧 DB 或旧二进制不能恢复额度、权限或待发送消息。恢复使用技术隔离的新目标，不覆盖原业务卷。

## 合并与分支清理

五份 PR 均为 MERGED，开放 PR 为 0。本轮五个远端分支和六个本地分支（含集成分支）已删除。
六份工作树已解除分支绑定，保留验收日志与此前因自动审批拒绝而留存的缓存/辅助文件。
本次没有重试被拒绝的文件清理。各包合并提交和时间见机器门禁；没有新增运行授权。

## 下一阶段执行顺序

1. 以已合并的产品候选及对应 main CI 作为下一阶段工程起点；实际部署与使用仍绑定新的准确范围。
2. 固定该候选的实际镜像、配置和 STOP/三类日志，补全 QQ 登录卷与原服务的准确恢复范围。
3. 分开批准一次原图的两次模型尝试，以及停止聊天后的单事实 R5 生命周期；各自完成手机侧验收。
4. 先采集真人反馈与一天私密日记，再逐步积累 3/7/14/30 个真实自然日；Reader 与第一次主动私聊分别批准。
5. 使用现有反馈清单汇总真实覆盖、漏忆、费用和延迟；有多轮稳定证据后再审查 E2 常驻与累计限额实现。

可复用提示：

> 接续 P12 A–E。先读取本交付、gates 和五份 PR 的最终回执；核对 main/CI、当前实际镜像与最新 STOP、Ledger、Budget、Window。不要重用历史窗口。只推进本次明确批准的一个真实范围，固定事实/图像/来源、费用/次数/时限、隔离恢复和停止人；真实结果按 coverage、usage、phone 和自然日分别记录。未批准能力保持关闭，UNKNOWN 与不确定投递不重试、不退款、不复活。F 许可证不在本次 A–E 范围。
