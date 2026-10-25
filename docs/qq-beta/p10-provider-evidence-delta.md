# P10 模型用量与费用观测

更新日 **2026-10-21**；本轮开发基线 `9eb673747ccdf05372426926370d018e05665e3d`。
当前模型路线为 **DeepSeek**。开发阶段按输入估算、输出上限和配置费率预留本地预算，
实际调用后记录供应商 usage，用于校准估算、发现异常及后续账单核对。
真实模型执行仍须有明确的数据、时间、次数和金额授权；本页不授予执行权限。

## 估算与结算

| 阶段 | 实现约束与含义 |
|---|---|
| 输入准备 | 对完整允许的 payload 做本地 token 估算，应用配置的裕量；估算用于规划和预留。历史、资料和当前消息各自的来源及大小约束仍须满足 |
| 预留 | 按估算输入、输出上限及冻结的配置费率计算整数 micro-USD；沿用原单次、逻辑请求、日、月、窗口、次数和并发限制 |
| 最终发送 | Core 重新检查授权、配置、时窗、租约和已预留预算；模型适配层校验实际发送的目的、凭据与 payload，保持不持有存储或审批职责 |
| 成功响应 | 保存有效 usage；按预留时的费率核算用量成本估值，记录输入估算误差、缓存用量及预留差额。普通输入估算不足可结算并进入诊断 |
| 异常响应 | 用量缺失或无效、输出/金额越限及配置或账本异常时，保留已知成本或 UNKNOWN 负债并阻止不安全的后续调用 |
| 未收到响应 | 沿用有界重试；每次尝试单独预留，未知尝试保留全额，不以最终回复数替代尝试数。窗口的次数和并发限制仍生效 |
| 真实账单 | usage × 配置费率是成本估值。供应商账单金额及对账差异另行记录，有实际材料后再完善收费细节 |

本地预算限制约束本系统的请求；输入估算、配置费率与本地账本不构成供应商账户总账的财务保证。
配置费率的有效期表示操作者要求复核的期限。初始参数用于开发和受控试用，
有实际样本后再依据偏差调整；token 用量异常才进一步排查对应请求和处理阶段。

## 观测与数据范围

只读运行摘要提供已结算调用数、估算/实际输入、输出和缓存 token、预留/用量成本及异常计数。
没有有效样本时明确报告零样本和空测量；UNKNOWN 不计作已校准成功调用。
诊断只输出聚合数字，不输出消息正文、身份、凭据或私密路径。

M0 多轮必须单独批准 `approved_window` 历史范围：同一窗口、主人私聊和会话，
只有完整 SENT 普通回复轮次可进入上下文；按轮次和 UTF-8 字节裁剪，并在发送前复核来源与 privacy epoch。
模型使用授权与历史使用授权分别生效。M1、Reader、日记和设备能力仍遵守各自的授权范围。

## 当前证据状态

本轮是离线代码与本地合成验证；真实 usage、缓存命中、供应商账单、手机延迟和持续运行签收仍为空。
既有的一次只读主机元数据盘点记录保存在私密工作材料中，不授予复制、恢复、部署、QQ 或付费权限。
后续真实操作使用已准备的 [对象与签收模板](p10-r1-r2-signoff.md)，
当前公共状态见 [证据记录](p10-contracts-evidence.json)。

