# P5 Direct QQ Beta

本轮从 `main@7bd47a659f60b4904b87508a3dbf0a4d6934d87d` 开发独立
`companion_onebot_bridge`，Core HTTP v1 保持唯一聊天接口。当前交付为代码、离线验证与部署草案。
真实 SnowLuma 兼容、QQ 0/20、真模型、M1 人审和生产发布仍由
[p5-gates.json](p5-gates.json) 分开记录。

```text
QQ → SnowLuma WS events → native Bridge Spool → Core HTTP → Chat Worker
QQ ← SnowLuma send_private_msg ← Bridge ← Outbox lease/begin-send/result
```

普通聊天没有 DSH、浏览器、Reader 或 Windows 执行步骤。旧 AstrBot 插件保留兼容；新进程不导入
AstrBot 或 Core 运行时。独立容器只安装带 hash 的 HTTP/WS 依赖，不含 SnowLuma 二进制、模型 Key、
Core DB 或登录卷。wheel 的 `onebot` extra 提供 WS 依赖；Core 默认安装不启用该 extra。

## 固定接口

- 首期会话为 `onebot-direct:FriendMessage:<owner>`；`source_event_id` 为
  `<bot>:onebot-direct:FriendMessage:<owner>:epoch:<epoch>:<message_id>`。
  SHA-256 生成稳定 event/idempotency/trace。新 epoch 用 CSPRNG 生成并保存，正常重启/重连不换。
  这个新会话仅供全新测试身份；旧 AstrBot 累积资料迁移必须单独设计映射。
- 仅接收 `message/private/friend`，同时检查 bot、user、sender。群聊、临时会话、自己的回声、
  notice/request/心跳不进入 Core。整数与无前导零十进制字符串等价，布尔/浮点/冲突身份拒绝。
- 原始帧默认 64 KiB、嵌套深度 8、节点 2048、段数 64、正文 20000 字；重复 JSON Key、
  非 Unicode 标量、无效时间（2000 年前或本地未来超过 300 秒）拒绝。
  其他非文字段丢弃其地址和内容；纯附件交给现有 Core 文字能力提示，混合消息明确说明未解析附件。
  `media/shared_content` 槽位保留，首期不开图片下载和来源 Worker。
- Spool 使用 SQLite/WAL/FULL；先提交再上传，同一 ID 的正文/附件指纹冲突立即停止服务。
  Core 严格 202 合同后删除正文，保留 hash 收据，防止重连重放。
  epoch/owner/bot/origin 固定到磁盘，改变即拒绝启动。单实例用 OS 锁，进程退出自动释放。
- 发送目的地只能来自私有配置中的 owner，文本以 OneBot text 数组发送，模型生成的 CQ 不会执行。
  发送前检查实际 bot/online/good 和冻结版本，再取得 Core `SENDING` 确认并提交本地不确定记录。
  只有 `ok/retcode=0/真实非零 message_id` 才回报 SENT；任何开始发送后的失败保留 UNCERTAIN。
  结果上报可恢复；不重复调用 `send_private_msg`。平台接受不表示客户端已读。
- 收件容量与磁盘上限耗尽时停止；已确认 hash 收据不自动淘汰。应监控卷配额并人工维护，
  不清空 Spool 换取恢复。权限要求 Linux 0700 目录/0600 配置，容器 uid 10004。
  Windows 仅作开发验证，真实身份/Secret 目录需另外由操作者限制 NTFS ACL。
- DNS 每次 HTTP 请求及 WS 重连都核对私网地址，并固定实际 TCP 目的地；不跟随重定向、
  不使用环境代理。只允许指定服务名、localhost 或回环/RFC1918/ULA 地址，拒绝链路本地元数据地址。

## 遗忘与恢复

本轮补齐“备份早于被遗忘事件、Bridge 仍有待确认入件”的回放边界。Ledger apply/replay 将事件删除
投影写入既有 `runtime_meta`，包括已 COMPLETE 的旧 Ledger 记录；Core 在入站事务内拒绝这些 event ID。
Bridge 仅将认证的精确 `410 {"error":"EVENT_ERASED"}` 视为终止收据，清除正文并保留去重 hash。
其他拒绝和错误合同不能据此删消息。不增加业务 schema，不推测旧来源 grant 归属，G1 仍为 FAIL。

## 指标与证据

Bridge 输出纯 JSON 阶段记录；Core 通过 `COMPANION_TELEMETRY=1` 显式开启，证据类别由
`COMPANION_TELEMETRY_EVIDENCE=OFFLINE_FIXTURE|LIVE_ONEBOT|PAID_MODEL` 标明。
记录只包含稳定匿名 trace、随机进程 session、单调高精度 `perf_counter_ns`、本地耗时和预算估计，
不含 QQ 号、私聊、URL、Cookie、请求正文或密钥。不要开启 HTTP/WS 库的 debug 日志。

阶段含 receive、Spool commit、Core ack、job start、context ready、model request/done、job done、
Outbox lease、begin-send、OneBot ack、Bridge receive→send ack。job start 耗时为 Core 持久作业年龄
（重试时包括退避），模型耗时只包围实际 complete 调用。不生成未实测首 token。
各进程 monotonic 时间不直接相减；汇总使用各自测得的 elapsed span。同一 trace 可用于关联。
Core 的预算数字是 `model_usage` 上界费率估计，不是账单。缺少值保持 null。

```powershell
uv sync --frozen --extra onebot --group reader
./scripts/check.ps1
python scripts/benchmark_qq_beta.py .local/p5/evidence/offline-100.jsonl `
  --delivery-states .local/p5/evidence/offline-delivery-states.json `
  --evidence-type OFFLINE_FIXTURE --output .local/p5/evidence/offline-summary.json
```

汇总只处理脱敏数据，不发送 QQ，不会把 LIVE_ONEBOT 或离线耗时升级为真人客户端往返。
P50/P95 使用 nearest rank；100 个合成样本仅量测本机 fsync/ASGI/Mock 路径，不能证明 VPS、
SnowLuma、腾讯网络或真模型延迟。人工 QQ 时间、评分和 20 场景单独记录在
[acceptance.md](acceptance.md)。操作、许可和回滚见 [operations.md](operations.md)。

阶段日志可能在崩溃时丢失；`observed_uncertain_stage_count` 只计观察到的事件。
完整未决数量必须配套同轮 Core `outbox GROUP BY state` 的匿名状态计数快照，
未提供时 `unknown_delivery_count=null`。这些计数是该时刻状态，不是历史累计失败率。
