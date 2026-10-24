# 固定上下文评价计划 v2

新 `prepare` 默认写 `schema_version: 2`。36 个合成 case、baseline/participation_v1、
相同非系统上下文和每次 2048 的输出上限保持不变。每个 case 的两臂来自同一份原生
Core 上下文；加入策略后超过输入预算就拒绝，不单独裁剪某一臂。历史 v1 JSON 不修改、
不回填计量字段，原来的 hash 和盲评验证仍可读取。旧估算的含义不会升级成精确计量。

## 计量来源

每条 request 的 `payload` 是 `companion.model.model_payload(..., "evaluation")`
生成的完整请求。`measurement` 是原生 `measure_payload` 返回的 `Measurement.receipt()`，
包含 payload SHA256、provider、endpoint、model、输入计量、预留余量、输出额度、方法及
适用时的官方工件版本和 SHA256。`budget_input_tokens` 等于原生 reserved input；费用
字段复用 `budget.cost` 和明确配置的费率，不是发票、最高收费保证或执行授权。

`measurement_profile` 记录计量策略、路由、输入安全比例、输入预算、输出额度、费率，
以及 `companion.model`、`companion.token_measurement`、`companion.paid_budget`、
`companion.budget` 和共享 `companion.evaluation_measurement` 源文件
的 LF 归一化 SHA256。它不包含 token、密钥、许可或 OwnerAuthority。校验会按明确提供的
当前 Settings 重新生成 profile、完整计量回执和费用，拒绝更换策略、工件、formatter、
路由或预算输入后仍沿用旧计划。JSON 自身 hash 不能替代这一复算。

离线脚本与 Core 执行入口共用 `evaluation_measurement` 的 profile 和 request 计量函数。
完整组合候选变更后须重新准备新计划；原冻结文件不重写。

- `estimate`：使用现有原生估算器；未配置 renderer 时，默认离线 Settings 使用字节估算。
- `exact_local_render`：仅表示固定官方 renderer/tokenizer 的本地文本渲染精确计数。
  hosted 别名、隐藏预处理和计费相等性未验证；provider usage 仍是实际费用证据。
- `unsupported`：原生 exact-required 策略不支持当前路由或格式。保留原因及空的预算输入/
  费用值，不写成 0；这种计划不可通过原生执行校验。

当前评价计划仅接受文本。工具、media 或额外 payload 字段不能靠重算 plan hash 获准。
`context_soft_tokens` 是本项目的冻结输入预算，paid approval 的 max input 也独立检查；
输出 allowance 另行保留。这些字段不证明 hosted 服务的完整硬上下文窗口大小。

## 离线使用

```powershell
python scripts/evaluate_r1_conversation.py prepare --output .local/r1-v2
python scripts/evaluate_r1_conversation.py blind --plan .local/r1-v2/plan.json --observations .local/observations.json --output .local/r1-v2-ballot
```

需要不同计量策略时，两条命令都通过 `--measurement-settings` 指向同一份明确的 Settings
JSON；该文件用原生 Settings 校验，包含其必填配置。工具只使用其中的计量和费率设置，
不加载环境里的生产配置、不实例化这些设置的 Core、不发送请求、不产生预算预留。
非默认策略的计划在未提供对应设置时会拒绝验证，不从历史回执推导许可或伪造审批。
输出目录必须是当前工作树的 `.local` 子目录，已存在目录不会被覆盖。

两臂必须保留相同模型参数及全部非系统消息。盲评要求每条声明 request 均有观察行，
失败、UNKNOWN、未启动或已结算但丢失结果的项仍在分母中。v2 只有真实 MODEL_RESPONSE、
SETTLED、非空、`complete_response=true`、`finish_reason=stop` 且未脱敏的成对结果可评分。
模拟输出可以用于验证导出，但不能被标为有效真人评分样本。重启后读取原结果，不补造
答案，也不从导出工具重试付费调用。计划和这些检查不证明自然度改善。

## 原生执行接缝

执行仍限现有 evaluation job、admission 和 budget 路径；一个 case、三次重复、六请求
的 pilot 限制保持不变。36-case/72-request 文件仅是离线计划，不能借此扩大执行范围。
集成方须接入 `companion.evaluation` 的 v2 读取补丁；该补丁在原 `_plan` 中复算回执，
并沿用其在 preview、submit、job 检查和 dispatch fence 中的调用。原生预算再次计量
并保存 admission receipt，原 journal、UNKNOWN、来源与 OwnerAuthority 约束保持权威。
最终真实 HTTP/独立 worker 集成验证由共享文件集成方在组合候选上完成。

## 复用依据

- [DSH token-meter estimate.ts，22ddee3](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/llm/token-meter/src/estimate.ts)
  把消息、system framing 和工具 schema 纳入统一计量，并明确标记固定密度估算。
  本项目复用统一计量职责，调用已有 native Measurement，不把该启发式复制成精确算法。
- [官方 DeepSeek V4.1 工件](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/tree/1bae0e1088993b9b82de08f59de5be5a34e42170)
  固定 revision `1bae0e1088993b9b82de08f59de5be5a34e42170`；版本、MIT 许可和各文件摘要
  已记录在仓库 `companion/token_artifacts/deepseek_v41/manifest.json`。复用现有校验加载器，
  不新增 tokenizer 下载、模型服务或远程 count 端点。
- [DeepSeek Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)
  区分离线估算与响应 usage；本地渲染身份不能代表 hosted 预处理身份。
