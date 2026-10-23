# 表情入库识图与聊天检索增量

本模块从已验证的 `07016eb21b3168145e70fdeb200da18bcf2e52fa` 独立实现，
保留第一阶段 MM01 来源绑定、MM02 逐帧媒体限额、冷却、UNKNOWN 和 Outbox 所有权。
状态是 **模块实现 / Mock 合同验收**。真实视觉模型质量、Core HTTP/admission/worker
接线和 QQ 自动选发仍由集成与部署负责人验收。

## 运行边界

`meme_caption.py` 是无存储、无权限、无网络的 DTO、严格 JSON 解析和纯采样参考函数。
`meme_semantic.py` 在已有 Bridge spool SQLite 内保存待识别状态、caption 缓存、
Core job 绑定和计量回执副本；`meme_semantic_search.py` 保存/查询本地描述向量。
Bridge 不导入 Core、不安装另一个 embedding 模型、不持模型密钥，也不直接访问模型 provider。
最终集成可将纯合同/采样文件机械提取为共用纯包；共享打包与原 HTTP v1 接线由集成负责人完成。

Core 继续唯一拥有模型 admission、durable job、原始 usage、费用预留/结算与权限验证。
本地缓存中的 usage 只是原回执副本，不是另一个费用账本。模块每次 `advance()`
只提交一次或查询一次，不启动后台线程、轮询循环或第二个调度器。

## 从来源审核到可检索

在来源/许可审核和 MM01/MM02 文件检查完成后创建 `SemanticLibrary(catalog, identity, spec)`。
它只为既有合格素材建立语义门，并使随后 `catalog.add()` 的新素材进入 `PENDING`。
此时 `run_discovery` 把素材列在 `pending_caption` 中，不把尚未识别的图算成 `accepted`。
普通合格图在 caption 通过后自动成为 `READY`，无需逐张询问主人。

现有 `TrustedReviewer` 中“来源审核必须已有语义描述”的耦合，需要集成方拆开：
其许可文件、固定版本、实际 acquisition/hash 检查保持；不再因缺 caption 阻止暂存。
caption 的置信度或内容判断永远不能批准来源。

原 catalog 的类别/标签挑图路径跳过所有已托管素材，即使它是启用语义门之前创建的实例。
语义候选、`prepare_selection` 和 `mark_sending` 均检查当前来源、模型版本、成功的 Core
绑定和冻结候选证据。`PENDING/QUARANTINED/INVALID` 素材不能选发，找不到就继续文字。

## Caption 请求、回执和缓存

```python
identity = CaptionIdentity(model_version="Core配置的精确视觉模型版本")
spec = EmbeddingSpec(
    model_version="BAAI/bge-small-zh-v1.5@46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59",
    dimensions=512,
)
semantics = SemanticLibrary(catalog, identity, spec)
status = await semantics.advance(
    asset_sha, caption_client, context,
    check=async_source_and_window_guard,
    commit_check=sync_source_and_window_guard,
)
```

`caption_client.submit(CaptionRequest)` 与 `caption_client.get(job_id)` 是唯一识图接口。
生产实现只连接经过认证的 Core HTTP v1；Core 用原始 bytes 复核 hash、格式与限额，
按原工作流预留模型预算，然后异步处理。`CaptionRequest` 包含：

- 原始 `data/asset_sha256/mime/byte_length` 与 `source_evidence_sha256`。
- `event_id/reply_revision/discovery_job_id/attempt_key`，只用于授权与幂等，不进入模型提示。
- Core 配置权威提供的 `model_version/prompt_version`。
- 由上述内容、提示词摘要和结果 schema 摘要生成的 `request.sha256`。

来源哈希使用固定对象：`schema_version:1`、`source_page`、`asset_url`、`license`、
`usage_basis`、`fetched_at`、`revision`、`acquisition`（解析后的对象）。时间戳保留来源完成
回执原值；不包含 caption/tags。规范 JSON 使用 UTF-8、`ensure_ascii=False`、排序键、
紧凑分隔符、`allow_nan=False`，拒绝重复键。不得对任意原 JSON 字符串直接做来源指纹。

