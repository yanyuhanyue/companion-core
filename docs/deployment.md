# 部署与运行

## 先准备

需要 Linux VPS、Docker Compose、可用的 OneBot v11 连接、主人 QQ、机器人 QQ，以及模型 API Key。代码默认单主人、私聊、文字消息。不要将数据库放在 NFS、SMB 或 Tailscale 文件共享目录。

旧宿主兼容性核查对象为 AstrBot 4.29.1，源码提交 `e5e21b71aec5642f110eb115eb8e9a0d8028b7e5`。当前主人消息统一使用原生 OneBot Bridge；AstrBot 插件仅用于旧队列与投递回执核对。固定已验收的协议后端版本，避免自动升级。

2026-10-19 的运行版本记录为 AstrBot 4.29.1 / NapCat 4.18.38；镜像固定方式和 NapCat 构建步骤见 [运行版本说明](../deploy/README.md)。

## 启动核心容器

```bash
cp config.example.toml config.toml
cp .env.example .env
# 编辑 .env，填写 QQ 账号、随机 bridge token 和模型 Key。
# 编辑 config.toml，核对模型、费率与预算。先保持 memory_enabled=false。
chmod 600 .env
# config.toml 只读挂入以 UID/GID 10001 运行的 Core，须由该身份可读。
sudo chown 10001:10001 config.toml
sudo chmod 600 config.toml
docker compose up -d --build
docker compose exec companion-api /app/.venv/bin/python -m companion status
```

Compose 默认启动一次性迁移容器、API 和聊天 Worker。日记、生活、工具、内容 Worker 分别通过 `--profile diary`、`--profile life`、`--profile tools`、`--profile content` 启用；对应功能还需配置开关。数据库和遗忘日志分别放在 `core-data`、`erasure-ledger` 卷中。API **没有宿主机端口映射**，通过 Docker 网络 `companion-internal` 访问；网络保留模型 API 所需的出网能力。不得给 8765 添加公网端口或公网反向代理。容器以非 root 用户运行、只读根文件系统、删除 Linux capabilities。

`.env` 由宿主 Compose 读取，保留宿主部署用户或 root 所有；不要将它交给容器用户。
后续编辑替换 `config.toml` 时重新核对其 UID/GID 与 `0600` 权限。
可选媒体节点和解析器的部署边界见[私聊媒体与 QQ 账号自主能力](qq-beta/media-autonomy.md)。

现有 AstrBot 容器接入相同网络：

```bash
docker network connect companion-internal <你的AstrBot容器名>
```

网络接入也应写进现有 AstrBot 的 Compose 配置，以免重建后失效。若 AstrBot 运行在宿主机，可采用本机受控网络接入；不要为方便接入而暴露公网端口。

## 主人入口与旧 AstrBot 数据迁移

新入站统一由 `companion_onebot_bridge` 接收。基础文字使用原生 `daemon`；图片、引用、文件、
语音及连续交互使用已有签名捕获和受控 `window-run` 入口，配置和授权见
[QQ 输入理解](qq-input-understanding.md) 与 [媒体运行配置](qq-beta/media-autonomy.md)。
迁移不会创建媒体签名密钥、授权、预算或发送窗口；仅运行 `daemon` 不会因此获得媒体理解能力。

AstrBot 插件 v0.6.0 删除 ImageQueue、旧图片下载/上传以及宿主新发送入口。
主人事件仍 `stop_event()`，阻止旧宿主默认 Agent 回答，但不写入新 inbox。
`drain_existing=false` 为默认值，不连接 Core；显式启用时只重报已有投递回执、上传原 inbox，
不领取 Outbox、不下载图片、不运行 Reader。先停止旧版本宿主及其主人自动回复，再升级核对插件
或运行下述 CLI。其他机器上的旧版本不使用新的锁，必须由部署操作者停止，不能靠配置文件证明已停机。

```powershell
uv run python scripts/package_bridge.py
```

核对插件包为 `dist/astrbot_plugin_companion_bridge-v0.6.0.zip`，包含原生共享回执实现，不依赖
Core 包。保留旧 `core_url/owner_qq_id/bot_qq_id/bridge_token/protocol_backend/protocol_epoch`
和原数据目录。旧 Reader 启用字段先按下节迁移；不要删除原 `inbox.sqlite3` 或锁文件。

