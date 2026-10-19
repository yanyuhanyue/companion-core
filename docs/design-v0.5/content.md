# M2 主人分享内容

M2 优先处理主人私聊中实际分享的链接、QQ JSON 卡片和截图，再把读取结果带回当前对话。`content_enabled=false` 默认关闭；评论互动和完整视频理解不在此能力内。B 站只有标题、简介和已分析封面时，不能声称看过视频。

## 入站与读取

Bridge 在原有 QQ 主人、私聊、机器人身份验证之后运行 `prepare_shared`。普通链接与卡片中的 URL 只允许 B 站视频、小黑盒帖子；卡片标题、描述、指令性字段不变成主人文字。B23 短链最多两跳，只允许落到白名单 B 站视频路径，最多耗时 10 秒，拒绝跨站落点。

`payload.shared_content` 是可选清单，最多包含两条链接和两个图片槽位：

```json
[
  {"kind":"link","url":"https://www.xiaoheihe.cn/app/bbs/link/123456"},
  {"kind":"image","slot":0}
]
```

图片清单不携带签名 URL，也不把 URL 哈希冒充媒体哈希。QQ CDN URL 只留在 Bridge 本次处理内存中；Core 验证实际图像字节后才记录 SHA-256。旧消息没有清单时保持原有 v1 内容；计算入站幂等指纹时需排除空的新增清单，以兼容升级前积压消息。

`ContentService.schedule(event_id)` 以已验证的主人事件为根，创建 `content_items`。链接复用 `ExplorationService.create(..., source_event_ids=[event_id])` 的持久请求、来源限定、流量预留和不确定结果状态。它使用共享的探索 GET 配额，不要求开启自主浏览，不为主人这次阅读创建主动分享任务。

Core 等待 `status(event_id).pending` 在有界时间内结束，然后将 `context_message(event_id, max_bytes)` 返回的 JSON 加入对话。该函数以整个 JSON 的 UTF-8 字节数计量；优先当前分享，保留来源事件、URL、读取状态和不可信标记，正文只能原文截断并注明 `excerpt=true`。较旧分享先舍弃，不生成未经验证的摘要。`CONTEXT_RULES` 应加入系统规则，外部正文、卡片和图片文字不具有指令、执行或授权效力。

## 截图与正文配图

截图只接受带图片槽位的主人 QQ 私聊事件，上传窗口默认 300 秒。每张图片先按 `max_image_bytes` 预留流量和一次图片名额，Bridge 读取 Core 状态后调用 `claim_upload(event_id,"image:0")` 原子领取下载权，之后才执行一次 QQ CDN GET。重复领取、已完成、失败、不确定、超时或配额不足都不会再次下载。

Bridge 的原始字节 POST 使用 `Content-Type: image/png|image/jpeg` 和 `X-Content-Key: image:0`。Core 的 `analyze_image` 再校验来源槽位、有效期、图片解码、尺寸、费用和幂等性，调用独立 `HTTPMediaProvider` 的视觉接口。截图使用 `qq_image` 观察来源，不复用摄像头设备授权；通用观察上传 API 必须拒绝直接提交 `qq_image`，使其只能由此次有预算的内容处理产生。

帖子读取返回的 `ReadItem.image_urls` 只是最多两个候选。正文观察提交后，`register_link_images(owner_event_id, observation_id, urls)` 检查原始主人事件依赖，再持久排队。`run_once()` 一次处理一张配图。小黑盒配图只允许 `imgheybox.max-c.com`、`imgheybox1.max-c.com`；B 站只允许 `i0.hdslb.com`、`i1.hdslb.com`、`i2.hdslb.com`。请求强制 HTTPS，移除查询参数，不跟随重定向；来自另一个平台或私有地址的图片不会读取。

同一主人消息的 QQ 截图与正文配图合计最多两张。自主探索没有主人分享来源时不进入配图分析。原读者适配器需配置 `media_analysis=true` 才能产生该平台的图片观察；QQ 图片使用 `content.image_adapter` 指定的 `qq_image` 适配器。

默认每图最多 1 MB、单边不超过 4096 像素、累计不超过 1600 万像素，只接受静态 PNG/JPEG。下载总截止 10 秒，模型截止默认 20 秒。Core 和 Bridge 均不保存原图或媒体缓存；媒体只存在一次调用的内存缓冲中。返回观察包括真实媒体哈希、处理模型、根事件依赖与 `image_analysis` 层级。

## 配额、失败和遗忘

`ContentConfig` 提供单图大小、每日图片数、每日预留字节和独立图片模型日预算。视觉端点完整 URL、key、model 和每次请求费用上限来自 `MediaEndpoint`；费用上限或日预算为零时不调用模型。费用预留同时计入 Core 全局月预算，包括其他模型与工具执行的 RESERVED/UNKNOWN 成本。

截图与配图在模型调用前原子预留费用。同一槽位同样字节成功后可返回已有结果，不再次调用；同一槽位不同字节发生幂等冲突。网络异常、取消、进程丢失、过期租约或无法确定结果的调用保留预留并进入 UNKNOWN，不自动重试。提交时重新核对源事件、隐私版本、租约与费用请求 ID，防止晚结果复活已删除内容。