`CaptionOutcome` 的唯一合法状态为 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN`。
它必须绑定 request/asset/source/model/prompt 和 job ID；仅 `SUCCEEDED` 携带结构化结果。
所有字段有长度、枚举和有限数值检查：description/visible_text 各最多 1000 字符，
tags 最多 16×32 字符，suitable/avoid 最多 8 个已知意图，use_when/avoid_when
最多 8×80 字符，tone/target 有固定词表。未知梗、人名、出处不得臆造。

`content_status` 标记清楚、不确定或文字/画面冲突；`temporal_status` 标记静态、
采样一致、含义变化或不确定。默认置信度低于 0.8、冲突、含义反转、对象不明等情况
进入 `QUARANTINED`。明显侮辱文字不会因模型给了“温暖”标签而通过。

`usage` 独立于 caption 内容状态，来自 Core 原 admission：

- 尚未实际 admission 的 `QUEUED/FAILED/CANCELLED` 可以是 `null`。
- `RUNNING/SUCCEEDED/UNKNOWN` 必须携带真实 `usage_request_id` 与回执。
- 已有回执后不能改成 `null`，不能换 request ID、降级已确认费用或丢失已知 token 数。
- `SETTLED` 才有确定的 `billed_micro_usd`；`RESERVED/UNKNOWN` 的未知费用保持 `null`，
  已知 token 数仍保留。成功语义与未知费用可以并存，预算仍由 Core 保守保留，不退款。
- 格式错误、撤回、来源失效或 STOP 后收到的真实 usage 仍先记入本地副本；内容不进入索引。

缓存主键为**原文件 hash + caption 模型/提示词版本 + 提示词/schema 摘要**。
成功/低置信的同版本结果不会自动重复识别；RUNNING 只查 job，UNKNOWN 和提交结果不明
不会重新 submit。`accept(request, outcome, ...)` 接受 Core 对同请求的恢复/结算结果，
本身不调用模型。Core 必须按 request/attempt 幂等保存 job 和原 usage。

同一图片换了新的合格来源，可由已完成来源审核的调用方使用 `rebind_source()`：
旧候选/索引立即失效，再请求 Core 复核当前来源 proof。Core 返回 `cache_hit:true`、
当前 request/source proof 和原 usage，不因来源时间戳变化再次付费。没有当前来源成功绑定，
旧 caption 缓存不能使图片重新可用。移除的素材不能通过 rebind/重新发现复活。

## GIF 采样与可见范围

`sample_frames(original_bytes, library)` 提供可复用的确定性参考算法：先执行所有 MM02
验证，按实际帧时长选首帧、尾帧及均匀时间位置，最多 5 帧，生成最长边 768 的 PNG。
返回每个 sample 的帧号、相对时间、SHA-256，以及总帧数、时长和 `complete/sampled` 覆盖。
没有显式时长的 GIF 使用 100ms；非法/零/超限时长拒绝。原始文件和其 hash 始终保留。

这些信息必须由 Core 从原图重算或复核，再附加到 model 输出；不能让模型自报覆盖范围。
`caption_model_schema()` 只包含模型可填写的语义字段，不含 `sampled_frames`；
`CaptionResult` 则是 Core 附加采样证明后的完整结果，两者共同绑定到 schema 摘要。
模块提交成功 caption 时也用原图复核采样证据。超过 5 帧时只能描述已提供采样的含义；
未采样的瞬时变化仍是实际视觉效果验收的限制，不能声称整段动图已完整理解。

## 增量索引与当前语境

`await semantics.index(sha, embedder, check=..., commit_check=...)` 只对新的/变更的有效
caption 调用 `embedder.embed(texts)`；已有匹配索引不会再次调用。
输入最多 8 条、每条最多 2000 字符，接口结果为
`{model_version, dimensions, vectors, local:true}`。当前 Core 中文 BGE 固定版本为 512 维；
模块允许显式 `EmbeddingSpec`，但每次都检查版本、维度、有限非零向量和本地标记。
更换 embedding 版本只重建相关向量，不重新调用视觉模型。

聊天查询由 Core 在主人输入已接纳后，使用当前 event/revision 生成有界语境及本地 query
向量，再交给 Bridge。模块不修改旧签名入站 offer，也不自行增加 HTTP 服务：

```python
offers = semantics.candidates(
    "meme:" + event_id,
    {"text": bounded_context, "intent": "comfort", "target": "recipient"},
    query_vector=core_local_vector,
    limit=8,
    check=current_event_guard,
)
```

仅有 `READY` caption 能参与检索。先按适用/避用意图、语气和说话对象过滤，再结合文字
匹配与有效本地向量排序；无向量时只回退到已验证的 caption 文本。安慰不会选嘲讽/嘲笑图；
`self` 指表情说话者对自己的自嘲，不能替换成对接收者的嘲讽。无匹配、解析失败或过期索引
不会随机补发。私聊 query 文本/向量不保存到本地图库，仅保存冻结查询摘要。

最多 8 个候选沿用 `candidate_id/description/tags/category` 四字段；描述附上可见文字、
语气和对象，全部作为不可信数据交给原聊天模型选一个或 NONE。冻结集合在后续重试中不会
扩展；原图 hash、caption/source/profile 快照在准备和发送前再次核对。发送始终走原 Outbox。

## 失效、容量与装配责任

删除、来源失效或字节变化会使旧 caption/index/冻结候选不可用；`invalidate(sha)`
立即封禁并移除向量，`catalog.remove(sha)` 同时清除原图。换模型/提示词版本不使用旧语义，
换索引版本不混用旧向量。每次 await 后及事务提交前检查来源、STOP、撤回和当前配置。
调用方须把已有 ErasureLedger/撤回重放映射到这些入口，恢复旧备份时不能仅信任 READY 标签。

原图沿用现有资产数量与字节上限；caption 缓存最多 `4 * max_assets` 条，job/source
绑定与候选沿用 `max_bindings`。不要为腾位置静默删掉 UNKNOWN 或实际计量回执。
生产调用方仍须执行原 spool 整体磁盘容量检查，包含 SQLite/WAL 和新增元数据；
同步 commit guard 可组合该检查。达到限额时停止新工作，当前文字回复继续。

集成方负责：许可 reviewer 拆分、Core 原图/admission/worker/usage、共用纯合同打包、
现有 BGE 的薄适配器、post-inbox 查询/独立 job-bound offer、最终发送检查及真实 HTTP+独立
worker 合成验收。本模块没有修改 Core、API、配置、迁移、模型、预算或发送文件。

## 固定样本结果与验证

测试中的图像/描述均为确定性夹具，caption client 模拟 Core 的异步状态、缓存、采样和 usage，
embedding 使用显式 512 维 Mock 向量。以下结果是**标注语义进入检索后的质量与约束结果**，
不是视觉识别准确率；这些语义质量样本未调用真实视觉模型或真实 BGE 推理。

| 固定案例 | 期望结果 |
| --- | --- |
| 难过、想要抱抱 | 只返回温暖安慰图 |
| 安慰中出现“嘲笑/搞砸工作”字样 | 排除高词汇匹配的嘲讽图，保留安慰图 |
| 说话者对自己的工作失误自嘲 | 返回 self 图，排除 recipient 嘲讽图 |
| 未指定自嘲对象 | 不把自嘲图强行用于接收者 |
| 和善画面描述但文字为“你真笨活该” | 隔离，不选发 |
| GIF 后帧把抱抱改为嘲笑 | `changes` 隔离，不只信首帧 |
| 完全不相关的天气查询 | 空候选 |
| 无字词重合的同义语境 | 有效 Mock 向量补充召回，视觉调用次数不增加 |

相关回归还覆盖缓存/进程重开、模型/提示/索引版本切换、来源重绑缓存复用、删除后旧实例
准备/发送围栏、await 期间撤回/STOP、UNKNOWN/null usage、错误身份与采样证据、向量异常、
最多 8 个冻结候选、费用回执恢复及无重复 submit。最终实际结果与精确提交由
`memes-status.json` 的 `semantic.verification_receipt` 指向，旧阶段回执保留。

参考 [Meme Manager 固定 caption 实现](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/88b8a1ba12a8969cab70b31e6cdc258f61620fcd/backend/semantic/caption.py)
的入库描述/少量 GIF 帧思路，以及[固定 query 实现](https://github.com/anka-afk/astrbot_plugin_meme_manager/blob/88b8a1ba12a8969cab70b31e6cdc258f61620fcd/backend/semantic/query.py)
的版本化索引与本轮候选 ID 校验。独立编写、未安装 AstrBot、未复制其消息系统。
