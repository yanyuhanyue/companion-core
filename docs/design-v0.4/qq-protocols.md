# QQ 协议后端切换与回滚

当前默认继续使用 NapCat。SnowLuma 通过同一个 AstrBot `aiocqhttp` / OneBot v11 适配器接入，Core 不导入任何协议端 SDK。既有 Bridge 就是协议差异的边界；只有实际出现不兼容行为时才添加专用适配。

SnowLuma 官方明确支持 AstrBot 反向 WebSocket，示例地址为 `ws://astrbot:6199/ws`，实际以 AstrBot 服务名和适配器配置为准：[接入文档](https://snowluma.github.io/zh/docs/guide/connect-bot)。这是可替换接入路径，不是热切换或消息补发保证。

## 身份、会话与去重

- 同一机器人 QQ 同时只连接一个主协议端。保留 owner/bot QQ ID、AstrBot 平台实例 ID 和 `unified_msg_origin`。
- Core 数据库、Memory、日记、预算和独立遗忘日志均属于 Core，不挂入协议端容器。
- Bridge 新增 `protocol_backend`（napcat/snowluma）与 `protocol_epoch`。首次沿用 NapCat 可保留空批次号，旧去重键完全兼容。
- 每次切换、回滚或需要隔离消息 ID 的重新接入，都生成新的 8–64 位批次号。会话键不变；事件来源 ID 增加批次号，避免两个协议端同名消息 ID 冲突。
- Bridge 本地 SQLite 记录已用批次。非空待上传队列会阻止切换，旧批次不能复用，重启不会丢失约束。修改配置后重启 Bridge 才生效。
- 批次号不是跨协议消息对照表：同一历史消息若被不同协议端重新上报，不能保证自动识别为同一条。切换时停旧端、排空队列、记录时间窗口，并检查新端是否补发历史消息。不要为了消除重复而按正文粗暴合并两条真实消息。

## 切换操作合同

1. 备份 AstrBot/Bridge 数据、Core 加密快照和最新独立遗忘日志，另外保存 NapCat 配置与 QQ 登录卷；记录镜像摘要、协议版本、帐号、平台实例和当前连接地址。
2. 使用独立容器、独立 QQ/配置数据卷准备 SnowLuma。此阶段不连接同一 QQ 主账号，不覆盖 NapCat 的卷。
3. 在维护窗口停止旧协议端接入。排空 Bridge 待上传队列，等待 Core 待处理聊天与发送收敛；所有 `SENDING / DELIVERY_UNCERTAIN` 必须核对，不能自动重放。然后停止 Bridge。
4. 为 SnowLuma 登录同一机器人账号，配置同一 AstrBot 实例的反向 WebSocket 和独立 OneBot token。
5. 在 Bridge 设置 `protocol_backend=snowluma` 和新的 `protocol_epoch`，保留 Core URL、owner/bot IDs、平台实例以及 Bridge 数据目录，再启动 Bridge。
6. 执行下表实机验收。验证前以标准私聊文字为主，保留原后端与卷以便回滚。
7. 回滚时先停 SnowLuma，排空/核对队列，恢复 NapCat 原卷，给 Bridge 使用又一个新的批次号。Core 数据库和遗忘日志继续沿用最新状态，不回退到旧副本覆盖新记忆。

以上是待部署环境执行的合同，本轮未切换任何运行中的 QQ 后端。

## 兼容检查

| 项目 | 本轮自动检查 | 实机门禁 |
|---|---|---|
| 主人私聊与同一会话 | 两种后端的标准事件夹具通过 Core | 比较 `unified_msg_origin`，完成双向私聊 |
| 消息 ID、去重和回滚 | 跨批次同名 ID 不冲突；同批次重放不重复入库 | 观察重连和历史补发行为 |
| 回执不确定 | 保留原发送前提交/不自动重发合同 | 核对实际 QQ 到达和断线回执 |
| 主动分享 | 继续走同一标准私聊发送路径 | 用户授权的实际主动消息测试 |
| 图片、语音、引用、JSON 卡片 | 附件内容不下载、不误当作已理解 | 仅验证协议/AstrBot层；Core 本轮仍为文字能力 |
| 空间、点赞、评论 | Core 持续拒绝公开执行 | 未来独立 API 与不可变草稿审批，不假定扩展兼容 |

`tests/test_protocol_backends.py` 是合成的标准 AstrBot 事件合同测试，不是两款客户端的实际兼容性认证。

可选只读预检（不发送消息、不获取聊天记录）：

```powershell
$env:ONEBOT_ACCESS_TOKEN = '从当前协议端安全取得的令牌'
uv run python scripts/probe_onebot.py --url http://127.0.0.1:3000 --bot-id 你的机器人QQ
```

它只调用 `get_login_info/get_status/get_version_info/can_send_image/can_send_record`。在线和能力声明不代表消息送达已验收；报告会保留 `private_message_delivery_verified=false`。

## 部署隔离与许可

SnowLuma 官方 Docker 指引要求 `SYS_PTRACE` 和 `seccomp=unconfined`。这些权限只属于 QQ 协议端容器，不传给 Core，不挂载 Core 数据或模型密钥。管理界面/noVNC 绑定回环，通过安全隧道访问；OneBot 使用私有网络和独立 token，不直接发布到公网。[官方 Docker 文档](https://snowluma.github.io/zh/docs/guide/deploy/docker)

部署时选择并记录已验证的明确版本/摘要，重新核对官方镜像、QQ 版本与许可。本项目没有复制、修改或再分发 SnowLuma 源码/二进制；其源码可见非商业许可及原生组件条款见 [官方 LICENSE](https://github.com/SnowLuma/SnowLuma/blob/main/LICENSE)。在同一 VPS 更换协议端不会改变公网出口 IP。