迁移 CLI 从源码仓库运行，输入私密的原插件 JSON、已经配置好的原生 TOML 和旧 spool 的绝对路径。
原生配置必须沿用 owner、bot、Core 地址及 bridge token，并使用全新的 protocol epoch；
OneBot 地址和令牌由已配置的原生连接提供。选择一个**未来**的切换 Unix 秒，在该秒前停好旧宿主。

```powershell
uv run python -m companion_onebot_bridge.migrate_astrbot prepare --plugin-config privacy/astrbot.json --native-config privacy/native-input.toml --spool /absolute/private/astrbot/inbox.sqlite3 --output privacy/native-cutover --cutover-after <planned-unix-second>
```

输出目录必须不存在。`native.toml` 保留原生设置，默认 `delivery_enabled=false`，绑定旧 spool 路径、
交接摘要和切换时间；`astrbot-config.json` 关闭旧核对循环；`handoff.json` 是不含凭据的本地库存与
能力缺口报告。POSIX 目录 0700、文件 0600，Windows 另用 NTFS ACL 限制访问。prepare 不启动服务、
不连接网络，允许在尚有旧队列时准备；它仅升级旧 spool 的核对表，不删除原正文或回执。

停好旧宿主后，可用原凭据核对 Core 中的旧任务。下述 `drain` 只调用 Core 的原 HTTP inbox/result：

```powershell
uv run python -m companion_onebot_bridge.migrate_astrbot drain --plugin-config privacy/astrbot.json --spool /absolute/private/astrbot/inbox.sqlite3
uv run python -m companion_onebot_bridge.migrate_astrbot seal --bundle privacy/native-cutover --host-stopped
```

`drain` 保留原幂等键和来源；严格验证 202 或 `EVENT_ERASED` 后才清除已核对正文，同时保存
不含正文的 `retired_inbox` 回执。拒绝项仍保留正文供诊断；发送未知结果仍为 `DELIVERY_UNCERTAIN`，
报告失败可以重报但不能重新发送。seal 要求本地 pending inbox 和 pending result 均为空，
在原 SQLite 事务中封存交接。成功后的旧 spool 禁止追加和上传新入件。重复 seal 核对同一记录。

达到切换时间后，才能用生成的配置启动原生进程。原生进程同时持有自身单实例锁和旧 spool 的锁，
并核对交接摘要、原生身份及空 pending 状态；换摘要、删交接字段或并发旧核对进程均不能接管已有原生 spool。
来源时间小于等于切换秒的帧不进入新来源，重连/重启仍使用同一新 epoch。手动 `capture_once` 收到此类帧会按原规则停止窗口；核对原因后重新准备窗口，不删除 STOP 标记绕过门禁。请在切换秒之后发送新消息；
这一秒的消息不会被悄悄改造成新来源。迁移后保留旧 spool 及其锁所在目录，原生用户须能读取数据库并写锁。
容器部署要把该目录挂载到配置指定的绝对路径；锁仅适用于同一主机本地文件系统。

```powershell
# 仅基础文字；保持原 Core 门禁和生成配置中的 delivery_enabled=false。
uv run python -m companion_onebot_bridge --config privacy/native-cutover/native.toml daemon
# 媒体入口沿用既有受控计划，计划需按最终配置摘要重新准备和授权。
uv run python -m companion_onebot_bridge --config privacy/native-cutover/native.toml window-run --window privacy/window.json --inventory privacy/inventory.json --authorization privacy/authorization.json
```

这些是操作步骤，不表示已执行上线或真人验收。新 OwnerAuthority 的共享 DTO/final-fence 由集成流程
单独接入；本迁移不添加任何绕开原生门禁的发送或上传路径。

### 旧媒体与会话的实际边界

- 原 ImageQueue 的签名 URL 只在内存中，停机后无法恢复。pending inbox 的图片槽位会随原事件核对，
  但不会重新下载；Core 的 WAITING/FETCHING/RUNNING/UNKNOWN 仍由原期限和恢复逻辑处理。
  `drain` 输出已知图片事件 ID，供检查 `/internal/v2/content/{event_id}`；只检查状态，不能重领 UNKNOWN。
