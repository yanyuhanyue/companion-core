# P10 Provider 证据增量与合同边界

核查日 **2026-10-21**；源码基线 `12a3f5b5e282900c0b6d8e233b5b86f8b635e255`。
本次只读公开官方资料及源码，承接 [P8-C.1 核查](p8c1-provider-contract-review.md)，不重新签发批准。
**结论：`PROVIDER_BLOCKED_WITH_ACTIONABLE_EVIDENCE_GAPS`，真实付费仍 BLOCKED。**
没有访问账户、余额、模型、计数或报价 API，没有联系供应商、读取 Key、连接主机或发送私人内容。
实际 usage、缓存、费用、延迟及账户证明均为 null；模型调用数为 0。

## 1. 当前合同哪些必须保留

| 当前实现或约束 | 审计判断与后续边界 |
|---|---|
| `PaidModelApproval.framing=chatml-v1`；本地 tokenizer 文件及固定追加 suffix | 这是当前支持的一种实现，不能成为所有提供方的统一准入前提。安全所需的是**全部计费负债的可验证上界**。精确序列化可提供这种证明，可信保守上界也可提供；近似计数和任意裕量不能 |
| 对所有场景采用同一渲染方法 | 普通 chat 与 JSON 模式转换不同，须由实际允许的完整 payload 和场景证明覆盖。只替换字符串或文件 hash 不能解决隐藏转换 |
| OpenAI 格式 `/chat/completions`、`max_tokens`、usage 字段 | 属于提供方协议适配。HTTP v1 仍是 AstrBot/Core 边界；ModelClient 继续只组装/发送/解析模型协议，不能接管存储、权限、预算或投递 |
| 供应商/endpoint/模型/账户身份/区域/币种/有效期绑定 | 必须保留实际授权绑定和变更失效。无需把“必须拿到远端镜像 digest”当作唯一证明形式；有适用且可验证的稳定上界合同也可评估。公开权重 revision、别名或响应 fingerprint 本身均不证明收费合同 |
| `input_contract_sha256` 等证据摘要 | 固定实际材料，不能创造材料。没有真实证据就保持 null；格式正确的 hash、操作者自签或虚构供应商签名不能证明远端行为 |
| 当前 `currency=USD` 与整数 micro-USD | 单币种整数会计必须明确。USD 是当前支持范围，不是所有实现必须永久采用的币种；新增币种需独立会计合同与实现，禁止隐式汇兑或混币累加 |
| 单次/逻辑请求/日/月/窗口金额、次数、并发上限 | 必须保留，按实际收费尝试计算，不按最终回复数；不因保守预留过大而扩大原批准额度 |
| 账户侧硬封顶、全入口约束、唯一授权账本与独立 custody | 仍是本任务书的必要准入，不能因改用保守上界而删去。具体账户是否满足须有获准私密证据，公开页面不能代填 |
| I/O 前持久预留、单次 admission、最终 payload/凭据/时窗复核、UNKNOWN 与对账 | 必须保留；超时/崩溃/丢响应保留最大负债并阻断，晚到更高费用保留且停止；旧库/旧账本不能恢复额度 |
| 输入数据量与来源限制 | 独立于费用上界。即使预留整个上下文，也不授权发送更多历史、记忆或私人数据；仍须字节/消息/完整轮次上限、主人私聊归属、SENT 状态、privacy epoch 和删除回放 |

