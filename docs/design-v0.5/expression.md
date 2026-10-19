# v0.5 自然表达与现有表情图库

`ExpressionEngine` 在文字回复上增加可选的表情意图，默认 `expression_enabled=false`。提示词只影响表达长短和自然度，不增加固定人设、推断真实情绪、修改事实或授予权限。模型可以在完整文字末尾选择一个 `⬡类别⬡` 标记；不选择就是普通文字。没有额外模型调用、图片生成调用或独立发送任务。

## 合同与过滤

`ResponseIntent` 的 `text` 必填，`image` 可缺省。图片只携带 `source="astrbot.meme_manager"` 和经过白名单检查的 `category`，不接受 URL、文件路径或任意插件名。默认类别为 `happy`、`like`、`surprised`、`morning`、`sleep`；最多配置 12 个安全类别名。

只有已认证的主人 QQ 私聊事件可以产生图片意图。日记和控制命令、外部观察、主动任务保持文字路径。确定性过滤同时检查当前及最近六条主人消息和本次回复；明确的严肃、痛苦、医疗、法律以及拒绝表情措辞会移除图片。这是保守的词句过滤，并不声称能理解所有隐含语境。未知、多个或非末尾标记只保留去掉标记的文字；模型只输出图片标记时走普通回复失败处理。

默认同一会话 30 分钟内最多一个图片意图；最近八条未取消/未失败回复内不重复同一类别。冷却使用现有 Outbox，因此重启后仍有效，`PENDING` 和 `DELIVERY_UNCERTAIN` 也计入。图片意图和文字在同一个事务内冻结在同一 `delivery_key`，重复调用不会更换已有内容。

## 复用的插件接口

适配 [anka-afk/astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) 的现有图库。2026-10-19 对目标环境的只读源码核验发现：插件目录名是 `meme_manager`，metadata 版本 `v3.20`；`config.py` 的 `MEMES_DIR` 指向 `data/plugin_data/meme_manager/memes`，`CategoryManager.get_descriptions()` 返回类别映射，原插件按类别目录读取 JPG/PNG/GIF。

Bridge 直接只读使用同一个 `MEMES_DIR/<category>/`。图库维护继续使用原插件；Bridge 不运行它的 LLM 响应钩子、后台同步、图床上传、自动发送或 GIF 转换，因此不会绕过 Core 的 Outbox。只启用本地已配置的图库来源，不把来源白名单视为版权证明；图库素材授权仍由图库维护者管理。

`MemeLibrary` 最多检查一个类别的 128 个目录条目，只接受根目录内的普通 JPG/JPEG/PNG/GIF 文件，拒绝逃逸路径和符号链接。Pillow 对实际字节执行解码验证，默认限制 4 MiB、单边 2048 像素、120 帧和累计 1600 万像素。Bridge 对相同字节计算 SHA-256，跨文件名/类别去重；最近 16 个图像哈希仅在进程内保留 30 分钟，没有文字、事件 ID、会话或路径关联。重启后的主要节流由 Core 的持久冷却提供。

发送使用已经验证的原始字节构造 `Image.fromBytes()`，避免校验之后重新读取可能变化的文件。该方法由 [AstrBot 消息组件](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/message/components.py) 提供。Bridge 不创建媒体缓存、不复制图库，也不把图片存入 Core。

## 失败、删除和回执

`deliver_expression` 在 `begin-send` 之前完成本地图像检查和 MessageChain 构造。图库不存在、素材被删除、格式/尺寸无效、Pillow 不可用或图片组件构造失败时，同一次发送退回完整文字，回执包含 `IMAGE_PREFLIGHT_SKIPPED`。文字与图片组成一个 MessageChain，只调用一次平台发送。

一旦开始发送，超时、异常或非明确成功回执统一保留 `DELIVERY_UNCERTAIN`；不能在此时补发文字，因为前一次可能已被平台接受。`begin-send` 回执超时不调用平台，最终回执丢失也不重发。图片成功选择或构造不等于已经送达，表达模块不写入“成功发送图片”的独立经历。

文字和图片意图都只保存在 `outbox.content_json`，复用现有遗忘闭包：源事件删除清空整个内容和哈希，待发送内容取消，正在发送内容保持不确定。独立遗忘日志重放到旧备份时同样清空。原插件图库属于独立维护的素材库，不随某次对话遗忘删除；本模块没有另建个人媒体图库或持久媒体副本。

## 接线与验收

Core 在初始化时创建 `ExpressionEngine(store, settings, clock)`；构建聊天上下文时将非空 `instruction()` 加入系统提示；普通回复入队用 `ExpressionEngine.enqueue(event_id, conversation_id, reply, now, action=...)` 替换直接 `enqueue`。这保留原 delivery key、哈希、租约和回执状态机。失败通知可继续调用原文字 `enqueue`。

Bridge 单独设置 `expression_enabled=false`，初始化 `MemeLibrary(StarTools.get_data_dir().parent / "meme_manager" / "memes", enabled=...)`。泵调用 `deliver_expression(client, item, library, build_chain, context.send_message)`；`build_chain(text, bytes_or_none)` 只做本地组件构造，图片组件必须使用字节，不能自行发送。两端开关都开启才会发送图片。Bridge 依赖 Pillow；关闭时仍可处理原来的纯文字 Outbox。

自动化覆盖默认关闭、意图校验、严肃与近期语境、冷却和重启、类别及内容哈希去重、来源/路径/尺寸/帧数限制、一次组合发送、本地失败文字降级、网络结果不确定、禁止重发和删除后旧备份重放。目标 QQ 图片实际送达仍需在明确启用后做一次协议端验收；本阶段只读取既有插件源码，没有远端修改或发送 QQ。
