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

## 阶段 3：API 重启不丢 QQ 输入（需设计评审）

现状：Bridge 在持久化 capture 之前先向 Core 检查授权（`companion_onebot_bridge/capture.py`
的 `runtime.check`），并丢弃超过约 30 秒的帧（`capture.py` 的 `MAX_AGE_SECONDS`，
`owner_runtime.py` 中同样的 30 秒检查）。`app.py` 的接收循环只捕获 WebSocket 与 OS 错误，
Core 不可达时 `httpx.HTTPError` 会让接收循环退出。

方向：Core 不可达时 Bridge 先把帧写入本地 spool，Core 恢复后再检查授权并提交；接收循环
捕获 `httpx.HTTPError` 并退避重试。这会改变授权检查的时序和帧时效规则，属于新机制，
实现前需要单独讨论。

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

**5c：发布脚本**

`scripts/release_plan.py --rolling` 增加 B 类判定（只有 Bridge 镜像变化、Bridge 配置和
spool schema 不变、授权 policy 仍然有效）。执行脚本 `scripts/rolling_bridge_release.py`：

1. 确认授权 ACTIVE；Core 中没有 LEASED/SENDING 投递，或等它们结束（最多 60 秒）；
2. `docker stop --time 90`，要求退出码 0、`instance.lock` 已释放、Bridge journal 中没有未完成的发送；
3. 用只改镜像的覆盖文件启动新容器，等待：`OwnerRuntime.verify` 通过、`runtime.probe`
   返回 `connection_ready`、日志出现 `automatic_source_connected`，再观察 20 秒没有重启；
4. 核对环境变量、挂载、用户、命令与旧容器一致；
5. 任一步失败：用旧镜像重新启动；旧镜像也起不来时，转为正常的 STOP 发布。

### 预计停顿

旧进程等当前发送完成需要 0–5 秒，新进程启动约 2 秒（`OwnerRuntime` 初始化加首次 probe）。
本次 journal 校验优化后，每次写入后的全量校验从约 1.3 秒降到约 0.25 秒（生产数据副本实测）。期间 Core 照常生成回复，
回复留在 PENDING，新 Bridge 起来后立即发出，主人看到的是一次 5–10 秒的延迟。

### 未解决

- 戳一戳、撤回通知在停顿期间丢失时是否需要补收（`get_friend_msg_history` 不含通知）；
- 资料探测（profile probe）每分钟写两条 journal 记录，一周约 7000 条。本次已让它们的校验只做一次，
  但 journal 仍在增长；是否改为只在资料变化时记录，另行讨论。

## 始终需要 STOP 的变更

- 付费合同或能力配置变化（快照或能力哈希改变）；
- 授权修订（续期、purpose 变化）；
- 需要重建表的迁移；
- carry 与备份操作（它们本来就要求持有全部 worker 锁或写入方全部停止）。
