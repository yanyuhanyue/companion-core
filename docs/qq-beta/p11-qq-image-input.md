# P11 A2：单张主人原图

本文保留首版验收合同；后续扩展的常用动图输入见[主人私聊动图](animated-image-input.md)，
多图与记忆组合见[消息分条](segmented-replies.md)。

这是有界文字窗口的独立可选媒体范围。默认 `image_scope="none"`，
`references_v1` 本身不允许领取、下载、上传或调用 Vision。

首版只接受当前已认证主人消息顶层的一张 `image` 段，可同时有主人文字。
第二张图在捕获验证时直接拒绝，重复图片也计第二张；不扫描 node、引用、转发、
卡片中的图片，不补 HTTP 原消息查询。图片段形状参考
[固定 SnowLuma 源码](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/event-converter/element-codecs.ts)。
公开源码和协议版本不是部署镜像源码哈希的证明。

## 批准范围

Core `live_text_window` 增加 `image_scope="owner_image_v1"` 与
`image_approval_sha256`。后者必须等于独立 `paid_model.image_input.approval_sha256`，
并纳入原配置、窗口及付费合同的摘要。要求 `references_v1`、认证捕获公钥、
`history_scope="none"`、`memory_scope="none"`。首版不组合图片历史或长期记忆。

只允许 `content_enabled`、`observation_enabled` 和唯一 `qq_image` 媒体分析 adapter；
Reader、URL 抓取、其他观察适配器、自主作业仍关闭。
`content.use_chat_model_for_vision=true`，Vision 使用同一个批准的聊天模型、
提供方、端点和凭据；不启用独立媒体提供方。

付费图片合同见 [P11 图片预算](p11-paid-image-budget.md)。每个图片输入的原聊天作业
依次调用一次 `content_vision`、一次 `chat`，同一个 logical request。
两次均进入现有 model_usage/admissions/BudgetJournal；总窗口仍最多五次模型尝试、
五个输入、五次发送，单并发。领取前不足两次剩余额度直接关闭，不消费最后一次额度
进行无法完成回复的图片分析。图片估值显式批准，实际 usage 依固定费率结算，非账单证明。

## 来源与单次领取

`payload.text` 仍是唯一主人文字；Memory 和命令不读取图片分析作为主人确认。
原始图片段的规范 JSON 摘要进入 rich image 投影：`index`、`slot=0`、
`source_sha256`、`actor=UNVERIFIED`、`coverage=UNREAD`。保留完整位置分区。
`shared_content` 必须恰为 `[{kind:"image",slot:0}]`；两端独立验证。
引用资料中的图片仍为 `UNSUPPORTED`。

沿用 `window-capture-once` 和逐条 `window-input-approve`。捕获签名覆盖 text+rich，
在原 Spool/WindowJournal 中持久化；逐条批准后才摄入 Core 和领取图片。
claim/check 每次重新校验捕获签名、原身份、来源、时间、输入摘要、窗口、epoch、
最新完整 ErasureLedger 和独立媒体批准。

Core 原 content_items 的 `WAITING→FETCHING` 领取生成一次有效租约。
领取不可退回或重试。Bridge 只在本次 approve 操作局部内存使用原始 URL，
Core 不接收 URL；Spool 接受后原始帧按既有规则清理。
若接受后 Bridge 重启丢失局部 URL，不能重新捕获或恢复 GET。

下载只允许三个固定 QQ CDN 域名。所有 DNS 地址必须公网，实际连接固定数值 IP，
保持 Host/SNI 与正常 TLS 验证；不跟随重定向，不带 Cookie/Authorization。
DNS 后和实际 GET 请求头派发前复查 Core 来源/租约与本地窗口时限。
总下载十秒，字节、编码、MIME 有界；Core 再以 Pillow 校验格式、像素、尺寸和单帧。
下载 task 的 HTTPCore trace 不记录可能包含签名 URL 的响应头，不影响其他 task 日志。

上传以现有捕获私钥在独立固定域签名，覆盖 plan/event/source/lease/media hash/MIME。
Core 在任何付费预留前检查签名和实际上传字节；普通 Bridge HTTP token 不能替换图片。
旧租约、替换字节、替换 MIME 和重复上传均拒绝。

## 已收费结果、发送与恢复

Content 的 RUNNING/usage_id、来源和 payload 摘要先提交，再进行原预算 reserve。
prepare 后、reserve 前硬退出仍保留领取记录，到期 UNKNOWN，不重新领取或调用。
reserve 明确未成功则 FAILED；已经预留或 I/O 不确定时不退款、不回退重试。

Vision 已知 usage 先结算，再检查窗口、来源、租约、epoch 和输出。
原生遗忘若先把 RESERVED 改成 UNKNOWN，实际 usage 仍记录，但 UNKNOWN 单调保留，
不会自动解除阻断。不得用聊天回复掩盖图片失败。

成功分析成为原生 `qq_image/image_analysis/FETCHED` Observation，只有当前 owner event
可以进入图片上下文；同会话旧分享、链接、引用图均不能隐式进入。
冻结 context 记录 source/item/observation/admission 的摘要与有序两次调用证明。
Vision 派发、chat 派发、接受回复和发送前均再核验，RUNNING 租约必须等于已签原租约。
STOP、最新 Ledger 变化、epoch、来源、结果或 payload 漂移均关闭。

本轮验证使用认证 localhost WS、Core HTTP、原生预算/Observation/Outbox，
CDN、模型和发送为模拟传输。另有真实子进程硬退出与 HTTPCore 内存后端回归。
没有真实 QQ/CDN/模型调用、媒体授权或部署验收；实际运行需单独明确批准。
