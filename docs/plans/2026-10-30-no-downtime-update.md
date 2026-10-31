# 无停机更新计划

日期：2026-10-30。基线：`d7641f8`。

现在每次发布都按 [`scripts/release_plan.py`](../../scripts/release_plan.py) 的 A/B/C 步骤
执行 OwnerAuthority STOP，停掉全部 worker、Bridge 和 Reader，再换镜像、activate、handoff。
本计划把能安全缩短停机的部分分阶段拆开，每阶段单独评审和验收。

## 阶段 1：worker 平滑退出（本 PR）

问题：Core 镜像以 Python 为 PID 1 直接运行（`Dockerfile` 的 ENTRYPOINT），`compose.yaml`
没有 `init:`，worker 也没有 SIGTERM 处理。Linux 上没有处理器的 PID 1 会忽略 SIGTERM，
`docker stop` 等满宽限期后 SIGKILL。正在进行的模型调用在租约到期后变成 UNKNOWN，
一直占用付费并发额度（生产并发为 1 时即唯一执行位）直到操作员关闭，也会阻止恢复。

改动：

- [`companion/shutdown.py`](../../companion/shutdown.py)：SIGTERM/SIGINT 只设置进程级停止标志。
  第二次 SIGINT 仍抛 `KeyboardInterrupt`，便于本地强制退出。
- `worker`、`life-worker`、`content-worker`、`executive-worker` 只在两个单元之间检查标志：
  正在运行的单元（模型调用、发送、提交）照常完成，之后不再领取新单元，释放文件锁，退出码 0。
- `Wakeup.wait` 在停止请求后返回 `"shutdown"`；空闲等待中的 watcher 每 50 ms 检查一次标志。
- `compose.yaml` 的 Core 服务加 `init: true`；worker 的 `stop_grace_period` 设为 150s，
  大于默认 `lease_seconds`（120）。`lease_seconds` 已被校验为大于单个单元的模型请求跨度。
  `deploy/compose.dev.yaml` 叠加在 `compose.yaml` 上，自动继承这两项。
- API（uvicorn）本身已处理 SIGTERM，未改。

效果：重启不再制造 UNKNOWN。发布步骤本身不变，仍执行 STOP。

## 阶段 2：按 worker 角色滚动重启

每次只重启一个 worker 角色，先停后启（`worker_lock.single_worker` 的文件锁不允许新旧进程重叠）。
API、Bridge、Reader 和 UI 不动，授权不做 STOP/ACTIVE 循环。

适用条件，由 `scripts/release_plan.py --rolling` 判定，结果为 R 类：

- 只有 Core 镜像变化；Bridge、Reader 或 UI 也要换时仍按 A/B/C；
- 不需要迁移（新镜像在数据副本上的 schema 与当前一致）；
- 已存的 OwnerAuthority policy 在新代码和当前配置下仍然有效，即付费合同快照和能力哈希都没变。

分类不确定、预检失败或探测没跑时都不会得到 R。

执行：`scripts/rolling_worker_release.py`，按 content、executive、life、chat worker 的顺序逐个处理。

1. 读库确认授权仍是 ACTIVE，记下 UNKNOWN 模型调用数；
2. `docker stop --time 150`：worker 做完当前单元后退出，退出码必须是 0，文件锁必须已释放，
   UNKNOWN 数不能增加；
3. 用原 Compose 文件加一个只改镜像的覆盖文件启动该服务，等新容器运行、重新持有同样的锁，
   再观察 20 秒没有重启，并核对环境变量、挂载、用户、命令和重启策略与旧容器完全一致；
4. 任何一步失败就停止，用旧镜像重新启动这个服务，后面的服务不再处理。

覆盖文件和回执保存在 `--output` 目录；之后的 STOP 发布从容器标签解析 Compose 文件，会带上它。

已知限制：

- API 继续运行旧代码，API 上的修改要等下一次 STOP 发布才生效。滚动期间 API 与 worker 的
  代码版本不同，但 schema、授权和付费合同相同。
- worker 停止期间对应的任务暂停，等新进程起来后继续，聊天会延迟几秒到几十秒。
- 生产容器若由旧的 Compose 渲染结果创建，没有 `init: true` 和 150s 宽限期；脚本显式传
  `--time 150`，worker 自己处理 SIGTERM，不依赖这两项。
