# QQ 引用、卡片和多图理解

本页适用于原生 `companion_onebot_bridge` 链路；多图原图与批准记忆的联合理解通过签名受控窗口启用。Bridge 将主人当前文字与引用资料分别传入 Core。引用、转发节点、卡片、识图结果及记忆都有各自的来源；其中只有主人当前文字可以触发命令或作为新的主人事实证据。

## 已实现的路径

| 输入 | 处理方式 |
| --- | --- |
| 引用消息 | 优先读取消息链中的正文；缺失时按消息 ID 调用 `get_msg`。 |
| 合并转发 | 按原顺序展开文字节点，保留显示昵称和节点路径，支持有限层数的嵌套。 |
| QQ JSON 卡片 | 提取支持的标题、摘要和链接字段；Bilibili 视频与小黑盒帖子链接交给已有 Reader。 |
| 多张图片 | 一次主人消息最多 4 张顶层普通图片，每张独立编号、领取、下载和校验；批量识图一次。 |
| 图片与记忆 | 识图线索参与限定范围的记忆检索；核对候选后，将原图、确认记忆及当前问题一起交给视觉模型回答。 |

例如两张耳机图片配合“结合我以前的需求选哪个”，最终请求同时包含两张原图和与耳机有关的获准记忆。回答规则要求区分图片可见细节、以前明确确认的需求与推测。图中文字不会被自动记成主人偏好。

这些路径复用现有事件、Content、Reader、Memory、预算账本和 Outbox。模型适配器只负责请求与响应。

## 引用正文与卡片

常驻 Bridge 在自身配置和 Core 配置中分别启用 `rich_input_scope = "references_v1"`；受控窗口则在 Bridge 窗口计划的 `live_text.rich_input_scope` 与 Core 的 `live_text_window.rich_input_scope` 中声明该范围。每条事件最多查询 4 次、等待 5 秒、展开 3 层嵌套、保留 32 个文字节点和 6,000 字符，单次 OneBot 响应最多 64 KiB，最终富消息投影不超过 16 KiB。循环引用、过深节点和超量内容会留下明确的缺失或截断标记。昵称不作为已验证身份。

读取成功、部分读取、原文缺失、不支持和截断状态分别进入上下文。只有占位符或媒体段时，不会将其冒充正文；上下文规则要求模型说明缺失范围。读取发生在主人私聊认证和重复消息检查之后；消息已提交 Spool 后的重放，以及受控输入审批，复用已绑定的投影，不再次查询可能变化的原消息。

Bridge 收到 Core 的成功接收或事件已删除回执后，会清除对应收件箱正文与捕获证据文件；重启会补做回执提交后尚未完成的删除。待批准或待上传的数据沿用原收件箱生命周期。Core 内的引用资料随原事件删除，旧备份恢复时仍执行删除日志回放。

卡片链接仅来自当前卡片的受支持字段。标准分享链接中的跟踪查询参数被丢弃；小黑盒固定分享路由只保留唯一合法的内容 ID。引用正文中的 URL、未知域名和短链不会自动成为 Reader 任务。支持的链接仍受已有 Reader 请求数、响应字节、来源授权和删除规则约束。未配置 Reader、读取失败或到期时，标题和摘要仍明确标为卡片自述。

受控窗口读取卡片正文需在原窗口配置中指定 `link_scope = "owner_links_v1"`，同时启用 `content_enabled`、`observation_enabled` 和所需的 Reader adapter，并运行对应的 [Source Reader 进程](design-v0.6/source-reader.md)。该字段随完整窗口计划、Core 配置摘要和接收签名绑定。Reader 在领取、实际网络请求前和结果落库时重新检查来源与窗口状态。同条输入还带图片时，卡片读取等待图片下载和批量识图结束后再领取。

## 多图与联合记忆的配置

多图在原受控窗口中沿用 `image_scope = "owner_image_v1"`、独立图片准入和签名上传；该范围不是常驻 Bridge 的顶层配置项。Core 同时启用 Content、Observation、`content.use_chat_model_for_vision = true`，并配置 `source = "qq_image"`、`media_analysis = true` 的图片 adapter。调整现有配置时应同时核对：

- `content.max_images_per_event` 与 `paid_model.image_input.max_images_per_request`，均可设为 1–4；每张重复图片也占一个位置。
- 每图字节、像素及固定输入 token 估值，以及累计输入、单次、单作业、每日和窗口费用限制。两次视觉请求都按实际图片数量估算；收到 usage 后按原账本结算。
- 普通图片问答的 `per_request_calls = 2`：一次批量 `content_vision`，一次携带原图的 `chat`。
- 联合记忆的 `memory_scope = "approved_facts"`、已确认记忆清单、记忆范围摘要和 `memory_enabled = true`；`per_request_calls = 3`，为可能的 `memory_review` 保留额度。没有候选时只进行两次调用。
- 该图片窗口保留 `history_scope = "none"`；长期事实的范围单独核对，不通过历史图片或未批准记忆扩大范围。

联合理解的顺序为：批量识图提取线索 → 在批准事实中检索并用 `memory_review` 核对候选 → 将原图、选中的确认记忆和当前问题交给 `chat`。没有检索候选时跳过核对调用；图片线索本身不作为长期记忆证据。

原图仅通过内嵌 base64 传给同一个获准视觉模型。支持单帧 PNG、JPEG、WebP。API 与聊天 worker 之间的原图暂存在数据库旁的私有目录，有效期为上传后 300 秒；事件正文、数据库和账本只保留必要的来源、摘要与状态，不存原图字节或 QQ 签名 URL。最终调用前重新核对原图哈希、隐私版本和到期时间。成功、失败、取消、停止窗口和删除来源会移除暂存文件；启动及 worker 运行时清理到期文件，删除回放也处理旧备份旁残留的原图。

常规聊天启用后台记忆提取时，应同时运行 `life` profile，由现有 `companion-life-worker` 执行提取。本页受控图片窗口只读取批准的事实，检索和核对随聊天 worker 执行；图片参与检索不会创建新的长期记忆。

## 上游依据与验证范围

协议操作参考 [OneBot v11 官方接口](https://github.com/botuniverse/onebot-11/blob/master/api/public.md)，正文解析顺序参考 [AstrBot 固定版本的引用解析器](https://github.com/AstrBotDevs/AstrBot/blob/e5e21b71aec5642f110eb115eb8e9a0d8028b7e5/astrbot/core/utils/quoted_message/extractor.py)及其[测试](https://github.com/AstrBotDevs/AstrBot/blob/e5e21b71aec5642f110eb115eb8e9a0d8028b7e5/tests/test_quoted_message_parser.py)。多图请求采用 [DeepSeek 官方视觉接口](https://api-docs.deepseek.com/guides/vision/)支持的有序文字与 `image_url` 输入。

固定 SnowLuma 的 [`get_msg` 实现](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/message.ts)会刷新消息中的媒体 URL；[`get_forward_msg` 的底层转换](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/modules/message-actions.ts)也会解析节点媒体地址。Bridge 不下载或保留这些附带 URL；Bridge 的次数和时间限制不能约束协议端内部刷新的全部成本。

本轮验证使用合成消息、认证本地 HTTP/WebSocket、真实数据库与文件、模拟模型和网络传输，覆盖幂等、费用、来源漂移、删除回放及停止窗口。未调用真实 QQ 或付费模型；实际正文可取得率和视觉回答质量仍需在已配置运行环境验证。
