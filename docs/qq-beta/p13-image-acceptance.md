# P13-A：一张真实原图的验收准备

基线 `eb5143500bed5395bb45f2a2cc20335da9988491`；准备日期 2026-10-22。
本包复用 [P12 原图链路与负例](p12-qq-media.md)，没有改变运行代码或开窗。
历史 P11 的文字/引用 ID 验收不计入本包；当前服务器状态为 `NOT_RECHECKED`。

| 证据 | 当前状态 | 取得下一等级的条件 |
| --- | --- | --- |
| 来源、一次领取、TLS、上传签名、双模型/原 Outbox | P12 已有离线证据；本包补流中断反例 | 精确最终候选的测试与 CI |
| 当前实例与固定 SnowLuma 公开源码对应关系 | NOT_RECHECKED | 新授权后的实际 image ID、registry digest（如能取得）和版本/构建来源分别记录 |
| 真实账户图片权限、实际模型版本/价格/额度 | NOT_RUN | 独立账户/模型许可；不能用公开文档或 Mock 代替 |
| QQ 原图捕获、CDN 获取、Vision、chat、手机可见 | NOT_RUN | 下表逐阶段证据齐全 |
| 实际 usage、缓存、配置价估值、独立账单 | null | 分别记录，不以估值冒充账单 |
| P13 真人图片成功、自然日样本 | NOT_RUN / null | 尚无新真人窗口 |

## 最小申请

[P13 单图申请模板](p13-image-request.template.json)延用
[P12 图片申请](p12-single-image-request.template.json)的范围，但补齐逐阶段签收、独立
视觉细节、STOP 双端与最新三类记录。它是 `DRAFT_NOT_EXECUTABLE` 审阅材料，不是
`LiveTextWindowApproval` 或付费许可文件；不能直接交给运行命令加载。

拟申请仅 **1 个输入、1 张图、2 次模型尝试、1 次主人私聊回复**，单并发、聊天最长
10 分钟、批准总有效期 30 分钟、本地金额上限 USD 0.01。两次是同一个 Job 的
`content_vision → chat`，计入窗口现有 ≤5 总尝试上限；不增加模型复核/总结调用。
只带一张顶层普通静态 PNG/JPEG/WebP，文字不能透露待核验的视觉答案。
本次输入逐条批准时必须确认确有原图 manifest；纯文字不能替代本次图片样本。

`references_v1`、`owner_image_v1`、同一获批 provider/model/endpoint/credential，
`content.use_chat_model_for_vision=true`；`history_scope=none`、`memory_scope=none`。
拟上限沿用 1,000,000 bytes、1,048,576 pixels、最长边 4096、单帧；图片估值 1024
tokens，加原 150% 估算安全系数。Memory/Diary/Affect/Reader/探索/主动发送均不启用。

模型、QQ 接收、QQ 发送、媒体 GET、付费 Vision、固定部署和准确恢复对象各自有
批准引用。新批准前所有实际目标、身份、账户、镜像/配置/计划摘要、开始截止时间、
操作者/停止人及当前日志头均为 null。历史 STOP 不能填入当前 STOP 核查字段。
若后续 E2 累计限额已加入最终候选，应一并绑定其真实账户与累计账本范围；本草案
不旁路原责任系统，也不预先声称 E2 已生效。

## 获批后的顺序与独立证据

