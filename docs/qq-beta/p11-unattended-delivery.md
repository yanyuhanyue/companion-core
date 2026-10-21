# P11 无人值守前置交付

`production_ready=false`。本轮完成离线工程和相应审查，不把合成样本算作真人或跨日体验。
P10 服务器运行的准确范围、镜像与停止证据见 [P10 运行记录](p10-runtime-observations.md)。
该服务器候选为 `da036dc7e1fcdda6a36cce4ca00457ebc7c0e4f1`，与本轮新增 P11 源码分开。

## 代码与证据

| 工作包 | 已有能力 | 本轮增量 | 当前剩余 |
|---|---|---|---|
| A0/E1 | 有界窗口、status/observe、原生账本与停止 handoff | 只读 Context Inspector、私人反馈模板及校验器、逐模块申请模板 | 新窗口逐项授权、手机签收和独立账单 |
| A1 | 原生 OneBot、Spool、HTTP v1 与 Outbox | 引用/转发 ID、卡片安全投影、段位置；纯引用资料输入；全程摘要绑定 | 每种 QQ 类型实测；引用正文按实际覆盖为 MISSING/UNFETCHED |
| A2 | 原生 Content、Observation、模型 admission 与 BudgetJournal | 单张当前主人原图的领取、下载、字节签名、Vision→chat 同 Job 结算；来源与结果到发送前复验 | 真实 QQ 图片与供应商图像能力/计费仍待验收 |
| B1 | 单独历史或限定事实召回 | 显式双范围许可的同轮组合；来源删改和 Ledger 未完成写入均拒绝 | 自然对话效果和真人评价 |
| B2 | 原生创建、确认、纠正、遗忘及恢复；已有独立测试库演练 | 已准备停止窗口后的独立 R5 执行与审查材料 | 真实 owner 事实生命周期和新范围批准；自动提取仍分阶段 |
| B3 | episodes、summaries、FTS/BGE | 主题相关的 1–7 日只读事件线与未完目标，带来源、覆盖和 epoch | 跨日实际质量；月度与图增强单独评估 |
| C1 | 原生 Affect/traits/Reflection | 显式反馈的 SHADOW 预览、TTL 与撤销；owner.preference/self.interest 分开 | 真实反馈、多自然日独立阅读；PAD 仍为研究提案 |
| D1/D2 | 原生 Diary/Consolidation/Goals/Social | 只有取得的真实类型来源才满足日记门槛；可解释兴趣和私人分享预览 | 真实聊天日→日记→修改/删除→翌日；Reader/主动分享另批 |
| E2 | 固定 BGE、FTS、未来 RFC | 本轮补近期连续性与最小 Inspector | 图检索、月度、折叠需实际压力/对照证据，未加入新运行时 |
| E3 | 公共源码与 Reader notices | 63 包版本、94 个许可文件盘点；固定第三方来源核查；非商业许可候选 | 主人选定自有许可/署名；最终各平台发行产物审计；Console 原资产 |

## 验收等级

本轮工程测试中的身份、消息、事实、图片和日期均为合成材料。localhost WebSocket/ASGI、
本地 BGE、SQLite、原生删除账本和加密恢复执行真实代码；模型与 QQ 发送使用 MockTransport。
因此可以验证来源、额度、幂等、失败和恢复边界，不能填写真人评分、真实付费 Token、账单或手机 E2E。
有效配对样本尚缺，P50/P95 保持未计算。

| 验证范围 | 实际证据 | 不由此证明 |
|---|---|---|
| A0/E1 | 28 项定向检查；只读 Inspector 与反馈格式 | 手机签收、来源认证、账单 |
| B1 | 180 项定向检查；原生 BGE/账本/恢复与模拟模型发送 | 自然对话长期质量 |
| B3/C1/D1/D2 | 修复管理指令和缓存响应头后 103 项相关检查 | 真实跨日兴趣、日记或主动分享 |
| A1+B1 | 56 项最终组合检查；与成长/E1 合并后 70 项检查 | 每种真实 QQ 消息类型签收 |
| A2 | 319 项相关检查；4 项加强的零付费 HTTP 反例复验；预算子模块 107 项检查 | 真实图片能力、供应商费用或账单 |
| E3 | 63 个依赖版本、61 个当前安装元数据、94 个许可文件摘要核验 | 全平台软件包发行许可已清结 |

这些组有重叠，不相加。本轮新增真实 QQ 发送、CDN 下载及付费模型调用均为 0；
真实模型 Token 为 NOT_RUN，供应商账单核验为 UNKNOWN。P10 的既有用量另见原记录。

第一版图片范围固定为 `owner_image_v1`，只支持一张当前主人原图，历史和长期事实范围均为 none。
图像模型须使用同一份已批准聊天 provider/model/endpoint/key，图像输入另有许可与估值参数；
不同媒体 provider、引用内图片、多图和混合历史/记忆尚未接入。Mock 响应不能证明实际供应商支持图像。

既有五条 QQ 实测、应用集恢复和独立测试库生命周期分别保留原结论。
R1 不覆盖 QQ 登录卷/原服务/整机；R2 的新候选服务器验证仅覆盖 P10 所列隔离测试；
R3/R4 旧窗口已停止；R5 QQ 业务生命周期仍待批准执行；R6 Reader 与主动发送未新增运行。
本轮允许的代码合并以各 PR 实际合并 SHA 与 CI 为准，不产生新的运行许可。

所有新增派生视图以原 SQLite 记录为来源。Affect 只有明确主人反馈，兴趣区分主体，
时间线只把 SENT 回复算作已完成会话，分享草稿保留 external_untrusted 覆盖。
遗忘后视图重建为当前 epoch；只读 Inspector 输出哈希引用并标为 NOT_REVALIDATED，
它不是平台认证或运行时来源复验。导出的私人报告仍需纳入原有保留/删除范围。

