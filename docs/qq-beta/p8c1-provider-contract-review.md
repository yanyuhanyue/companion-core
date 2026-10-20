# P8-C.1 · DeepSeek 提供方合同核查

核查日期：**2026-10-20**。源码基线：`e24d8788d4c597f9e938d96d87ed76805504f044`。
范围：官方公开资料及固定源码的只读核查；公开价格属于**观察值**，不是操作者批准、账户报价或费用封顶证明。

**结论：`PROVIDER_CONTRACT_UNVERIFIABLE`；真实付费 `BLOCKED`；本轮模型 API 调用数为 0。**
官方资料足以确认当前默认模型名称、公开价格及请求路径，但不能为现有
`PaidModelApproval` 的 `chatml-v1`、精确 tokenizer、结构化 suffix 与实际服务器隐藏转换提供完整等价证明。
公开 V4.1 渲染参考反而与本地 ChatML 渲染存在明确差异。账户隔离、不可超支额度、账户币种、地区及价格有效区间也未获账户级证据。
不填写真实批准，不以近似 tokenizer、字符比例、合成费率或缓存命中假设补齐。

这是 C2 的证据结论，不表示 C1 已启用 live 窗口，也不表示真实 QQ、恢复或部署通过；`production_ready=false`。

## 1. 证据类型与边界

- **官方事实**：以下直接链接正文在 2026-10-20 检索。动态网页、GitHub `main` 与模型别名可能变化，取证日期不等于有效期保证。
- **源码事实**：只对上述固定提交的公开源码与示例成立；未读取实际 `config.toml`、环境变量、Key、账单、数据库、QQ 数据卷或私人主机。
- **判定/提案**：本报告从证据推导的兼容性与预算结论，不能当作供应商承诺。
- 仅读取文档、公开代码及公开模型仓库元数据。没有调用模型、`/models`、`/user/balance`、计数或报价服务，没有 smoke、SSH、部署或聊天发送。

## 2. 默认配置与当前官方服务

