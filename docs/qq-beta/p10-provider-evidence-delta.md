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
结构化输出、工具及图片/视频未验证完整预处理时保持 `estimate`；`exact_required` 在预留前拒绝。
`provider_count` 是独立证据类型，本片不启用任何远程计数请求，也不在运行时下载文件。

复用依据：[官方 V4.1 编码](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/1bae0e1088993b9b82de08f59de5be5a34e42170/encoding/README.md)
及同 revision 的 MIT tokenizer；文件、大小、SHA256 见包内
`companion/token_artifacts/deepseek_v41/manifest.json`。
[官方用量说明](https://api-docs.deepseek.com/quick_start/token_usage/)以返回 usage 为准。
计量与实际用量分离、预留输出空间复用
[DSH token-meter](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/llm/token-meter/src/types.ts)
及其 compaction-basic 思路；DSH 的 chars/4 仍为估算，本片没有将它标作精确计数。
