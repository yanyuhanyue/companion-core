# P13 A–F 开发与离线交付

本轮完成 P13-00 及 A–F 的授权开发、离线故障回归、研究与独立验收材料。
**P13 真实验收尚未完成**：任务书第 13 节要求未经另行授权只做公开代码、离线设计与
合成回归，并明确禁止连接 VPS、读取私密数据、实际 QQ/CDN/付费调用、启用日记/Reader/
Autonomy 或合并 PR。本轮保持这些边界，五个 PR 均为 Draft。

`private_beta_ready=false`，`production_ready=false`。P13-L 许可决定不在 A–F 范围。

## 基线、候选与构建身份

开始及交付复核的 main 为 `eb5143500bed5395bb45f2a2cc20335da9988491`，
[完整 CI 39719175508](https://github.com/yanyuhanyue/companion-core/actions/runs/39719175508)
10/10 成功。主线本轮没有合并或部署。开始状态、模块复用和旧 P11 实测保持在
[基线](p13-baseline.md)，不会用新离线结果覆盖历史。

组合代码候选为 `a1d32c3ae2005f1098d6597b50eb2ece74d0a1b4`，树为
`f1b6bf7c0d24b82805abaa7a565c9949c1a9f4fb`，迁移头为
`0018_reflection_goal_lineage`。它包含下列五包实现，最终门禁与交付文档追加在该候选之后。
最终回执另列文档追加后的准确候选/树、各 PR 最终 HEAD、完整 CI 与构建产物 SHA256，
并核对文档追加没有改变产品代码。

该组合候选的 Wheel 与 sdist 已通过安装迁移、无 Git 元数据重建和私密输入排除验证：

| 产物 | SHA256 |
|---|---|
| `companion_core-0.5.0-py3-none-any.whl` | `99a54c1e1cc28164ec0d47b9a029d37e26decfed6e5bdccbf505f0e8c9df695f` |
| `companion_core-0.5.0.tar.gz` | `3bc961b15e74d110b7baa18513929162cdf6028fe693e4f7ac36dc46a1104f8e` |

CI 容器构建仅证明该 CI 源码对应的构建与隔离烟测。最终回执从组合 CI 日志分别记录
Core、Executor、Reader、Bridge 的构建 image ID；注册表不可变摘要、实际部署 image ID、
运行配置摘要和真实主机状态尚未取得，仍为 null / `NOT_RECHECKED`。

## 五个独立工作包

本表 SHA 指已审阅实现；#63 后续还包含最终门禁与本交付文档。每个最终 PR 的完整 CI
以最终回执核对的准确 HEAD 为准，文档流程成功不能代替产品完整 CI。

| 范围 | Draft PR / 已审阅实现 | 交付与证据 |
|---|---|---|
| A / F1 | [#62](https://github.com/yanyuhanyue/companion-core/pull/62) · `5c01eb8` | 单张原图独立申请、两个视觉细节与双阶段 usage 回执；GET 中途超时/超限拒绝；固定 SnowLuma 引用/转发深读审查 |
| E1/E3/E4 / F3 / P13-00 | [#63](https://github.com/yanyuhanyue/companion-core/pull/63) · `d0e6ca9` | 21 子项门禁；只读 Context Inspector 选择差异；原事件窗口批准关联；真人评分与逐对象恢复材料 |
| C / D / F4 | [#64](https://github.com/yanyuhanyue/companion-core/pull/64) · `24022ed` | 真实/模拟来源隔离；反思到目标的准确关联与否决；自然日/日记审阅申请；PAD 只读研究 |
| B / F2 | [#65](https://github.com/yanyuhanyue/companion-core/pull/65) · `748896c` | 记住/确认/纠正/忘记分操作申请；独立对照与旧备份恢复；冻结留出集的检索 A/B |
| E2 | [#66](https://github.com/yanyuhanyue/companion-core/pull/66) · `4768c3c` | 默认关闭的跨窗口/日/月/账户累计限额；原账本、签名旧责任库存、暂停/续签、最终 I/O 检查 |

所有包复用现有 Core、Memory、FTS/BGE、Budget Journal、Window Journal、Spool 和 Outbox。
Core HTTP v1 仍是 Bridge 与 Core 的边界，模型适配器不承担存储、权限或发送。
没有新增生产检索图、事实主库、钱包、消息队列或多 Agent 运行内核。

## 缺陷、修复与验证

- 日记/反思来源包会把保留的模拟 Observation 当成 live 来源；现于 SQL 数量上限前
  排除模拟项，防止较早的模拟记录挤掉真实来源。demo 来源和输出保留模拟身份。
- 拒绝反思后派生目标仍可继续；新增 nullable 精确关联，仅撤销相关未来意图，并在
  Reader 最后派发时再验租约。已完成结果保留；已开始/不确定读取保留 UNKNOWN 与责任。
- Inspector 缺少候选/选中/最终上下文差异；现投影原冻结记录、摘要、epoch 和已结算
  review 绑定，未知原因如实标注。旧 epoch 不显示旧引用，不重查私有事实来补造历史。
- 反馈可能借用当前窗口批准解释旧事件；现沿原 INPUT 与历史 window 绑定批准，隐藏
  上下文也不免除实际能力的 R4/R5/media 引用。结构关联不等于授权原件认证。
- 累计限额审查修复了事务回滚丢暂停见证、最终双时钟采样越过窗口、第三窗口续签丢
  旧责任归属、只读状态初始化缺失账本四类问题。反例复验覆盖旧前缀恢复、嵌套业务回滚、
  真实 loopback HTTP/认证 WS、三窗口交接与零额外 I/O；均使用合成身份和数据。
- 首轮完整检查还检出最终 hook 撤销旧付款批准后的空投影异常；修复为先复用已验证
  账本快照，并验证已登记账户撤付款/累计范围仍受控拒绝、零 I/O、保留 UNKNOWN。

完整本地检查与最终组合 CI 的准确结果、日志摘要由最终回执记录。定向结果不相加：
A 为 123 passed，观察为 99 passed，
B 为 131 passed；C/D 根审修复后的三文件为 89 passed。E2 最终模型请求边界、累计模型/窗口
和原 paid gate 四文件为 152 passed；全组合检查覆盖其最终版本。
独立审查没有剩余已确认的阻断缺陷，但真实账户、平台和自然日结论不在离线测试能力内。

F2 冻结的 15 条留出查询含 13 条必要记忆：原 FTS+BGE 找回 13/13、误报 15；
标签/最多一跳仍为 13/13、误报 21，候选内容 UTF-8 字节增加 275（20.1%）；未测完整
模型 prompt 或实际 Token。因此维持原生产
检索，不准入图扩展。删除、旧加密备份加最新 Ledger 恢复和 FTS/BGE 重建后，已删内容
及向量命中均为零，独立对照仍可找回。此结果仅属于公开合成留出集，不推导真人质量。

## 实际证据与尚未完成的门禁

| 项目 | 本轮实际结果 |
|---|---|
| 新真实 QQ 输入 / 付费模型调用 / 手机确认 | 0 / 0 / 0 |
| 实际 CDN 原图 / Vision / 业务记忆生命周期 / 私人日记 | `NOT_RUN` |
| 真人自然日 / 手机配对 p50、p95 / 独立账单 | null / null / null |
| 当前主机、STOP、运行 image ID、配置摘要 | `NOT_RECHECKED` / null / null / null |
| 新服务器、部署、恢复、QQ、付款、R5、Reader、日记、表达与发送批准 | 全部 null |
| 私人 Beta / 正式发布 | false / false |

[机器门禁](p13-gates.json) 分开列实现、离线、协议本地/平台、供应商、手机、自然日、
恢复本地/业务和发布；设计材料的 DONE 表示材料完成，不表示对应能力已启用。
P11 的历史 6/6 手机签收没有重复记入 P13；没有生成新的私人审批目录或真实聊天材料。

## 停止、恢复与后续

普通窗口上限仍为五次；图片固定 Vision+chat 两次，不能混入历史/事实范围。
累计限额默认关闭，稳定账户归属由批准材料证明，本地计数不限制其他程序费用或供应商
实际扣款。新窗口、换 Key、续签不清零 UNKNOWN、发送不确定和原自然日/月责任。
原 STOP 和只读状态保留；续签需新批准与准确前驱/head，没有自动续签路径。

迁移前无归属的旧目标保持 null，启用前须单独审阅；有新反思关联时拒绝无损性无法保证的
降级。已部署累计合同后，不得用旧二进制、旧数据库或日志前缀覆盖新责任。
恢复须最新 Ledger、Budget、完整 Window/Spool 与身份/schema 一起核对，先禁止出站，
再打开隔离副本。所有副本一起回退仍需要外部保管的最新 head 清单检测。

下一步依次是：候选审阅及单独批准合并/当前状态回读、一张真实原图、一个业务事实闭环、
一个真实自然日与日记、累计合同签收后的渐进 Beta；具体退出标准见
[下一阶段五项任务](p13-next-stage.md)。这些材料不是运行批准，未获批准项目保持未执行。