| 阶段 | 需要核对的准确内容 | 私密证据；公开只留摘要/状态 |
| --- | --- | --- |
| 0. 开窗前 | 当前 STOP、唯一活跃 Bridge、无未决 I/O/UNKNOWN/不确定发送；可用隔离恢复路径 | 新回读时间、双端 STOP、identity/epoch/privacy、最新 Ledger/Budget/Window/Spool 头 |
| 1. 主人供图 | 一张非敏感原图；主人/机器人/私聊身份、当前输入文字与图片位置、原始段哈希 | 认证捕获与本次逐条输入批准；禁止把引用/转发图当原图 |
| 2. 原图 GET | 固定 CDN、公网 DNS、数值 IP、Host/SNI/TLS、无代理/重定向/凭据；单次 10 秒 | 下载/解码状态、实际 bytes/MIME/尺寸、media hash 与单次 lease；签名 URL 不进入公开材料 |
| 3. Vision | 原上传证明、实际图片 payload hash、同一 Job、第一次且唯一 content_vision | request/admission/usage hash、FETCHED Observation 和原输入依赖 |
| 4. chat | 只使用本次有据图片观察，不混入历史/M1；原 source/epoch/Ledger/lease 重新核查 | 同 Job 的第二次且唯一 chat、冻结上下文、实际 usage、待发正文摘要 |
| 5. 人审与发送 | 至少两个仅靠视觉能判断、未被输入文字透露的事实；候选符合来源 | 每个细节独立人审记录；原 Outbox 一次发送，不把 ACK 当手机签收 |
| 6. 手机与关闭 | 手机可见一次且无重复；停止后没有补调用/补发 | 手机证据、双端 STOP、最终完整日志头与每个 usage/预留/UNKNOWN 状态 |

两个视觉细节必须不同，例如不同物体的形状/颜色/相对位置，而不是把同一句答案
拆成两次计数。主人私下核对原图和候选，公开材料只引用每项证据 hash，不写照片
正文或私人回复。即使文本回复看似合理，只复述输入、猜测、仅有 UNREAD、没有
有效图片观察或缺 usage，均不能记 `LIMITED_LIVE_IMAGE_PASS`。

该等级还要求收图成功、FETCHED 图片观察、恰好两条真实模型 usage、手机一次签收、
无重复和停止账本闭合全部成立。供应商账单独立记录：调用前为 `NOT_RUN`；已有
usage 但缺对账材料时为 `UNRECONCILED`。账单未到不虚构金额，也不放弃保留预留责任。
公开费用是实际 usage 乘本次配置费率的估值，不是供应商账户外部硬扣费上限。

## Provider 公开复核

2026-10-22 再次读取 DeepSeek 官方
[Vision](https://api-docs.deepseek.com/guides/vision/) 与
[型号/价格](https://api-docs.deepseek.com/quick_start/pricing/)：`deepseek-flash`
公开对应 `DeepSeek-V4.1-Flash-1017`，接受 user 中的 text + inline `image_url`；
当前图片最多计 1024 tokens。公开高峰输入未缓存/缓存/输出价格分别为
USD 0.30/0.006/1.20 每百万 tokens，非高峰为其一半。本申请按较高公开费率提案。
这些是文档证据，账户实际权限、适用价格和窗口内模型版本仍未核验；不改路由、
不试探 Key、不借用旧批准发起兼容性请求。若实际拒绝，保存错误和费用责任后停止。

## 失败与回退

非主人、多图、闪图/表情、坏 MIME/尺寸、来源替换、DNS/TLS/GET 失败、许可/epoch/
Ledger/lease 漂移、缺 usage、UNKNOWN、DELIVERY_UNCERTAIN 或 STOP 未确认，立即
停止扩大动作。下载已领取不能退回重新 GET；图像失败不能降级成猜测回复。
原 Budget Journal/Window Journal/Spool 与 ErasureLedger 保留原责任，不覆盖旧卷、
不恢复旧批准或额度、不重发不确定消息；操作者与停止人必须在实际申请中写明。

新增反例 `tests/test_p13_image_acceptance.py` 在真实认证 localhost WS/HTTP v1 中，
分别让模拟 CDN 流读到一部分后超时、或超过声明/获批字节上限。核对流关闭、只有
一次 GET、没有模型预留/派发/Observation/Outbox、窗口停止、重复批准不重试及签名
URL 不进入日志。该用例不证明真实 CDN 或账户兼容，也没有引入新的下载实现。

最终定向 `./scripts/check.ps1` 对新文件及原图片、原图准入、完整图片链路、硬退出、
付费图片六个测试文件运行：Ruff 通过，**123 passed，55.72 秒**。两条新反例均由既有
行为正确拒绝，因此没有修改下载/权限/账本产品代码。完整组合检查及准确 PR HEAD
CI 由 P13 集成交付另行记录；此处不预填。

引用与转发正文仍未接入；对应公开源码审查和有界后续提案见
[P13-F1](p13-rich-read-proposal.md)。
