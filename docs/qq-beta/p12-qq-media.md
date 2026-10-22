# P12-A：原图准入与富消息覆盖

核查日期：2026-10-22（Asia/Shanghai）。源码起点：
`c6493d481676e1e6684fb49564c2557224950d20`。本包只做离线修复、公开协议复核及
新窗口申请准备；不继承 P11 已结束窗口的运行批准。

## 结果与门禁

| 项目 | 本包证据 | 真实运行状态 |
| --- | --- | --- |
| 普通单图、图文与拒绝表情/闪图 | 固定公开源码 + 合成测试 | NOT_RUN |
| 接收签名、一次领取、下载边界、双调用与原 Outbox | 认证 localhost WS、HTTP v1、原生数据库/账本回归；远端传输模拟 | NOT_RUN |
| DeepSeek Flash 图片协议 | 当日官方文档明确支持；本地实际请求体检查 | 账户权限、供应商实际兼容与账单均 NOT_RUN |
| 引用 ID / 转发 ID / 卡片元数据 | 保留 P11 LIMITED_LIVE_PASS 历史证据；本包补覆盖规则/负例 | 没有新增真人样本 |
| 引用原文 / 转发正文 / 卡片链接正文 | 当前仍 MISSING / UNFETCHED / DISCOVERED | DISABLED |
| 单图申请 | [有界申请模板](p12-single-image-request.template.json) | DRAFT，目标与批准未填 |

`production_ready=false`。没有连接部署主机、访问账户/密钥/私密数据库、下载真实 QQ
图片、发起付费调用或 QQ 发送；新 usage、发信数、手机确认和独立账单均为 `null`。
本包没有增加钱包、媒体缓存、数据库表、模型路由或网络读取接口。

## 固定图片段与修复

依据 SnowLuma 固定提交
[`db142db9e6398c779283eab9afc3552da1e2c361` 的收发 codec](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/event-converter/element-codecs.ts)，
普通收图字段为 `url/file/sub_type/summary`；闪图另带 `type=flash`，商城表情另带
`emoji_id/emoji_package_id/key`。其
[图片上传处理](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/protocol/src/highway/image-upload.ts)
将 subtype 1 用于动画表情。版本名称与公开源码形状不证明实际部署镜像来自该提交。

原 `original_image` 只核查段的顶层键和下载地址，合法 CDN 地址配上表情 subtype、
flash 或未知扩展仍可能取得普通原图范围。现在只有普通 subtype（缺省或严格整数 0）
及上述普通字段能进入 `owner_image_v1`。显式布尔/字符串/null subtype、表情、闪图、
未知字段与错误元数据类型在领取前拒绝。可选字段仍可缺省，保留原有 url-only 兼容。
`file` 为内部标识时不作为本地路径读取；仅允许固定 CDN 的 URL。

一张顶层原图可独立发送或与主人文字混合；两张图（包括同一张重复）拒绝。node、
引用、转发、卡片里的图片不提升到原图范围，CQ 伪标签不产生图片。原图完整规范
JSON 哈希覆盖其元数据，输出只留 `source_sha256/index/slot/UNREAD`，不含签名 URL。
正文位置分区、owner 唯一正文与来源签名保持原路径。

既有下载只允许固定 QQ CDN 的公网 DNS 结果、固定 IP + Host/SNI、正常 TLS 证书验证；
不跟随重定向、不发 Cookie/Authorization、不重试、不落媒体缓存。DNS 后与真正发
GET 请求头前各核验一次来源/租约。新增 HTTPCore 内存网络后端反例确认：TLS 使用
主机名和证书验证，第二次核验失败时零 GET 字节。它不是公网 TLS 或 CDN 成功证据。

Core 继续校验 PNG/JPEG/WebP、单帧、实际 MIME、字节、像素和最长边；两次模型调用
仍为同一聊天作业的 `content_vision → chat`，原 Budget Journal 和 Outbox 负责结算与
发送。Vision 派发/返回、chat 派发和发送前的签名、epoch、最新 Ledger、lease、
payload、观察来源及预算栅栏没有更换。图片仍不能同启历史或长期事实。