## 接续与回退

先按 [Beta 观测说明](p11-beta-observation.md) 复制私有申请和反馈模板，填写准确候选、
镜像、身份、截止时间、额度和逐项批准证据。每次仍最多五次外部模型尝试，
历史审查或图片识别与回复共用总额；周期总额度尚未实现。
真实运行必须接续最新 Core/Spool、Budget Journal、Window Journal 与 ErasureLedger，
不能用旧备份或新窗口重置责任和遗忘。旧停止回执、失败与 UNKNOWN 一并保留。

回退先停止对应范围，读取 Core/Bridge 双端回执，再关闭对应 scope 或换回经过验证的镜像。
回退代码不回滚真实数据和独立账本，也不重发不确定投递。

下一步依次为：本人签收富消息及历史/事实组合；按单独媒体合同验收一张原始图片；
按 R5 执行真实记忆生命周期；跨自然日积累表达、日记和兴趣反馈；确认发行许可及署名。

## 固定候选与检查

P11 基线：`e742e07d11d2e1e245c9073fc28e09c20f4a7d8f`。最终产品提交：`549bc587e55647c39f796d4f92aaafbaaa789d2d`。
下表为各独立 PR 的最终分支 HEAD 与源码 tree；合并提交由 GitHub squash 产生。

| PR / 范围 | 最终 PR HEAD / tree | PR CI / 合并后 main CI | 合并提交 |
|---|---|---|---|
| [#50](https://github.com/yanyuhanyue/companion-core/pull/50) A0/E1 | `4290cd2039546d06515f4c006e95705282fe3e44`<br>`c59ed73968a84089f03d6b2955e19fff90af99b9` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39632465307) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39633697730)，均 SUCCESS | `fd75228b49d84bf0b64c8bc8726efb603fe76877` |
| [#51](https://github.com/yanyuhanyue/companion-core/pull/51) B1 | `7ff4e78fe71dc18cdfcabadbb70337ec0e34439e`<br>`a9114e24936733c8eeeafa0e9fcdd53f7af57042` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39634605026) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39635562568)，均 SUCCESS | `7d8fc45e5d72e139b0e498df5f562849a585d1b9` |
| [#52](https://github.com/yanyuhanyue/companion-core/pull/52) B3/C1/D1/D2 foundations | `fa5d2cdb12adea6dfcc67e9a8688add17484100c`<br>`6ed7198e1ad7f4d0cc1dfc1a49366bba7148096f` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39635704288) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39636245716)，均 SUCCESS | `ce71ad45c55de344aef11f5e4c1a2ce03ca623da` |
| [#53](https://github.com/yanyuhanyue/companion-core/pull/53) E3 license prerequisites | `aea15628a7952bcc6fcb4a2cde6cd073ef4273eb`<br>`365c59476b7bd8a07b3a46d1c6c075ba4f706526` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39636661954) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39637183747)，均 SUCCESS | `08ddcac3c591c6dbcdbf8a7d20965dcec362cd65` |
| [#54](https://github.com/yanyuhanyue/companion-core/pull/54) A1 with B1 composition | `15f2eed0327578ecb799484db21487a2b2bf3a52`<br>`7592ba66f08468474377b4421bb3371940567eb8` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39637493833) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39638107055)，均 SUCCESS | `e2d863acd73d466c67de9ba6062a8b4df577df29` |
| [#55](https://github.com/yanyuhanyue/companion-core/pull/55) A2 and image Context Inspector | `2bcc8ca047d17ce5c1ab019bcb614df4bdee97a6`<br>`226038934e06f1fd1a97c63e46d1b1f40179f50b` | [PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39642503533) / [main](https://github.com/yanyuhanyue/companion-core/actions/runs/39643703716)，均 SUCCESS | `549bc587e55647c39f796d4f92aaafbaaa789d2d` |

最终整合产品在 `2bcc8ca047d17ce5c1ab019bcb614df4bdee97a6` 上执行 `./scripts/check.ps1`：Ruff 通过，**1993 passed、6 skipped**，pytest 用时 501.92 秒。
本机跳过 4 项需独立浏览器开关的测试及 2 项需符号链接权限的测试；两平台 CI 的符号链接检查通过，独立浏览器组 4 项全部通过。

产品 PR 的完整 CI 在 Windows、Linux 各为 **1995 passed、4 skipped**；专门浏览器组 **4 passed**、原生插件组 **58 passed**。容器构建/冒烟、replay、包构建和校验均通过。四个分片按各系统分开统计，不能跨系统累计。

各定向组与全套测试有重叠，不累计为独立用例数。

关键修复证据包括：Ledger 已落盘但数据库应用失败时拒绝旧来源；纯引用输入不触发全库检索；
管理指令不进入日记和时间线；HTTPcore 实际传输日志不泄露签名图片 URL；
替换图片字节、租约或回复载荷哈希均在相应付费/发送边界被拒绝。
真实子进程在图片准备落盘后、预算预留前退出，重开数据库后保留单次领取，
到期转 UNKNOWN，不重新下载或调用。原红例和私人审查记录保留在本地证据目录。

## 下一轮接续提示

> 先读取本文、P10 运行记录和 P11 各范围合同，核对最新 main/CI。沿用最新 Core、Spool、
> Budget Journal、Window Journal 与 ErasureLedger；旧窗口已停止。优先准备准确候选的
> 富消息及历史/事实测试申请，再按独立图片合同和 R5 依次准备真人验收。逐项记录手机签收、
> 来源覆盖、真实 usage 与独立账单，缺项留空；跨日体验随自然日追加。非商业 LICENSE
> 和署名采用已准备的候选，等待主人明确选择后落地。