内容、图片元数据和来源 URL 仅存于 `content_items` 及标准 Observation。`erase_content` 加入现有 `ErasureLedger.apply`：删除主人分享或其正文观察时，清理 URL、图片哈希、指纹、结果指针和租约；Observation 的依赖闭包撤回所有派生正文与图片分析，独立遗忘日志在旧备份恢复时重放相同删除。原图没有本地持久副本需要另行恢复或清理。

## 复用证据

接口研究固定到 [Zhalslar/astrbot_plugin_parser 的 636adb4](https://github.com/Zhalslar/astrbot_plugin_parser/tree/636adb471df98ea6b1cf7a70c45f0bc3bdf26687)，仓库声明 MIT 许可证。其小黑盒低层 `_fetch_link_tree` 返回 JSON 的 `result`，`_parse_body_segments` 解析 `link.text` 中的有序文字/图片块；B 站读取 `Video.get_info()` 后提取元数据。顶层 `parse_video` 会立即创建视频下载任务，通用媒体构造器也会启动下载，因此本阶段只参考低层 GET 和纯文本解析边界，不启动插件完整发送流水线。[相关小黑盒源码](https://github.com/Zhalslar/astrbot_plugin_parser/blob/636adb471df98ea6b1cf7a70c45f0bc3bdf26687/core/parsers/xiaoheihe.py#L382)、[B 站下载触发点](https://github.com/Zhalslar/astrbot_plugin_parser/blob/636adb471df98ea6b1cf7a70c45f0bc3bdf26687/core/parsers/bilibili/__init__.py#L229)。

独立小黑盒 HTTP 读取可使用 [xhh-workshop 的 3f1ca9a](https://github.com/Sakura520222/xhh-workshop/tree/3f1ca9aee836691f134a2c744ff64fe6f3534ef6)：`GET /api/feeds?page=1&limit=N` 返回上游 `result.links`；`GET /api/post/detail/{link_id}?owner_only=1` 返回 `result.link`。服务需要自身登录配置，外层 Bearer token 需显式配置。它同时包含写帖、评论和 Agent 路由，因此 Core 的读取适配器只允许指定两个 GET 路径。仓库声明 `GPL-3.0-only`；这里通过独立 HTTP 边界接入，未复制其 Rust 实现。[HTTP 路由](https://github.com/Sakura520222/xhh-workshop/blob/3f1ca9aee836691f134a2c744ff64fe6f3534ef6/crates/xhh-http/src/routes/feed.rs#L24)、[鉴权开关](https://github.com/Sakura520222/xhh-workshop/blob/3f1ca9aee836691f134a2c744ff64fe6f3534ef6/crates/xhh-http/src/main.rs#L34)。这次核实的是源码实现，没有读取用户平台凭据、安装远端服务或假定当前账号已完成登录验收。

## 配置与运行

- Settings 增加 `content_enabled=false` 和 `content=ContentConfig()`；Core 初始化 `ContentService(self)`，有效分享入箱后调用 `schedule`。
- HTTP Bridge 鉴权：`GET /internal/v2/content/{event_id}` 返回 `status`；`POST .../image/claim` 的 `{request_key}` 调用 `claim_upload`；`POST .../image` 的有界原始字节调用 `analyze_image`。只对图片上传路径按 `content.max_image_bytes` 调整请求体上限。
- Bridge 在身份验证后调用 `prepare_shared`，持久化不含图片 URL 的清单；入箱接受后调用 `forward_images`。QQ 协议发送与内容读取职责分开。
- 阅读完成后为主人来源调用 `register_link_images`，内容 worker 调用 `run_once`；聊天 worker 用 `status` 做有限等待，为 `context_message` 优先分配实际可用上下文字节预算。
- 隐私闭包调用 `erase_content`；通用 Observation HTTP 接口不接受 `qq_image` 直接写入。

启用这条路径需要 Core 的 `content_enabled`、`observation_enabled`，相应 B站/小黑盒观察适配器及读取请求/流量额度；自主探索开关可保持关闭。Bridge 另设 `content_enabled` 与对应读取器开关、独立适配器令牌。图片需配置 `[content.vision]` 的 HTTPS 完整端点、模型、密钥和每次费用上限，同时给予 `image_daily_micro_usd` 额度。

正文配图由 `python -m companion content-worker` 独立处理；Compose 使用显式 `content` profile。它和聊天、日记、生活及工具 worker 分别持有独立实例锁。主人分享触发读者的短时快速检查；图片使用容量16、最长60秒的内存队列，等入箱202确认后传输，卸载立即取消并清空。无有效配置时返回失败证据，聊天不会无限等待；默认等待上限30秒，正文配图不会延长该消息的原始等待截止。

已有 QQ v1 事件保持兼容；新增空清单不改变旧幂等指纹。恢复旧备份会将尚未完成的内容任务标为 UNKNOWN，避免旧图片授权或旧读取请求再次执行。

测试覆盖卡片与短链白名单、正文与图片真实来源、总图片上限、GET 下载边界与慢流总截止、完整 UTF-8 上下文预算、一次下载领取、视觉费用与共享月预算、重复/冲突/未知请求、过期晚结果，以及源删除与旧备份重放。外部真实账号读取和 QQ 图像送达仍需在部署配置完成后验收。
