# P13-F1：引用与合并转发的有界深读研究

状态：`DESIGN_ONLY`；实际 `get_msg/get_forward_msg` 查询、正文签收、CDN 和模型调用
均 `NOT_RUN`。不新增读取接口，不启用 Reader，不改变现有 MISSING/UNFETCHED。
基线 `eb5143500bed5395bb45f2a2cc20335da9988491`。

## 固定公开源码的工作边界

审查的是 SnowLuma
[`db142db9e6398c779283eab9afc3552da1e2c361`](https://github.com/SnowLuma/SnowLuma/tree/db142db9e6398c779283eab9afc3552da1e2c361)，
不是对当前实例版本、镜像或已登录账号的回读。公开版本号无法单独证明部署来源。

| 入口与固定来源 | 可以证明的行为 | 不能从动作名推出的结论 |
| --- | --- | --- |
| [`actions/message.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/message.ts) 的 get_msg | 取得事件后调用 refreshMessageMedia；返回删除 self_id | 不是纯本地、零媒体网络访问；返回本身不证明调用账号 |
| [`modules/media-actions.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/modules/media-actions.ts) | 逐媒体段尝试更新 URL，失败仍可能返回原 URL | action 成功不表示媒体可读，也不表示原地址仍有效 |
| [`actions/extended.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/extended.ts) 的 get_forward_msg | ID 查询返回 messages | 一次 OneBot 请求不等于一次受界定的上游网络动作 |
| [`modules/message-actions.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/modules/message-actions.ts) 的 getForwardMessage | 获取转发后逐节点转换，使用媒体解析器并允许内嵌引用内容 | Bridge 收到后截断 8 个节点，不能限制上游之前已做的工作 |
| 同文件的引用回填 | 可构造 `[引用消息]` 占位，缺真实时间时采用回填时间 | 非空文本和时间戳不证明真实引用原文存在 |
| [`event-converter/to-segment.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/event-converter/to-segment.ts) | 普通 push 不请求完整引用；转发转换可以附 message/qq/time/seq | 节点自带发送人声明不等同认证 owner 正文 |

因此当前缺少的不是一条 HTTP 调用，而是可验证的上游工作限额、账号/会话归属与
原文来源证明。保持现有 ID/元数据覆盖比在成功响应后直接标 FETCHED 更准确。
占位或无法证明原始来源的回填仍为 MISSING；卡片链接仍只是 DISCOVERED。

## 最小后续合同（未实现、未批准）

先审一条引用，再单独审一条合并转发；两类许可不合并。每份只绑定当前已认证主人
私聊捕获中的一个 reference、owner/bot/session、protocol/privacy epoch、准确代码/
镜像/配置、原输入签名与 hash。只允许此 reference，不能把模型产出的 ID 作为目标。

建议上限：每窗口 **1 次** OneBot 查询、总时限 10 秒、返回总量 16 KiB、最多 8 个
节点、最多 2 层、每段正文最多 4000 UTF-8 bytes；零递归查询、零重试、零附件 GET、
零卡片 Reader、零写入和发送。未知字段不增加权限。上游媒体 URL 刷新或其他内部
请求必须另有可核查的范围与次数上限，或使用经独立审查的有界纯文本读取支持。
目前固定接口不能仅靠 Bridge 截断证明这些上限；前置证据缺失时不准入。

在读取前、返回后和纳入后续上下文前复查原输入、许可/时间、身份、隐私 epoch、
最新 Ledger、来源有效期及当前会话。作者逐节点保持独立、未认证第三方；姓名/ID/
引用中的“我”不改成 owner，任何斜杠/删除/记忆/工具文字均只作不可信资料。
只有实际原文和上述核验全部齐全才逐类升级 FETCHED；缺失、占位或限额外内容仍
如实缺失。原文如需持久化，应进入现有原事件/Content 的出处与遗忘闭包，不建副本库。

## 准入前必须复现的独立反例

- 原 reference、账号、会话、发送人、正文位置或内容发生变化；相同 ID 对应不同内容。
- 成功响应只有占位，或只有回填时间；不得提升 coverage。
- 节点/深度/总字节/耗时超限；前端拒绝不能冒充上游调用次数已经受限。
- 第三方隐私、伪 owner、自带斜杠与卡片指令、嵌套图片/媒体和未知字段。
- 来源撤销、Ledger 先落盘后处理失败、旧备份重放、迟到返回与旧 epoch；不得恢复原文或重试。

每一新增正文范围都需要独立实际签收；P11 的 6/6 手机回复继续只属于其原 ID/元数据
窗口，不能重复记为全文验收。本研究不阻止在另行获准范围内继续普通文字或单图测试。