上述源码事实对应 [config.py](https://github.com/yanyuhanyue/companion-core/blob/12a3f5b5e282900c0b6d8e233b5b86f8b635e255/companion/config.py)、
[paid_budget.py](https://github.com/yanyuhanyue/companion-core/blob/12a3f5b5e282900c0b6d8e233b5b86f8b635e255/companion/paid_budget.py)、
[budget.py](https://github.com/yanyuhanyue/companion-core/blob/12a3f5b5e282900c0b6d8e233b5b86f8b635e255/companion/budget.py)、
[model.py](https://github.com/yanyuhanyue/companion-core/blob/12a3f5b5e282900c0b6d8e233b5b86f8b635e255/companion/model.py)。其余是合同审计判断；本批未改 runtime。

## 2. A / B / C 官方证据矩阵

此处 **A＝精确合同，B＝可信保守上界，C＝可评估替代方向**。
旧报告的“方案 A＝封顶票据”只是条件提案，归入这里 C 的未来能力，不能误读为已存在服务。
下表官方事实均于 2026-10-21 重新检索；网页与仓库 main 可变，核查日不是供应商有效期承诺。

| 类别与证据 | 官方实际内容 | 本项目缺口与判断 |
|---|---|---|
| A · DeepSeek 服务身份 | [价格页](https://api-docs.deepseek.com/quick_start/pricing/) 将 `deepseek-flash` 指向 `DeepSeek-V4.1-Flash-1017`，旧 Flash 别名路由到当前模型 | 别名未锁定未来部署；没有账户适用的完整收费转换/版本有效期证据 |
| A · 精确输入 | [Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/) 的字符比例是近似值，提供 demo tokenizer，实际数量以响应 usage 为准 | 没有将 demo 文件、当前托管收费部署及全部预处理绑定；不构成精确计数或保证上界 |
| A · renderer/JSON | [官方模型 encoding README](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/README.md) 使用 DeepSeek BOS/User/Assistant 和非思考结束标记；[encoding.py](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/encoding.py) 在 system 分支渲染 response-format | 与本地 ChatML/消息末 suffix 存在差异；参考公开且有价值，但不证明托管 API 所有隐藏输入与本地等价。A 未满足 |
| B · 输入/输出硬边界 | [价格页](https://api-docs.deepseek.com/quick_start/pricing/) 标示上下文 1M；[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/) 定义生成 token 上限及输入/输出共同受上下文限制 | 未取得明确整数、覆盖全部可计费隐藏输入/输出及失败/取消/拒绝请求的上界承诺；不能把 1M 标签或本地限制直接当作财务 T |
| B · 费率 | [价格页](https://api-docs.deepseek.com/quick_start/pricing/) 当前 Flash 高峰 cache miss/input 为 USD 0.30/百万、output 为 1.20/百万，低峰减半，并保留调价权 | 可观察价格，不是全批准及在途计费区间的最高价保证。保守预留不使用缓存折扣，仍缺费率有效期和完整收费项 |
| A/B · 账户上限/隔离 | [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/) 按账户合计并发；user_id 涉及缓存/安全/调度。[余额 API 文档](https://api-docs.deepseek.com/api/get-user-balance/) 描述余额及 CNY/USD | 新 Key/user_id 不能证明独立预算。余额、并发限制、低充值额均不足以证明零超额硬封顶；具体账户币种/全部入口/适用区域未核实。A/B 共同阻断 |
| C · Anthropic | [计数文档](https://platform.claude.com/docs/en/build-with-claude/token-counting) 将计数称为 estimate，免费但不应用缓存逻辑；[限制文档](https://platform.claude.com/docs/en/api/rate-limits) 描述组织/Workspace 支出限制及到限拒绝 | 是可进一步评估的供应商能力，不能从 estimate 推导最大费用；本账户配置、在途并发/晚到账单零超额、单次全部收费和费率有效期仍未知，不判定通过 |
| C · 自托管可控 renderer | [Transformers 模板文档](https://huggingface.co/docs/transformers/main/en/chat_templating) 可直接生成模板 token；[vLLM Online Serving](https://docs.vllm.ai/en/latest/serving/online_serving/) 提供不执行推理的 renderer 与 token-ID 输入接口 | 推断：可评估固定工件、完整渲染后按同一 token IDs 执行，避开远端隐藏 framing 证明缺口。尚未选模型/设备、下载、安装或验证；资源/电力/托管费用与数据权限均未知，不能称免费或已可用 |

公开页面未证明某能力，不等于供应商绝对没有未公开能力。本结论只覆盖本轮材料及当前项目门禁。
工具、搜索、媒体和思考模式不在当前获准文本范围，不能因将来添加这些收费入口仍复用未扩展的证明。

## 3. 最小后续接口提案与拒绝条件

设计方向：在 **Core 的预算准入层**提供一个经验证的“费用上界证明”，沿用现有预留/结算/日志及一次性 admission，
不创建第二账本或让 ModelClient 持有审批。证明只需绑定：完整 payload 与场景、提供方/账户/模型/endpoint、币种、
有效区间、证明方法、真实证据引用、全部输入/输出/其他收费的最大负债。数据来源/字节/轮次上限另外复核。
实现可在正式部署前调整接口，无需为旧 ChatML 做兼容层；本批仅提案，当前代码仍只支持 `chatml-v1`。

- **A 精确序列化**：证明完整远端计费转换与固定 tokenizer 等价，覆盖特殊 token 字面量、Unicode、BOS/EOS、角色/空白、JSON、生成前缀、输出及失败收费。有限样本相等只能回归，不能替代完整合同。
- **B 保守上界**：取得覆盖每个收费尝试的整数 T/O、最大费率及其他费用上界，不要求本地恰好等于实际 token。若费率为整数 micro-USD/百万，预留 `ceil((T*Ri + O*Ro)/1_000_000) + F`；F 为已证明、向上取整的其他最大费用。所有项覆盖成功、超时、取消、拒绝及晚到账单，Ri 不使用缓存优惠。
- 证明缺失/失效、payload/身份/版本漂移、任一原额度不足、未知收费项、账户外消费者不可控、账本缺失或对账超额时，拒绝新增 I/O。停止不撤回在途请求，也不释放 UNKNOWN。

不要求供应商采用某种签名算法或预报价产品；若未来真有不可超价执行票据，可作为 B 的最大金额证明接受独立评估。
必须先证明该能力实际存在且适用，报价自身涉及的数据/费用也须获准。不能自造票据服务、hash 或供应商签名填平缺口。

## 4. 可由主人选择的后续范围

| 选择 | 可以继续准备的离线成果 | 进入真实步骤之前仍需 |
|---|---|---|
| 保留 DeepSeek，补 A 或 B | 整理本页逐项证据问题与受审准入接口；不联系供应商、不调用计数/报价 | 真实完整计费合同、账户硬封顶/全入口、币种/区域/期限及主人 R4 数据/金额批准。材料不足继续 BLOCKED |
| 评估另一提供方 | 先比较其官方上界、失败收费和硬封顶合同；Anthropic 仅是候选例 | 具体候选的完整 A/B 证明、适配审查、账户与数据批准；不自动换 Key、切流或提高预算 |
| 评估本地推理 | 先设计固定 tokenizer/template/token IDs 与 CPU/RAM/GPU/输出/时长/并发限制 | 明确设备/模型许可、下载/安装/资源费用和数据范围；实际资源与效果验证。自托管不自动授予 QQ/M1/Reader |

当前 `selected_route=null`。现有离线捕获、SENT 有界多轮、恢复申请及缺陷修复可继续，不能把选项或合成成功当成 R4 通过。
R1/R2 与 P9/P10 的签收字段见 [对象与签收模板](p10-r1-r2-signoff.md)，本批快照见 [证据记录](p10-contracts-evidence.json)。

## 5. 本批验证与独立审查

非作者按固定基线核对本批三份公共材料及相关源码/官方断言，未发现 P0/P1/P2；结论只限合同文本。
PowerShell `scripts/check.ps1` 的 Ruff 与三份定向测试通过：**16 passed，0.47s**；检查快照不冒充授权/测量、原审批合同和本批公共材料隐私。
两份新报告的相对链接检查无断链。本批不修改 runtime，不重跑全量/容器/原生测试，也不把旧 main CI 写成本批最终提交的 CI。
精确交付 SHA 与后续集成验证由持续交付记录关联；测试成功不提升任何真实准入。