| 项目 | 已证实内容与限制 | 证据、检索日期 |
| --- | --- | --- |
| 拟核查对象 | `provider=deepseek`、`model_name=deepseek-flash`、`model_url=https://api.deepseek.com` 是源码默认值和两个公开示例；实际私密配置未核实 | [config.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/config.py)、[通用示例](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/config.example.toml)、[Demo 示例](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/deploy/qq-beta-core.example.toml)，2026-10-20（源码事实） |
| 当前官方模型 | 官方列出 `deepseek-flash` 与 `deepseek-v4-pro`；Flash 当前服务版本为 `DeepSeek-V4.1-Flash-1017`，Pro 为 `DeepSeek-V4-Pro-0813`；Flash 旧别名会路由到当前 Flash，不能据别名锁定旧权重 | [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，2026-10-20（官方事实） |
| HTTPS 与最终路径 | 官方 OpenAI 格式 base 为 `https://api.deepseek.com`，Chat Completions 为 `POST /chat/completions`；本地直接拼接得到 `https://api.deepseek.com/chat/completions`，并禁止 HTTP 重定向 | [First API Call](https://api-docs.deepseek.com/)、[API 正文](https://api-docs.deepseek.com/api/create-chat-completion/)、[model.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/model.py)，2026-10-20（官方/源码事实） |
| 实际 payload | 本地发送 model、完整 messages、显式 `max_tokens`、`stream=false`；DeepSeek 显式 `thinking.type=disabled`。四类结构化场景另发 `response_format.type=json_object`；没有配置工具或服务端搜索 | [model.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/model.py)，2026-10-20（源码事实） |
| 输出参数 | 官方 `max_tokens` 限制生成 token；当前模型支持非思考模式。本地每场景显式传上限，不依赖官方思考模式默认值 | [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)，2026-10-20（官方事实）；上述 model.py（源码事实） |

**判定：** 名字与路径在公开文档层面兼容，不证明账户可访问、真实运行版本固定或底层序列化一致。
不得将 Hugging Face 权重 revision 当成官方托管 API 的部署摘要，也不得把 API 返回的模型名或模型自述当成固定部署证明。

## 3. 公开价格观察与有效期缺口

下表单位为 **USD / 1,000,000 tokens**，来自 [官方价格正文](https://api-docs.deepseek.com/quick_start/pricing/)，检索日 **2026-10-20**。

| model | 输入 cache hit：高峰 / 低峰 | 输入 cache miss：高峰 / 低峰 | 输出：高峰 / 低峰 |
| --- | ---: | ---: | ---: |
| `deepseek-flash` | 0.006 / 0.003 | 0.30 / 0.15 | 1.20 / 0.60 |
| `deepseek-v4-pro` | 0.044 / 0.022 | 1.32 / 0.66 | 3.96 / 1.98 |

官方高峰规则为周一至周五 UTC `01:00–04:00`、`06:00–10:00`，中国法定节假日除外；其余为低峰。
网页明确保留调价权，没有给本项目一个未来不变的 `rates_valid_until`。
这些是公开价观察，不能据此虚构账户报价有效期。[同一官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)，2026-10-20。

源码的费率单位是“每百万 token 的整数 micro-USD”，按最终总分子向上取整到 micro-USD。
通用示例的 Flash `300000 / 6000 / 1200000` 对应所观察的高峰未缓存输入/缓存输入/输出价格；这是示例，仍缺批准。
Demo 示例的三个 `0` 是 Demo 占位，不能进入要求正费率的 `PaidModelApproval`。
[budget.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/budget.py)、[config.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/config.py)、上述两个示例，2026-10-20（源码事实）。

**判定：** 即使未来获准采用高峰价保守预留，也仍须证明该价格在整个批准/请求计费区间是上界。
短窗口、刷新网页或提前避开高峰不能证明供应商不会变价；计价时间点及跨时段请求规则未获本项目证据。
不对 cache hit 作预留折扣：官方说明缓存尽力而为、不保证命中；命中/未命中字段在响应 `usage` 中，属于事后观测。
[Context Caching](https://api-docs.deepseek.com/guides/kv_cache/)，2026-10-20（官方事实及上述判定）。

账户 `USD` 不能从英文美元报价推定。官方余额文档允许 `CNY`、`USD`，本轮没有查账户余额；
`PaidModelApproval.currency` 仅允许 `USD`。若真实结算为 CNY、含未计入费用或汇兑，现合同仍阻断，不能临时按汇率换算后宣称 USD 绝对封顶。
[Get User Balance 文档](https://api-docs.deepseek.com/api/get-user-balance/)、上述 config.py，2026-10-20（官方/源码事实及判定）。

## 4. 精确 tokenizer、ChatML 与结构化输出

| 需要的证明 | 当前可见证据 | 判定 |
| --- | --- | --- |
| 固定 model → 精确 tokenizer 与后处理 | 官方 Token Usage 页提供离线 demo tokenizer，但说明字符比例随模型变化、实际 token 以返回 usage 为准；没有在该页把下载文件的哈希、模型服务版本与收费预处理固定为本项目合同 | **未证实**。demo 与字符换算不能充当远端完整输入上界。[Token & Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)，2026-10-20 |
| `chatml-v1` 与模型渲染等价 | 本地逐条拼接 `im_start / role / content / im_end`，结尾生成前缀为 ChatML assistant；官方 V4.1 模型格式参考使用 BOS、DeepSeek User/Assistant/System 标记，非思考模式含 thinking-end 标记 | **不兼容的公开参考**；没有证据证明官方 API 会改成与本地 ChatML 等价。[paid_budget.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/paid_budget.py)、[官方 V4.1 encoding README](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash-1017/blob/main/encoding/README.md)，2026-10-20 |
| JSON 模式的全部隐藏输入 | 本地在四类场景所有消息之后追加 `structured_suffix`，再追加 assistant 前缀。官方 API JSON 指南只规定请求参数与 prompt 要求；官方 recipe 参考还会将 response-format 指令并入**首条 system 内容** | **suffix 不可等价认定**；追加位置会影响分词，JSON 有效性保证不证明隐藏输入数量。[JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[官方 dsv41.rs](https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/main/deepseek-recipe-encoding/src/v4/dsv41.rs)、上述 paid_budget.py，2026-10-20 |
| 用户文字与特殊 token 的区分、角色合并、空白、BOS/EOS、Unicode、生成前缀 | 官方 recipe 是 API 转换/渲染参考，存在消息归一化等规则；仓库仅对本地渲染的字符串调用一次 `tokenizer.encode(add_special_tokens=True)` | **未证实**两端对所有允许输入相同。有限样本一致也不能覆盖特殊 token 字面量及全部边界。[官方 recipe](https://github.com/deepseek-ai/deepseek-recipe)、上述 dsv41.rs / paid_budget.py，2026-10-20 |
| 本地文件 hash → 远端收费合同 | `input_contract_sha256` 是操作者填入的证据引用，代码校验不了供应商部署；tokenizer hash 只固定本地文件 | **未证实**。填入任意正确格式 hash 不构成授权或远端证明。[config.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/config.py)、[P8-B 预算合同](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/docs/qq-beta/p8b-budget.md)，2026-10-20 |

官方并非完全没有公开渲染/分词资源：`deepseek-recipe` 是 DeepSeek 官方仓库，支持 V4/V4.1 API 请求转换、prompt 渲染和附加 tokenizer 后的编码。
其 tokenizer 说明还明确标出本地修订、来源与校验信息；V4.1 捆绑文件的比较来源是较早的 Flash-Vision-Exp revision。
这些是有价值的候选核对材料，仍没有把**本项目将调用的托管部署、完整预处理、收费 token、版本有效期**锁为同一合同。
本轮仅阅读，没有下载模型或 tokenizer、安装 recipe、执行其示例，也没有据此生成审批 hash。
[官方 recipe README](https://github.com/deepseek-ai/deepseek-recipe)、[tokenizer 总说明](https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/main/static/tokenizers/README.md)、[V4.1 来源说明](https://raw.githubusercontent.com/deepseek-ai/deepseek-recipe/main/static/tokenizers/v41/README.md)，2026-10-20。

**当前阻断结论：** 对默认普通 `chat`，ChatML 与公开 V4.1 参考差异已经足以拒绝。
关掉结构化场景并不能修复普通 chat 的渲染证明；开启结构化场景还需要额外完整转换证明。
精确兼容验证或供应商改换不能只改 `framing` 字符串：现有类型只接受 `chatml-v1`，需独立受审的后续实现及合同。
`PROVIDER_CONTRACT_UNVERIFIABLE` 是本报告/门禁结论，当前源码的本地错误如 `MODEL_INPUT_BOUND_UNPROVEN` 不会自动检查官方文档或远端版本。
因此不能声称软件能识别任意伪造批准；实际批准应保持缺省，触发 `PAID_APPROVAL_REQUIRED` 后达到 0 HTTP。

## 5. 地区、账户及外部封顶

| 项目 | 官方/源码证据（2026-10-20 检索） | 当前准入结论 |
| --- | --- | --- |
| 账户可用性与地区 | [开放平台协议](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) 的公开版本生效于 2026-04-29；含地区可用性、账户/API Key、预付与出口限制条件。公开资料不证明本账户满足其适用条件 | 账户、主体、实际访问/处理区域及批准未提供：`BLOCKED` |
| 数据处理区域 | [官方隐私政策](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html) 描述其覆盖的个人数据在中国处理/存储，同时明确第三方开放平台下游终端用户的处理规则不在该政策覆盖范围内；[开放平台协议](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) 另要求开发者处理相应终端用户权利/隐私规则 | 不能将通用政策写成该账户具体 API 的区域固定、无留存或删除完成证明。`region` 待账户适用合同/供应商确认；保留 `BLOCKED` |
| 所有计费入口 | [Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/) 将并发按**账户**合计，不随 API Key 区分；`user_id` 说明的是缓存/调度/内容处理隔离 | 新 Key 或 `user_id` 不证明独立预算账户。必须盘点所有 API Key、其他客户端及具计费权限的入口；本轮没有盘点 |
| 账户/项目不可超支额度 | [开放平台协议](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html) 说明预付/余额不足时可停服；[余额 API 文档](https://api-docs.deepseek.com/api/get-user-balance/) 仅描述可用余额。上述正文没有给本项目证实原子、零超额的账户/项目费用上限 | 余额、通知、并发限制和本地 stop 都不等于供应商硬封顶；不能推定存在项目级 cap、无透支、并发计费原子扣减或关闭自动充值 |
| 单一账本与独立故障域 | `sole_account_database=true` 与 `provider_budget_evidence_sha256` 是必填声明；本地 budget journal 通过 fsync 保存保守费用下界，丢失/不一致要阻断 | [config.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/config.py)、[paid_budget.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/paid_budget.py)：代码不证明实际部署的故障域或账号唯一入口，仍待私密证据 |
| 对账、币种/税费/汇兑 | [余额文档](https://api-docs.deepseek.com/api/get-user-balance/) 支持 CNY/USD；实际费用须由后续授权的供应商账单证据确认。本地有 `record_provider_discrepancy`，不会靠对账重新放行 | [budget.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/budget.py)：费用/币种差异保存较高负债并阻断；本轮没有实际对账 |

**判定：** “同账户还有其他不可控客户端”足以保持 blocked；单一 Core DB 不能限制账户外消费者。
即使进程共享一份 DB，DB 与独立 journal 同时损毁仍需供应商证据；不能删除 UNKNOWN、恢复旧账本或更换窗口重获额度。
上述缺口是针对本项目合同的证据缺失，不宣称供应商绝对没有任何未公开的企业能力。

## 6. 两种不放宽预算的替代设计（已独立审查，仍为 blocked 提案）

共同约束：保留现有 `budget/paid_budget` 的单次、逻辑请求、日、月、窗口、次数、并发、单一账户入口与独立 journal 合同；
保留 I/O 前持久预留、一次性 admission、payload/Key 身份绑定、截止与最终重核、UNKNOWN 负债和停止语义。
不把权限、存储或投递移交 ModelClient/Bridge；不提高操作者原费用/次数额度、不引入自动重试、不将一条回复算成一次收费。
两个方案均需未来独立实现和批准，**不能**填入现有 ChatML approval 冒充通过。

### 方案 A：供应商可执行的预报价与一次性封顶票据

**前提（当前未证实）：** 供应商提供可信且不执行推理、不产生模型费用的预报价接口或预签发合同；
报价票据绑定完整 payload hash、账户/凭据身份、精确模型部署、区域、USD、所有收费项、到期边界和最大总金额，
供应商承诺执行时在其内部原子校验与扣减，不能超过该金额，过期/重放/变更拒绝。
票据金额保证必须涵盖成功、失败、取消、超时及晚到账单的全部收费，不能只对成功响应生效。
如报价本身收费、会处理待批准内容或产生隐藏副作用，必须先独立批准其数据/费用合同，不能借“询价”绕过门禁。

Core 先按票据最大金额及次数写现有 DB/journal 预留，再消费一个精确绑定的执行票据。
票据报价大于任一现有上限就 0 模型执行；执行超时或丢响应保留票据最大负债并停止。
票据仅在供应商端具有真实执行约束时能替代本地精确分词依赖；本地代理自己估价或签名没有这个效力。

**失败方式/必测：** 签名/身份/币种不符、预报价与执行 payload 不同、票据重放或过期、超时报价、计费区间变更、
供应商撤销价格/不可验证版本、请求开始后客户端崩溃、账单超过票据、旧 DB/journal 恢复均应拒绝新 I/O。
账户全入口、外部不可超支额度、独立故障域和隐私批准依旧需要证明。

**剩余 blocked：** 本轮所读官方 API 没有证明存在上述具有计费约束的票据接口；需要供应商书面/可验证技术承诺与实现。
不自行请求询价，也不假设成功可行。

### 方案 B：完整硬上下文上界的逐尝试最大预留

**前提（当前未证实）：** 供应商合同提供明确整数 `T`，保证每次**全部可计费输入**（含隐藏 system/framing/JSON/后处理）不超过 T；
明确整数 `O` 覆盖 `max_tokens` 所允许的全部可计费输出，非思考模式没有超出 O 的额外可计费输出；
整个批准/在途请求计费区间的 USD 最高费率 `Ri`、`Ro` 可验证且不会突破，另无未计入服务费。
T/O 必须同样覆盖成功与失败尝试（含超上下文或校验拒绝）的全部可计费 token；供应商须保证超限请求在产生超出该费用上界的收费前拒绝。
公开“1M context”标签本身不证明 T 的准确整数及其覆盖收费内容，也不证明未来价上界。
[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)、[max_tokens 定义](https://api-docs.deepseek.com/api/create-chat-completion/)，2026-10-20（提案的公开背景，不是前提已满足）。

每次不论文字长短、tokenizer 或预期缓存，按以下**完整模型上界**预留（费率为整数 micro-USD/百万 token）：

```text
reservation_micro_usd = ceil((T * Ri + O * Ro) / 1_000_000)
```

`Ri` 取所有输入可发生的最高价，不依赖 cache hit。把最大输入与最大输出相加即使重叠也只会多预留；
任何既有额度不足立即拒绝，不为“跑通聊天”提高预算。
只有后续完整可信结算且无 UNKNOWN 才能按受审规则释放差额，账单迟到/字段缺失/较高费用继续保留上界并阻断。
方案不依据字数估算，也不将本地 `max_input_tokens` 或上下文软限制当作供应商全输入的计费承诺。

**失败方式/必测：** T 不含服务端隐藏输入、模型别名扩大上下文、超上下文失败仍收费且不受 T 约束、
`max_tokens` 未覆盖某类输出、价格漂移、其他收费项、币种/税费不明、其他客户端绕过、崩溃/旧账本回放均应停止。
单独账户的供应商不可超支保证、全入口约束与独立 journal 仍是共同前提；仅把余额充得很少不构成证明。

**剩余 blocked：** 本轮没有 T/O 的完整计费覆盖与费率有效期保证，也没有账户硬封顶/唯一入口证明。
即使这些证据后来补齐，当前 `PaidModelApproval` 仍强制 ChatML 精确输入，不能用伪 tokenizer 或
`input_contract_sha256` 填空实现方案；需新的最小受审上界合同与专门回归，继续复用原预算引擎。
实际可能因过度保守而完全无法在既有预算下调用，这个拒绝结果是设计允许的。

## 7. 后续批准材料：沿用字段，缺失继续 null

不新增大 Schema。下列是现有 approval 及 A4 证据的核验清单，公开材料只存匿名引用/哈希；私密账户、凭据与 journal 实际路径不上报。

| 现有字段/核验组 | 需要的精确证据 | 当前 |
| --- | --- | --- |
| `provider / model / endpoint / region` | 账户可用范围、HTTPS base 与最终路径、实际不可变部署或变更立即停止机制、适用数据处理区域 | model/URL 有公开观察；账户/部署/区域 `null` |
| `currency / input_rate / cached_input_rate / output_rate` | 账户 USD 结算、整数费率上界、包含所有费用及舍入、税费/汇兑处理 | 批准值 `null`；表中价格仅公开观察 |
| `starts_at / expires_at / rates_valid_until / timezone` | 有偏移时间、计价时点及在途请求规则、整个区间价格有效保证 | `null` |
| `tokenizer_path / tokenizer_sha256 / input_contract_sha256 / framing / structured_suffix` | 精确 tokenizer/特殊 token/后处理、实际 API 所有角色及隐藏转换与结构化输入等价证明 | 不满足当前 ChatML 合同；`null` |
| `max_input_tokens / max_output_tokens` 与每尝试/请求/日/月/窗口金额及次数、`concurrency` | 可证明边界、全局保守累计，所有场景与失败/重试逐次计入 | 未批准；`null` |
| `sole_account_database / provider_budget_evidence_sha256` | 实际计费账户所有入口、其他客户端隔离、供应商硬封顶及不可超支语义 | 未证实；不能声明 true |
| `journal_path` 及独立故障域 | 私密保管人、独立 journal 身份/链尾、与 DB 的实际独立性和恢复对账 | 未取证；`null` |
| `window_id / approval_sha256 / rate_evidence_sha256` | 有权操作者绑定上述完整材料、准确候选、身份、数据范围、时间与停止人 | 未批准；`null` |

补证不会自动授权一次 smoke。后续真实付费调用、QQ 发送、M1、Reader、恢复与部署仍各需精确授权；
本次只核查文本合同，其他默认能力保持各自既有状态。

## 8. 实际费用与延迟观测

以下均为**本轮未运行**，`null` 不是零费用、零 token 或零延迟；没有把合成样本计入供应商统计。
`relation_review` 单独列为所需分析口径；固定源码没有独立同名调用入口，不能把 `memory_review` 数据伪造拆分。

| 场景 | 实际 API 样本数 | input / output / cache-hit tokens | 供应商实际 cost / currency | 模型 latency p50 / p95 |
| --- | ---: | --- | --- | --- |
| chat | 0 | null / null / null | null / null | null / null |
| memory_review | 0 | null / null / null | null / null | null / null |
| relation_review | 0 | null / null / null | null / null | null / null |
| memory_extract | 0 | null / null / null | null / null | null / null |
| diary | 0 | null / null / null | null / null | null / null |
| reflection | 0 | null / null / null | null / null | null / null |
| activity | 0 | null / null / null | null / null | null / null |

源码按 `prompt_tokens / completion_tokens / prompt_cache_hit_tokens` 解析 usage；缺失、异常或超过准入边界会保持保守负债/阻断，
费率计算结果也不等于供应商账单。每次 HTTP 尝试分别计费，一条 QQ 回复可能对应多次模型尝试。
[model.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/model.py)、[budget.py](https://github.com/yanyuhanyue/companion-core/blob/e24d8788d4c597f9e938d96d87ed76805504f044/companion/budget.py)，2026-10-20（源码事实）。

## 9. 审查与验证记录

本文件是唯一公开主报告；未修改预算/模型代码、配置、A4/A5 或批准记录。
检索失败或搜索摘要没有用作合同证据；重要结论均基于已打开的官方正文与固定源码。
所有静态检查和独立方案审查应在实际完成后记录，不预写通过。

- 公开材料隐私定向回归：`tests/test_p8c_public_materials.py` **3 passed**（0.15s）；使用 `-B -p no:cacheprovider`，pytest 临时目录指定在工作树 `.local/test-temp`。本轮仅新增文档，未重跑供应商/模型 I/O 或产品行为测试。
- 父代理统一 `scripts/check.ps1` 定向检查：**28 passed**（2.14s）；本次最终文档补充后的统一检查由父代理记录，不将先前结果冒充最终版本检查。
- 非作者 `contracts_design_review` 已复核官方关键正文及固定本地代码，A/B 结论为 **`REVIEWED_DESIGN_ONLY_BLOCKED`**，无未修必改项。该结论仅接受设计约束，不证明供应商能实现票据或完整上界，不授权启用；提供方结论仍为 `PROVIDER_CONTRACT_UNVERIFIABLE`。
- 真实供应商 API、账单、手机 E2E：`NOT_RUN`；模型调用 0，新增 QQ 发送 0。
- 最终统一 `scripts/check.ps1`：Ruff 通过，隐私/原准入/新合同门禁 **29 passed / 0 failed / 0 skipped**（1.94s）；两份新报告的本地链接检查通过。独立审查引用成为必填条件后已复测，未将两案设计审查提升为提供方可用证明。