- 首次用于生产前没有在独立环境演练过整套 Docker 操作，第一次使用要有人在场观察。

## 阶段 3：API 重启不丢 QQ 输入（已实现）

原来 Bridge 在持久化 capture 之前先向 Core 检查授权（`capture.py` 的 `runtime.check`），
Core 不可达时 `httpx.HTTPError` 会让接收循环退出，主人在 API 重启期间发的消息只能靠
30 秒内的历史补收找回。

现在 [`companion_onebot_bridge/held_inputs.py`](../../companion_onebot_bridge/held_inputs.py)
把这类消息先暂存：

- 只有传输错误和 5xx 会暂存，4xx（授权被拒）照旧让 Bridge 停止。暂存时只做本地检查
  （主人私聊、30 秒收到时限），不签名、不进 inbox、不读引用和媒体，什么都不发给 Core。
- `held_inputs` 表保存原始帧、真实收到时间和对端；Bridge journal 只记哈希（`INPUT_HELD`）。
  有暂存时，新消息排在后面一起暂存，保持主人的发送顺序。
- Core 恢复后，上传循环按顺序处理：先重新检查授权和撤回，再走原来的 `prepare_owner_input`
  和 `persist_capture`，用原始收到时间签名。Core 照常检查 30 秒收到时限、授权生效时间、
  policy 到期、撤回、擦除和保留期。
- 每条暂存以一条 `INPUT_HOLD_CLOSED` 结束：`PROMOTED`（正式入库）、`DUPLICATE`（已由实时或
  补收入库）或 `REJECTED` 加原因（`HOLD_EXPIRED` 超过 10 分钟、`RECALLED`、`CORE_HTTP_403`、
  `OWNER_STOPPED` 等）。结束时删除原文。
- OWNER STOP 会把仍在暂存的消息记为 `REJECTED/OWNER_STOPPED`，所以停写发布前要确认预检中
  暂存输入为 0。
- 戳一戳在 Core 不可达时记一条 `owner_poke` 日志后丢弃，不再让 Bridge 退出；它没有历史可补。
- Bridge 启动时仍要求 Core 可达（`runtime.probe`）。

旧 Bridge 镜像不认识 `INPUT_HELD`，一旦产生过暂存记录就不能再回退到旧镜像：它会以
`OWNER_JOURNAL_RECONCILIATION_REQUIRED` 拒绝启动，不会写坏 journal。

## 阶段 4：在单元边界重新加载控制台配置

现在 `operator_config` 只在启动时由 `Settings.load` 应用。对不属于付费快照、也不进入能力哈希的
控制台字段，可以在 worker 的单元边界读取新版本并替换设置，省去为改配置而重启。
需要先列出可热加载的字段清单。

## 阶段 5：Bridge 单独重启（设计稿，需评审）

目标：只换 Bridge 镜像时不做 OwnerAuthority STOP/ACTIVE，Core、worker、Reader 照常运行，
Bridge 停顿控制在 10 秒以内，且不丢主人消息、不产生 UNKNOWN 发送。

### 现状和问题

1. **SIGTERM 会打断发送。** `app.run` 收到 SIGTERM 后设置 `stop`，随即取消全部任务
   （`app.py` 的 `for task in tasks: task.cancel()`）。如果投递任务正处在 `IO_STARTED`
   与 `ACK` 之间，这次发送就停在 `IO_STARTED`。新进程启动时 `OwnerRuntime.__init__`
   看到 `RESERVED/IO_STARTED/UNKNOWN` 会直接 `stop()`，之后只能走完整的 reconcile 和
   handoff 发布。
2. **停顿期间收不到消息。** Bridge 通过正向 WebSocket 从 SnowLuma 接收消息。Bridge 断开期间
   SnowLuma 是否缓存事件还没有确认；如果不缓存，这几秒内主人发的消息会丢。
   Bridge 只接受 30 秒内的帧（`capture.py` 的 `MAX_AGE_SECONDS`）。
3. **新旧进程不能重叠。** `spool` 的 `instance.lock` 是进程级文件锁，owner journal 只能有一个写入方，
   所以只能先停后启，不能蓝绿切换。

### 方案

