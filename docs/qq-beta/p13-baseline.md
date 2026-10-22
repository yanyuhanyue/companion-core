# P13 起点、复用边界与未取得的真实证据

本轮范围是 P13-00 和 A–F。L 许可证决定不在本轮范围。
任务书 SHA-256：`03c1e49e781722b8a28913b18937426e6a4cfda84e6237c8dde7dd69a4e3feba`。
用户要求完成这些工作包；任务书中的运行步骤作为验收合同，不扩展实际操作权限。

## 开始时核查

2026-10-22 再次读取 GitHub：main 为 `eb5143500bed5395bb45f2a2cc20335da9988491`，
[完整 CI 39719175508](https://github.com/yanyuhanyue/companion-core/actions/runs/39719175508) 成功，
开放 PR 为 0，唯一远端分支为 main。P12 #57–#61 均已合并。
前一完整产品合并 `7568a2377806f98f286efe0aeac4b69a0b7eae73` 的
[CI 39718418701](https://github.com/yanyuhanyue/companion-core/actions/runs/39718418701) 同样通过。
已验证集成本地 Ruff + 2116 passed / 6 skipped 属于 P12，不能计为新的真人样本。

开始核查时迁移头为 `0017_paid_model_gate`，尚无新 P13 wheel 或部署镜像；
旧 CI 的打包和容器测试不提供当前真实运行镜像身份。新候选源码、构建产物及 CI 在各包完成时分别固定。
代码、构建产物、主机当前状态、模型实际账户能力、手机确认和真实自然日分别记录。

## 复用矩阵

| 范围 | 原权威路径 | P13 工作 |
|---|---|---|
| A / F1 | Bridge `images` / `rich_input` → Core `image_input` / `qq_images` / `qq_rich` → Content、Model、Outbox | 一图独立验收准备、视觉细节证明、固定上游深读边界；缺正文保持缺失 |
| B / F2 | `memory_control` HTTP v1 → 原 Memory、FTS5/BGE、ErasureLedger、backup | 分操作 R5、对照事实、旧备份不复活；原检索与派生实验分开 |
| C / F4 | Affect SHADOW、Interest、Reflection、原 traits 和来源 | 真人观察表与可撤销候选，PAD 仅研究；不把模型自述作为人格证据 |
| D | Diary、Consolidation、Goals、Observation、原 Outbox | 实际自然日的来源、审阅和接续准备；Reader 与一次主动发送另批 |
| E2 | 原 Paid Budget Journal、Window Journal、Spool、Model 准入和发送边界 | 默认关闭的累计额度扩展，保留现有五次窗口上限、UNKNOWN 和恢复责任 |
| E1/E3/E4 / F3 | Inspector、status/observe、原反馈汇总和恢复合同 | 缺失健康段、真人质量、成本/手机时延分别记录；按对象准备隔离恢复 |

Core SQLite 仍是唯一事实库；模型适配器不承担存储、授权或投递。
普通聊天仍通过 OneBot Bridge 和 Core HTTP v1，M0/M1 不执行 DSH、设备或公开社交。
预算、输入和发送计数扩展各自原有独立日志，不新增钱包、事实库或发送队列。

## 实际证据与权限

本批没有连接服务器、读取或复制业务库。当前主机状态为 `NOT_RECHECKED`。
P11 09:36:03 的停止回执、privacy epoch 2 和旧镜像/配置摘要均只证明当时状态；
历史摘要见 [P12 门禁](p12-gates.json)，不能据此重新启用旧窗口。
P11 的 6 输入 / 8 模型调用 / 6 手机确认只属于原两个已结束窗口。
引用正文 MISSING、转发正文 UNFETCHED、卡片仅有限元数据；供应商独立账单仍未对账。

新服务器回读、部署、恢复对象、R3 QQ、R4 账户/付款、R5 读写改忘、R6 Reader、
CDN/Vision、日记、表达变更和主动分享批准均为 null。
私人 Beta 必须显式关闭 proactive 及未获准的 Memory/Diary/Affect/Reader/探索/社交/设备能力。
普通窗口仍最多五次模型尝试；一张图片的 Vision 和 chat 合计两次，不自动续签。

所有 P13 新真人输入、模型调用、手机确认、业务记忆生命周期、真实日记及自然日样本均未执行。
`private_beta_ready=false`，`production_ready=false`。
已有合成通过不改变这些状态；新样本和验收必须来自对应的新准确授权。

## 可校验门禁

[P13 门禁](p13-gates.json) 保存 21 个子项，按实现、离线、协议（本地/平台）、供应商、
手机、跨自然日、恢复（本地/业务）和发布八个维度记录。
[schema](../../contracts/p13-gates.schema.json) 与 `tests/test_p13_gates.py` 检查结构、
真实等级所需引用及样本约束；它们不替代授权原件或证明引用材料真实。
P12 记录保持历史状态。开始基线不会被后续候选覆盖；新迁移、构建与五个独立 Draft PR
见 [P13 交付](p13-delivery.md)，后续实际验收见 [下一阶段任务](p13-next-stage.md)。
PR 合并仍待新决定。
