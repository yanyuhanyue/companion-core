# P8-C.2 / P9 离线集成记录

日期：2026-10-21。持续执行基线为 `12a3f5b5e282900c0b6d8e233b5b86f8b635e255`；
CI 优化已通过 [PR #35](https://github.com/yanyuhanyue/companion-core/pull/35) 合入
`8bd28718003b7ab087bf5dc01759fe18beeee3f3`。这是一份实现与验证记录，不是实机授权或新任务书。

## 已审实现

| 范围 | 行为与验证边界 |
|---|---|
| CI / 本地测试 | 分支 push 不重复触发整套 CI；PR、main/tags、手动和 merge queue 验证保留。通用测试从真实迁移空模板复制隔离 SQLite，迁移专项仍执行真实迁移 |
| Life / Nightly | Core 为组合根，两个模块只接收实际使用的依赖和有限提取 callback，不再持有开放的 Core 成员接口 |
| 配置 / 指纹 | 直接读取完整 Settings，删除重复默认值；对归一化的完整 payload 生成统一指纹。省略与空资料列表去重，内容变化冲突；不重写既有数据 |
| P9 最近对话 | 同主人私聊、同会话、完整 SENT 普通回复才组成历史轮次；资料与历史整组裁剪，保留当前输入和当前资料引用。live 历史限制仍为零 |
| 认证接收 | 固定候选已接入真实本地 WS 接收、独立接收器签名、逐条批准和原 Spool/Window Journal；异常接收和发送均保留额度，最终校验实际目的及投递租约 |
| Provider / 恢复 | 保留真实付费阻断，给出证据缺口；列明 R1/R2 对象、动作、停止及回退和 P9/P10 实测签收字段 |

Life/Nightly 固定源 `0b725a479d7b6e0d20266ee6ac7530f30e1e5a21`、配置/指纹
`fd5908893b759bb3a78442a336fb029b466fb075`、P9
`93f4a02be7ba180b7347600d99e5e23024f39904` 均已通过非作者独立审查。
Core 组合检查逐一核对 17 个方法：只有构造、ingest、context 分别来自上述受审增量，
其余方法与 main 基线一致。合成探针还确认历史读取后发生遗忘时，原 epoch 检查在预算预留与模型调用前阻断。

认证捕获固定源 `7a8cff3f16af365e6bcbf4e2f02756a5ba6b15af` 的验证和独立审查正在进行，
详见 [接收合同](p10-authenticated-capture.md)。最终组合的测试、候选 SHA 及 CI 回执将在
冻结后记录。没有通过的检查不在这里预填成功。

## 测试效率与已完成的验证

同环境、同 20 项 M0 用例、同 cProfile 测法为 **32.38s → 5.12s（减少 84.2%）**；
主要减少重复 schema 迁移。这一数字只对应该定向范围，不推广为全套、模型或真实聊天性能。
测试优化最终本地全量为 **1221 passed / 6 skipped / 1 既有 warning，236.42s**。
首轮缺少本地 embedding 缓存的失败运行未计入性能比较，修复缓存路径后失败项及完整套件均通过。

- [PR #35 精确源 CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39514513763)：7/7 success，源 `3a12a34822f4fab696dcee9c533848b7611ba0db`。
- [合并 main CI](https://github.com/yanyuhanyue/companion-core/actions/runs/39515593735)：7/7 success，源 `8bd28718003b7ab087bf5dc01759fe18beeee3f3`。
- Life/Nightly 定向：55 passed，28.14s；配置/指纹定向：196 passed / 1 skipped，22.67s；P9 定向：55 passed，16.94s。各次 lint 均通过，现有 warning 保留。
- 上述定向范围有交集，不相加为新总数，也不替代最终组合的完整验证。冻结 P8-B 7/8 实验记录保持原样。

## 实际准入

供应商仍为 `PROVIDER_BLOCKED_WITH_ACTIONABLE_EVIDENCE_GAPS`；R1 恢复、R2 部署、
R3 QQ、R4 付费仍 BLOCKED，R5 M1、R6 Reader 仍 DISABLED，`production_ready=false`。
本批没有连接运行主机、读取或复制私人数据、QQ 发送、真实模型付费或部署。
真实账单、手机签收、非空恢复和连续运行时长均未产生，保持 null。

后续真实动作依据 [Provider 证据](p10-provider-evidence-delta.md)、
[R1/R2 对象和 P9/P10 签收](p10-r1-r2-signoff.md) 与原六项独立批准记录执行。
批准范围和实际证据必须分别成立；离线合格不替代真实观察或主人签收。