- 旧版本已删除的 accepted inbox 没有完整事件索引，无法从空 spool 证明旧 Core 媒体全部终态。
  本地 seal 只证明本地队列已核对。切换前仍须检查 Core 的内容任务与旧 Outbox；不把它显示为全链路已完成。
- 新入口使用 `onebot-direct:FriendMessage:<owner>`；不改写旧来源、签名或 Outbox 目的地。
  旧来源待发送任务不能由新来源冒领；原生目的地检查会拒绝。旧会话自动衔接、历史待发送的跨来源迁移
  不在本片提供，须由 Core 原核对/取消流程处理后再启用发送。账本、证据、擦除与 UNKNOWN 保留。
- 新媒体仍使用原 `MediaPump`：签名原件清单、来源撤回/新输入抢占、下载租约、上传凭证和持久恢复状态。
  已确认上传只读回执；不确定上传不重新执行效果。引用/转发嵌套能力以当前原生捕获合同为准。

## 将旧内置 Reader 迁出 AstrBot

此版本删除插件内的 Bilibili/Heybox Reader poller。先停止旧插件，私密导出其配置对象为 JSON，
再在仓库内运行以下迁移命令（输出目录必须尚不存在）：

```powershell
uv run python -m source_reader.migrate_astrbot --config privacy/astrbot-config.json --output privacy/readers --network companion-internal
```

命令不连接 QQ、Core 或来源站点，不修改原配置，也不启动服务。它生成：

- `compose.json`：复用 `deploy/source-reader.Dockerfile` 与独立 Reader CLI，沿用原 Core origin 和 adapter ID。
- 每来源一个私密 `.env`：仅携带该 Reader 的原令牌；配置过 Heybox 来源服务时保留独立来源令牌。
- `astrbot-config.json`：保留其他插件设置，移除旧 Reader 字段，供回填 AstrBot 插件配置。

使用 Docker Compose 2.30 及以上版本；`env_file.format: raw` 保留令牌内 `$`、引号和等号的原值。
输出目录应位于私密位置；POSIX 系统创建为目录 0700、文件 0600。不要将配置或令牌提交到 Git。
Core 中的 observation adapter ID/source/token 必须与原配置一致，迁移命令不创建新授权或扩大限额。
默认的 `http://companion-api:8765` 只用于已配置的私有容器网络；其他明文地址仍需是 localhost 或私网 IP。

```powershell
docker compose -f privacy/readers/compose.json build
docker compose -f privacy/readers/compose.json up -d
```

确认独立 Reader 在原队列完成一次受控读取后，把生成的插件配置回填并安装本版本插件。
不要同时运行旧内置 poller。新插件发现旧启用字段会在初始化前报告迁移要求，不会默默丢弃读取能力。
在途租约仍由 Core 保存；失去结果 ACK 时只依赖原租约恢复，不能删除队列或直接重新执行外部读取。
既有 `/internal/v2/readers/{id}/lease`、`validate`、`result` 合同不变；来源证据和擦除继续由 Core 维护。

迁移只保留原公开读取能力。小黑盒未配置来源服务时，生成的 `--public-discovery` 明确启用旧插件的
公开首页标题/链接提取；默认独立 Reader 不受影响。仅提取标题的结果不能作为已读正文，SPA 空壳仍失败。
不会启用账号授权、浏览器、评论、视频媒体或额外预算。Bilibili 元数据读取仍受原请求数约束；不足以取得字幕时明确标注层级。