## Provider 复核：有公开能力证据，尚无账户实测

2026-10-22 的官方
[Vision 文档](https://api-docs.deepseek.com/guides/vision/)
明确 `deepseek-flash` 接受 user 消息中有序 text 与 `image_url` parts 的内嵌 base64。
其公开限制为单图 32 MiB、请求 48 MiB、最长边 8192 px，图片上限 1024 tokens。
本次拟用 PNG/JPEG/WebP 单帧、最多 1,000,000 bytes / 1,048,576 pixels，代码最长边
4096；不使用外部 URL、Files API 或供应商远程拉取 QQ 地址。

官方 [Chat Completions 协议](https://api-docs.deepseek.com/api/create-chat-completion/)
与现有 `/chat/completions` 请求一致。离线测试实际截获 `deepseek-flash`、
`thinking.type=disabled`、`stream=false`、Vision `max_tokens=300` 及内嵌图片 parts；
所有响应均为合成传输，不能证明真实模型看图效果。

官方 [型号与价格](https://api-docs.deepseek.com/quick_start/pricing/)
当日将 Flash 指向 `DeepSeek-V4.1-Flash-1017`；公开 USD / 百万 tokens 价格如下：

| 时段 | 输入未缓存 | 输入缓存命中 | 输出 |
| --- | ---: | ---: | ---: |
| 高峰 | 0.30 | 0.006 | 1.20 |
| 非高峰 | 0.15 | 0.003 | 0.60 |

高峰为工作日 UTC 01:00–04:00、06:00–10:00（中国法定假日除外）；公开价格可能变化。
申请暂按较高费率预留、图片估值 1024 tokens 并沿用原安全系数；这是本地估算，不能
当作账户收费上限。账户可用性、实际适用价格、额度、独立账单和未来窗口内价格
有效期仍须另行批准后核实。不得自动切换型号/提供方或用旧授权试探账户。

## 富消息的实际语义覆盖

| 收到的段 | 当前可用内容 | 回复边界 |
| --- | --- | --- |
| text | 主人当前文字，保留原位置 | 唯一可能的主人命令/事实来源；仍受各自许可限制 |
| reply | reference ID；原文 MISSING | 问题依赖引用时说明看不到原文、请补相关文字，不猜测 |
| forward | reference ID；正文 UNFETCHED | 不声称看完节点、知道发言人或转发内容 |
| json | 白名单标题/摘要/规范 URL；PARTIAL 或 UNSUPPORTED | 明确属于卡片自述；URL 只是 DISCOVERED |
| image | 独立准入的当前原图 UNREAD；成功后另有 FETCHED Observation | 不能仅凭 UNREAD 声称看见；引用里的图仍不读取 |
| node / record / video / file / other | 类型和位置；UNSUPPORTED | 不提取主体、私密正文、命令或媒体地址 |

模型规则新增缺原文时的自然说明和第三方归属边界，不要求向主人复述内部状态码。
这只约束候选生成；实际表达质量留待真人窗口，人审后才准发送。
未经取得正文不得改为 FETCHED，HTTP v1 契约亦拒绝伪造的 coverage 升级。
卡片、节点、图片摘要中的 `/记住这个`、纠正、删除或工具指令不成为主人命令。

引用 ID、卡片投影变化进入原摘要/幂等冲突/派发与发送检查；源遗忘及旧备份 Ledger
回放清除保留的富消息。未支持节点内部的原文从未保存，改变那些已丢弃字段不会
改变投影摘要；不能宣称签名保存了全部 QQ 原始正文。超深节点先被整个帧深度上限
拒绝，允许深度内的节点则只保留 UNSUPPORTED 标记。

## 引用与转发正文：后续独立设计，当前不实现读取

固定源码确有只读动作，但动作名不能替代安全边界：

- [`get_msg`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/message.ts)
  读取存储事件后会刷新媒体 URL；不能称为纯离线文本查询。返回移除 `self_id`，
  因此调用账号必须由认证连接证明。只读动作也可能引起额外媒体元数据访问。
- [`get_forward_msg`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/extended.ts)
  接收 ID 返回节点；底层
  [message-actions](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/modules/message-actions.ts)
  会获取转发、转换每个节点并解析媒体 URL，也可能附带引用正文。
- 同一底层文件存在引用回填占位 `[引用消息]`。HTTP/action 成功、非空文本或存在
  timestamp 均不足以证明原文已取得；占位和不能证明来源的回填继续 MISSING。

建议未来只读许可分为 quote 与 forward 两项，各次申请仅一个已签收到的 reference，
指定 owner/bot/session/epoch/协议版本；每窗口最多一次 OneBot 查询、总时限 10 秒、
总响应 16 KiB、最多 8 个节点、嵌套最多 2 层、单段文字最多 4000 UTF-8 bytes。
不递归查嵌套引用/转发，不读取附件，不访问卡片链接，不补调用/重试。引用/节点
作者逐个保留为未认证第三方，不成为 owner Memory 或任何控制权限。

这些是待开发合同。目前上游动作在返回前可能遍历大量节点和刷新媒体，Bridge
事后截断响应不能证明上游工作已受限。新范围需先审查其内部网络范围和计数，
或提供可核验的有界文本读取支持；尚无此证据，不部署读取实现。只有正文实际
取得、账号/会话/来源和隐私核验通过，才逐类升级 FETCHED；任何漂移保留缺失状态。

## 新单图窗口申请与停止

模板只申请 **1 条图片输入、2 次模型 HTTP 尝试、1 次私人回复**，单并发；全部尝试
共享原有最多 5 次总窗上限。本申请不安排其他输入/调用，失败不补测。建议聊天最多
10 分钟、批准总有效期 30 分钟，金额最多 USD 0.01（本地账本限制）。准确收件人、
主机、镜像/配置/计划摘要、账户、操作人、开始截止时间、最新 Ledger/Budget/Window
head 与恢复回执仍为 null；填实并获得新的明确许可前，申请不可执行。

获批后由主人主动发一张无敏感信息的原图，只给“请描述图中内容”之类非暗示文字。
检查原图哈希、认证接收、CDN GET、FETCHED 观察、两次模型 usage、待发正文；
人审确认至少两个没有在文字中透露的可见细节后，原 Outbox 仅发一次并由手机确认。
若只凭文字猜对，不算看到图片。验收保存私密来源，公开只给匿名覆盖与状态。

任何非主人、第二张图、来源/权限/epoch/lease/账本漂移、格式/DNS/TLS/下载失败、
缺失 usage、UNKNOWN、DELIVERY_UNCERTAIN、重复或无法确认 STOP，立即停止扩大动作。
停止人使用原窗口 STOP 和服务停止，核对账本尾部、保留未知费用和不确定发送；
不恢复旧授权、不清空日志、不重发。回退保持原服务关闭与原始数据/账本，普通图
准入也不得回退到宽松分类后继续同一批准。

## 离线复核入口

新增 `tests/test_p12_media_boundaries.py` 覆盖原图/图文位置、表情/闪图/未知字段、
认证捕获零副作用、原图元数据哈希、伪造 FETCHED、第三方/超深节点、DeepSeek
请求体及 TLS 后租约失效。既有以下回归继续覆盖来源变更、遗忘回放、上传证明、
最终 HTTP 漂移、异常计费、五次上限、硬退出与混合历史：

```powershell
./scripts/check.ps1 -TestFiles tests/test_p12_media_boundaries.py,tests/test_onebot_images.py,tests/test_live_image_input.py,tests/test_image_capture_crash.py,tests/test_paid_image_budget.py,tests/test_qq_rich_input.py,tests/test_onebot_rich_capture.py,tests/test_rich_history_composition.py
```

本包最终定向结果：ruff 通过、201 项通过（62.45 秒）。其中新增边界与下载文件的
53 项复验包含在上述 201 项内，不累计成 254 项。首次运行发现 URL NUL 错误码回归，
修复后保留原 `IMAGE_URL_NOT_ALLOWED` 分类，最终定向已包含该反例。

完整检查使用 `./scripts/check.ps1`。准确最终提交、检查数量及 CI 链接由对应 Draft PR
记录；离线测试结果不替代 CDN/提供方/手机/跨自然日验收。
