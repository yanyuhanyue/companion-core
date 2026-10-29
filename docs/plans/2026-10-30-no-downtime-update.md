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
只适用于同时满足以下条件的发布：

- 迁移只做增量（加表、加列、加索引），旧代码在新 schema 上仍正确；
- 不改变付费合同快照（`paid_budget.snapshot`）和 `owner_authority.capability_hashes`。

满足时已激活的授权仍然有效，不需要 STOP/ACTIVE 循环。需要在 `release_plan.py` 中增加
发布分类：比较新旧镜像在数据副本上的快照与能力哈希，并识别迁移是否为增量。分类不确定时按 A/B/C 处理。

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

## 始终需要 STOP 的变更

- 付费合同或能力配置变化（快照或能力哈希改变）；
- 授权修订（续期、purpose 变化）；
- 需要重建表的迁移；
- carry 与备份操作（它们本来就要求持有全部 worker 锁或写入方全部停止）。