**5a：平滑退出（已实现，[#225](https://github.com/yanyuhanyue/companion-core/pull/225)）**

- SIGTERM 只设置停止标志，不再立刻取消任务（`app.run` 改为 `drain`）。各循环本来就在
  两个单元之间检查标志：
  - 投递：当前 `deliver_tick` 走完（发送、`spool.finish_delivery`、`flush_result`、`ACK`
    写入 journal），不再领取新投递；分段回复中途停止时，剩下的气泡留在 Core 的 PENDING，
    由新进程继续发；
  - 接收：停止时关闭 WebSocket（`close_on_stop`），正在持久化的那一帧处理完后循环结束；
  - 上传、媒体、表情包和资料探测：当前单元完成后退出；
  - 60 秒（`SHUTDOWN_GRACE_SECONDS`）后仍未结束的任务才取消，日志输出
    `bridge_shutdown` 的 `CLEAN` 或 `CANCELLED`。
- Bridge 由发布脚本渲染的 compose 启动，不在仓库的 `compose.yaml` 里，所以停止时要由
  发布脚本传 `docker stop --time 90`。
- 未做：退出前的 `window_attempts`/inbox 自检和非 0 退出码。新进程启动时
  `OwnerRuntime.__init__` 已经会对未完成的发送执行 STOP，5c 的脚本再按 journal 判定。

**5b：补收停顿期间的消息（已评审，已实现，开关 `history_backfill` 默认关闭）**

已确认 SnowLuma 2.0.3 不缓存：正向 WebSocket 的 `onEvent` 在没有客户端连接时直接返回
（`if (!this.acceptingActions || this.connections.size === 0) return;`），停顿期间
主人发的消息会丢。SnowLuma 支持 `get_friend_msg_history`，所以方案是：

- 新进程连上 WebSocket 后，用 `get_friend_msg_history` 读取主人会话中最后一条
  已捕获消息之后、30 秒以内的消息，按原有的 `normalize` 和 `persist_capture` 处理，
  capture 记录写 `kind: onebot_history_backfill`，与实时的 `authenticated_onebot_ws` 区分。
- 这会新增一个 OneBot 读接口和一种输入来源，Core 侧的来源校验也要放行这一类。
  补收的消息按 `source_event_id` 去重，不会和实时消息重复入库。
- 戳一戳和撤回是通知，不在消息历史里，停顿期间的这两类仍会丢。
- 实现：`companion_onebot_bridge/backfill.py`。每次连上 WebSocket 后读取最近 20 条，
  只补主人发的、比最后一次 capture 新、在 30 秒内、并且晚于当前授权绑定时间的消息
  （Core 拒收授权生效前的 capture，上传被拒会让 Bridge 停止）。读取失败只记日志，
  不影响实时接收。Bridge 配置 `history_backfill = true` 才启用，随下一次停机发布写入。

**5c：发布脚本（已实现）**

沿用 `EDGE` 类，不新增类别。`scripts/release_plan.py` 对只换 Bridge 的发布另外要求：
Spool 中授权 ACTIVE 且没有 STOP 标记、没有 `RESERVED/IO_STARTED/UNKNOWN` 发送、新 Bridge 镜像与
运行中 Core 镜像的 `companion_delivery_protocol` 摘要相同；任一项不满足即 `NOT_READY`。
执行脚本 [`scripts/edge_release.py`](../../scripts/edge_release.py)：

1. 重读 Spool，确认授权 ACTIVE、没有未结发送；
2. `docker stop --time 90`，要求退出码 0（退出报告发现未结发送时为 3）、`instance.lock` 已释放、
   Spool 中仍没有未结发送；
3. 用只改镜像的覆盖文件启动新容器，等待它持有 `instance.lock`、日志出现 `ws_connected`，
   再观察 20 秒没有重启，核对环境变量、挂载、用户、命令与旧容器一致；
4. 任一步失败：用旧镜像重新启动并停止；旧镜像也起不来时回执写 `STOP_RELEASE_REQUIRED`，
   转为正常的 STOP 发布。

Reader 和 UI 走同一脚本，只检查退出、健康检查和配置一致。

**5d：补收覆盖报告（已实现）**

`backfill` 每次输出 `owner_backfill`：读到的条数和时间范围、上次 capture 时间、覆盖判断
（读满 20 条且最早一条仍晚于上次 capture 时为 `PARTIAL`，可能漏掉更早的消息）和按原因计数的
跳过项（`OLDER_THAN_CAPTURE_BOUND` 是平台保留了但超过 30 秒收到时限的消息，`BEHIND_HELD_INPUTS`
表示有暂存消息尚未入库）。`notices_recoverable` 恒为 false：戳一戳和撤回通知不在消息历史里。

### 预计停顿

旧进程等当前发送完成需要 0–5 秒，新进程启动约 2 秒（`OwnerRuntime` 初始化加首次 probe）。
本次 journal 校验优化后，每次写入后的全量校验从约 1.3 秒降到约 0.25 秒（生产数据副本实测）。期间 Core 照常生成回复，
回复留在 PENDING，新 Bridge 起来后立即发出，主人看到的是一次 5–10 秒的延迟。

### 未解决

- 戳一戳、撤回通知在停顿期间丢失时无法补收（`get_friend_msg_history` 不含通知），只在补收报告中说明；
- 资料探测（profile probe）每分钟写两条 journal 记录，一周约 7000 条。本次已让它们的校验只做一次，
  但 journal 仍在增长；是否改为只在资料变化时记录，另行讨论。

## 隔离演练（2026-10-31）

在部署主机上用独立 Compose project `companion-rehearsal`（独立网络、数据目录和合成身份，
不接触 `companion-main` 与真实 QQ）运行 API、chat worker、Bridge，以及模拟 SnowLuma 和
HTTPS 模型的 fake 服务。起点是生产正在用的 2026.10.30 镜像，再升级到本分支镜像。

| 情形 | 结果 |
| --- | --- |
| Bridge EDGE 升级，每 2 秒一条主人消息 | 停止到就绪 4.4 秒；35 条全部入库，其中停顿期间的 2 条由补收找回 |
| API 停止 21 秒，持续输入 | Bridge 没有退出；16 条暂存，Core 恢复后全部 `PROMOTED`，25 条全部入库 |
| SnowLuma 断开 8 秒并重复推送 | 补收找回断开期间的消息，覆盖 `FULL`；重复帧 `duplicate_suppressed`；20 条全部入库 |
| R 滚动 worker，同时请求分段回复 | worker 0.7 秒退出（退出码 0），无新增 UNKNOWN；分段照常发送 |
| R 换成无法启动的镜像 | 90 秒内未拿到锁，回到上一镜像，后续回复正常 |
| Bridge 回退到旧镜像（已有暂存记录） | 旧镜像以 `OWNER_JOURNAL_RECONCILIATION_REQUIRED` 拒绝启动，脚本换回新镜像 |
| 发送卡住后停止 Bridge | 发送超时记为 UNKNOWN，退出码 3，报告 `unfinished_sends: {UNKNOWN: 1}`；重启后拒绝运行，没有重发；预检 `NOT_READY` |
| 需要迁移的 Core 镜像加 `--rolling` | 判为 B，滚动脚本拒绝 B 类计划 |
| `--policy-change` | 判为 C |

演练中发现并已修复：

- 主线 Bridge 镜像缺少 `schedule` 依赖，`scheduled_import` 导入失败，新 Bridge 无法启动；
  已加入 `deploy/onebot-bridge.lock`。调度循环原来不响应停止，最长拖满 60 秒宽限；
- API 容器停止后其服务名没有 DNS 记录，`socket.gaierror` 绕过了 httpx 的传输错误分类，
  使发送循环崩溃、接收循环把正在处理的帧当作断线丢弃；现在归为 `httpx.ConnectError`；
- 预检和滚动脚本拿镜像标签与容器的镜像 ID 比较，永远等不到就绪；现在先解析为 ID；
- 一次 R 发布后 worker 与 API 镜像不同，下一次预检找不到 worker；现在 Core 组总是包含 worker。

未覆盖：Reader 和 UI 没有参与演练；配置漂移导致 policy 失效（C）只有单元测试，演练中被
上面那条 UNKNOWN 发送先行阻断。

## 始终需要 STOP 的变更

- 付费合同或能力配置变化（快照或能力哈希改变）；
- 授权修订（续期、purpose 变化）；
- 需要重建表的迁移；
- carry 与备份操作（它们本来就要求持有全部 worker 锁或写入方全部停止）。