Reader 部署依据为现有独立 Reader 入口及 [Compose env_file 的 raw 格式](https://docs.docker.com/reference/compose-file/services/#env_file)。

## 开启记忆

先在真实 QQ 上测试正常消息、重复消息和故障恢复，确认仅一条主回复，再将 `memory_enabled=true`。停止 API/Worker 后运行迁移初始化，再启动服务：

```bash
docker compose --profile life stop companion-worker companion-life-worker companion-api
docker compose build
docker compose run --rm migrate
docker compose --profile life up -d companion-api companion-worker companion-life-worker
```

聊天 Worker 只处理即时回复；自动记忆提取由 `companion-life-worker` 执行，因此开启记忆时需一并启动 `life` profile。常规提取不依赖夜间整理开关；有排队、执行中或到期重试的聊天时，后台记忆会让位。

初始化会迁移至当前 schema；开启 M1 时还会准备固定版本的中文嵌入模型，首次需要下载约 90 MB 的公开模型文件。Compose 将缓存放在 `/data/embedding-cache`，后续离线启动可复用；本地默认目录为工作区 `data/embedding-cache`。模型无法加载时会报 `MEMORY_INDEX_UNAVAILABLE`，应修复缓存或下载问题后重启。该缓存只存公开模型权重，个人记忆向量仍在受备份与遗忘机制管理的 SQLite 内。

使用“/记住这个：…”验证新会话的不同问法、冲突提示、确认、纠正和忘记。相似检索返回的是候选，最终回复前还会检查它是否直接对应问题。语义核对调用使用同一个模型适配器，记入每日记忆预算；预算不足时聊天会继续，并明确说明记忆核对暂不可用。

自动记忆默认为待确认假设，有逐字引用证据，冲突内容标为待审查。敏感内容采用规则过滤加模型约束，仍应检查候选；这并非对所有自然语言敏感信息的完美分类器。Core 不将部署凭据加入上下文，并过滤已知密钥和常见凭据格式。外部内容始终作为数据。

## 备份和恢复

日记沿用同一个数据库和遗忘日志；不另建独立且无法回放删除的备份。启用要求及 QQ 指令见 [M1.5 合同](design-v0.4/diary-m1.5.md)。只有完成目标环境与真实样本审核后，才在所有 Core 进程的同一配置中设置 `diary_enabled=true` 并重启。可通过主人私聊立即暂停。HTTP 管理另设独立 `COMPANION_DIARY_ADMIN_TOKEN`，不要将该密钥配置到 Bridge。

协议后端切换请按 [切换与回滚合同](design-v0.4/qq-protocols.md) 执行。先保留 NapCat，实机验证后再切换；Core 数据库和最新遗忘日志不随协议端回滚。

生成 Fernet 密钥，并在部署环境单独保管：

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 把结果放入环境变量 COMPANION_BACKUP_KEY。
uv run python -m companion backup backups/core.enc
uv run python -m companion restore backups/core.enc data/restored.sqlite3
```

容器内使用同样的命令，但备份目录需挂载可写的独立备份卷。热备份使用 SQLite backup API，包含已提交的 WAL 页面，随后加密。恢复只写入全新路径，回放当前配置指向的最新遗忘日志并检查数据库完整性，再原子放置目标文件；现有数据库不会被覆盖。

生产应由你现有的备份服务定时调用该命令，把加密文件和**最新遗忘日志**分别复制到受限存储。首版没有静默创建系统 cron，也没有替你配置异地存储或自动保留策略。旧数据库备份和旧遗忘日志一起回滚会丢失后来的删除意图，禁止这样恢复。

升级前先备份，停止所有已启用 Worker 和 API，再执行一次迁移，随后启动服务。当前 Alembic 链支持空库和同版升级；含日记记录的 `0004_diary` 拒绝破坏性降级。`0003_memory_index → 0002_m1` 仅移除可重建的向量缓存，但不要直接降级删除事实或 v0.5 业务表。应用回退前验证 schema 兼容性与最新遗忘回放，不能仅把数据库和日志都换成旧版本。恢复时会撤销旧媒体会话，并将未完成投递、执行和内容读取置为需要核对的状态。

`0021_reflection_admission_retry` 为反思保留 `RETRY_WAIT` 和下次尝试时间。历史任务若因调用次数或并发不足而在模型请求预留前失败，会在来源集合一致、当前授权有效时续跑；已完成的记忆和日记步骤继续复用。日调用次数耗尽等到配置时区的次日零点，并发占用短暂等待。已发出或计费不明的模型请求保持原状态，不自动重放。可按实际回补天数配置 `reflection_max_daily_calls`（默认 1，最大 5），所有预算及窗口上限仍生效。有等待任务时拒绝降级此迁移。

独立 B 站 Reader 的公开发现使用固定的热门列表 API，每次至多取得 20 个去重视频标题；取得标题后仍需独立的读取任务才能形成正文证据。访问受限、跳转或响应异常会记录失败，不切换接口规避限制。

## 常见运行状态

原生 OneBot Bridge 的引用、卡片正文及签名受控窗口的多图与记忆配置，见 [QQ 输入理解说明](qq-input-understanding.md)。

- `MODEL_RETRYABLE`：最多 3 次指数退避，保留未知计费预留。
- `MONTHLY_BUDGET_EXHAUSTED`：停止新模型请求，普通消息得到明确失败提示；提高预算需修改配置。
- `DELIVERY_UNCERTAIN`：不自动重发；查看 QQ 后使用“/核对发送”指令。新普通聊天仍可继续。
- `ERASURE_LEDGER_MISSING/MISMATCH/TRUNCATED`：停止服务，找回最新独立日志再恢复。
- `OWNER_IDENTITY_MISMATCH`：已有数据库属于另一主人，禁止直接更换 owner；新身份使用新数据库。
- `WORKER_ALREADY_RUNNING`：已有 Worker 持有 OS 锁，正常关停该实例；不要删除锁文件绕过检查。
- 反思 `RETRY_WAIT / SCENARIO_CALL_LIMIT`：尚未发出模型请求，等待次日可用额度后由生活 Worker 续跑；不会重复生成已完成的日记。
- 日记尚未取得模型请求 ID 时，并发槽拒绝延后 30 秒；付费模型日/月配额拒绝延后到下一个本地周期。领取次数和错误原因保留，重试仍复核窗口、来源和预算。已产生 UNKNOWN 的请求停止自动重试；STOP 取消的日记不会因重复调度重新启动。

原始事件按 `raw_event_retention_days` 到期清空，派生原文一同失效。自然到期仅移除过期证据：仍有独立有效正向证据的记忆保留；失去有效来源的记忆降为待审查、置信度归零且不进入检索。纠正命令生成的副本不算独立证据。主人主动遗忘和来源授权删除仍清除整个关联事实及派生闭包。长期保留策略应在实际使用后明确配置。

自然到期在独立 ErasureLedger 中记录为 `raw_retention`，同时保存当次 `retention_cutoff`，恢复旧备份时按相同边界重放。既有删除记录继续执行原删除语义。写入新记录后，应用回退须使用支持该记录类型的版本，保留最新独立账本。

在线 `/纠正这一条` 将原始命令事件与新事实一起提交。Worker 在事实提交后、成功回执登记前退出时，可恢复同一个新记忆 ID；租约、STOP、截止和遗忘仍在恢复与提交时复核。


自主调度切换时，须先使用旧版本 compose 配置停止独立的 `companion-diary-worker`，
再应用统一 Life Worker 的 Core／领域／部署改动。新版本只启动 `companion-life-worker` 消费
自主任务；不得保留旧日记进程并行生成。此步骤需要独立部署授权，代码验收不会自动执行。

### Bridge 分层依据与验证

2026-10-25 检查 DSH QQ 插件 `c8b9158b37817b0dbc1e514e62e16204ad47a6ce` 的
[inbound](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/transport/inbound.ts)
和 [bootstrap](https://github.com/tencent-connect/dsh-qqbot/blob/c8b9158b37817b0dbc1e514e62e16204ad47a6ce/src/gateway/bootstrap.ts)：
附件处理交给传输层、入站与生命周期集中组装。本项目复用该职责划分，实际媒体授权与恢复复用已有
`capture`、`pending_media.MediaPump`、Core claim/check/upload，不复制 DSH 的直接 followup 权限。

单实例锁提取自本项目原生 spool，并复用 Python
[flock](https://docs.python.org/3/library/fcntl.html#fcntl.flock) /
[msvcrt.locking](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)；交接封存使用 SQLite
[显式事务](https://www.sqlite.org/lang_transaction.html)。摘要用于本地交接一致性核验，不是新的业务授权。

`test_astrbot_media_retirement.py` 验证旧状态、并发实例、配置/身份/时间边界和独立迁移 CLI 的真实 HTTP；
`test_native_media_cutover_process.py` 验证迁移后签名捕获、真实 HTTP、独立媒体/聊天进程、上传回执丢失、
擦除及重启。现有原生撤回、抢占、UNKNOWN 和预算测试继续覆盖原 MediaPump。隔离插件打包测试确认
不依赖 Core，且包内没有被退休的 ImageQueue 和宿主发送入口。测试使用模拟外部传输，不是真人验收。