此前公开资料核查保留在 [PR #36 的固定历史版本](https://github.com/yanyuhanyue/companion-core/blob/9eb673747ccdf05372426926370d018e05665e3d/docs/qq-beta/p10-provider-evidence-delta.md)。
当前开发与费用观测以本页及代码中的估算、预留、用量诊断流程为准。

## 离线请求计量（2026-10-25）

`token_measurement` 显式配置后，上下文裁剪和预算使用同一完整请求计量接口。
回执保存在 `model_admissions.measurement_json`，绑定实际 payload、路由、方法与官方文件哈希；
独立预算 journal 的版本化摘要验证回执，旧行 `{}` 保持历史估算身份及原合同字节。
输入计数、预留余量、输出上限分列，最终仍以供应商 usage 结算，缺失 usage 保持 UNKNOWN。

首个配置需完整声明 `policy`（`prefer_exact` 或 `exact_required`）、`provider="deepseek"`、
`endpoint="https://api.deepseek.com"`、`model="deepseek-flash"`、
`renderer="deepseek-v4.1-flash-1017"` 和
`revision="1bae0e1088993b9b82de08f59de5be5a34e42170"`。
未配置时保持估算；未知模型不自动映射。`exact_local_render` 仅表示已固定官方文本渲染与
tokenizer 对完整本地请求的精确计数，不证明托管模型的预处理或最终账单相同。
普通文本和 SDK 的函数工具声明、调用及文本结果使用固定官方编码器计数。工具请求必须
采用已验证的首条 system 消息映射和 `tool_choice=auto`；实际网络 payload 不改写。
结构化输出、图片/视频及其他未验证格式保持 `estimate`；`exact_required` 在预留前拒绝。
`provider_count` 是独立证据类型，本片不启用任何远程计数请求，也不在运行时下载文件。

复用依据：[官方 V4.1 编码](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/encoding/README.md)
及同 revision 的 MIT tokenizer；文件、大小、SHA256 见包内
`companion/token_artifacts/deepseek_v41/manifest.json`。
工具映射复用同一 `encoding.py` 的 `load_cases` 和 `encode_messages`，将工具 schema
加入首条 system 的渲染副本，保留原请求哈希。工具连续回合的测试直接对照官方 case
loader，并在真实 ModelClient 的模拟 transport 边界核对声明被篡改时不会发送。
[官方用量说明](https://api-docs.deepseek.com/quick_start/token_usage/)以返回 usage 为准。
计量与实际用量分离、预留输出空间复用
[DSH token-meter](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/llm/token-meter/src/types.ts)
及其 compaction-basic 思路；DSH 的 chars/4 仍为估算，本片没有将它标作精确计数。

## 可选请求联合窗口

`paid_model.max_context_tokens` 可选，默认省略；它约束同一聊天路由的本地输入计数加预留裕量，
再加本次 scenario 的真实输出预留。输入、输出和费用原限制继续生效。视频的独立路由使用
`paid_model.video_input.max_context_tokens`，不继承另一模型的聊天窗口。
超过联合窗口返回 `MODEL_CONTEXT_LIMIT`，不降低输出或截断当前输入。

原 reserve 与发送前的两处 measurement 复核共用此检查，最终 HTTP hooks 之后仍会执行。
未设置时不增加 null 序列化字段；原 approval、Settings、snapshot、policy 与 journal 合同保持不变。
设置非空值会改变付费合同摘要，已有 owner grant 不会自动获得该新合同。

该限制约束本地计量。未知 tokenizer、图片和视频仍保持原 estimate 身份与已授权路径，
不能把估算加裕量称为供应商 tokenizer 的严格上界。它不改变全局计量策略，不产生远程计数请求，
也不配置任何生产限额。验证使用合成配置、签名 HTTP 入站及独立 worker，外部模型 transport 为 Mock。

## 原生请求前的可选上下文选择

Core 组装有界候选，在原图附加后记录连续区域；SDK 每次 `before_model_request` 使用实际
`instruction_parts` 和完整函数声明重新计量。软目标、原输入限制及联合窗口共用压力判定，
先删除可选引用，再删除较早的完整历史回合。规则、当前输入、已绑定媒体、复核事实、承诺和
接续依据保留；本 job 已产生的模型响应、工具调用与结果不参与裁剪。必需内容超过软目标
保持完整，能否发送由原硬准入决定；不增加摘要模型调用或自动降低输出预留。

布局位于原 `agent_conversations.snapshots_json` 的 `context_plan.metadata.core_context` v1，
只存本 job 区域索引、SHA-256 和裁剪原因。最多 96 个区域、64 KiB，无法精确映射或初始越界
时保留完整输入。缺项和未知版本不允许新裁剪；已识别 v1 结构、来源摘要或请求绑定损坏，
在付费 I/O 前拒绝。来源快照及 `job_sources` 保守保留，裁剪不会解除来源责任。

请求与持久消息同步更新，沿原 MODEL_PENDING 检查点写入 pending 选择；最终 ControlledModel
wire 必须匹配，再由原 admitted 事务关联原 request_id、payload 和 measurement 摘要。
拒绝、READY/WAITING 恢复、已结算 usage、UNKNOWN 与 STARTED 的停止规则沿原 ConversationStore。
工具新增来源按快照前缀绑定。原擦除 handler 同时删除该 JSON；独立擦除账本重放会清理旧备份，
费用责任仍保留。非 SDK 上下文使用同一压力判定，继续执行原完整计量和准入。

实现复用已锁定的 Pydantic AI 2.60.0 公开 Hooks、消息类型和 `dataclasses.replace`。
连续 ModelRequest 的普通 metadata 会在 SDK 合并时丢失，因此使用原 Core snapshot；
不写 SDK 保留命名空间。SDK 的 ProcessHistory 同时更新请求和持久消息，规划亦遵循此语义。
参照 [SDK 消息历史](https://pydantic.dev/docs/ai/message-history/)及
[持久化](https://pydantic.dev/docs/ai/core-concepts/persistence/)，具体行为由安装版本的
公开 API 探针和实际 ControlledModel 最终 wire 对照验证。
