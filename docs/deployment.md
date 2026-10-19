# 部署与运行

## 先准备

需要 Linux VPS、Docker Compose、可用的 AstrBot + NapCat/OneBot 连接、主人 QQ、机器人 QQ，以及模型 API Key。代码默认单主人、私聊、文字消息。不要将数据库放在 NFS、SMB 或 Tailscale 文件共享目录。

本地兼容性核查对象：AstrBot 4.29.1，源码提交 `e5e21b71aec5642f110eb115eb8e9a0d8028b7e5`；使用 `stop_event()`、`Star.initialize/terminate`、`StarTools.get_data_dir()`、`Context.send_message()`。实际生产部署应固定你验收过的 AstrBot/NapCat 版本，避免自动升级。

2026-10-19 的运行版本记录为 AstrBot 4.29.1 / NapCat 4.18.38；镜像固定方式和 NapCat 构建步骤见 [运行版本说明](../deploy/README.md)。

## 启动核心容器

```bash
cp config.example.toml config.toml
cp .env.example .env
chmod 600 .env config.toml
# 编辑 .env，填写 QQ 账号、随机 bridge token 和模型 Key。
# 编辑 config.toml，核对模型、费率与预算。先保持 memory_enabled=false。
docker compose up -d --build
docker compose exec companion-api /app/.venv/bin/python -m companion status
```

Compose 启动一次性迁移容器、API、聊天 Worker 和独立日记 Worker。默认关闭日记时不生成日记或调用模型。数据库和遗忘日志分别放在 `core-data`、`erasure-ledger` 卷中。API **没有宿主机端口映射**，通过 Docker 网络 `companion-internal` 访问；网络保留模型 API 所需的出网能力。不得给 8765 添加公网端口或公网反向代理。容器以非 root 用户运行、只读根文件系统、删除 Linux capabilities。

现有 AstrBot 容器接入相同网络：

```bash
docker network connect companion-internal <你的AstrBot容器名>
```

网络接入也应写进现有 AstrBot 的 Compose 配置，以免重建后失效。若 AstrBot 运行在宿主机，可采用本机受控网络接入；不要为方便接入而暴露公网端口。

## 安装桥接插件

```bash
uv run python scripts/package_bridge.py
```

生成 `dist/astrbot_plugin_companion_bridge-v0.5.0.zip`。在 AstrBot 安装本地插件 ZIP，或把 `astrbot_plugin_companion_bridge/` 目录放入其插件目录，然后重载插件。私有仓库可用本地 ZIP，避免在 QQ 或插件配置里保存 GitHub token。

配置插件：

| 项 | 值 |
|---|---|
| `core_url` | `http://companion-api:8765` |
| `owner_qq_id` | 与 Core 一致的主人账号 |
| `bot_qq_id` | 与 Core 一致的机器人账号 |
| `bridge_token` | 与 `COMPANION_BRIDGE_TOKEN` 一致 |
| `protocol_backend` | 默认 `napcat`，切换时可设为 `snowluma` |
| `protocol_epoch` | 沿用原 NapCat 可为空；每次切换/回滚使用新的批次号 |

主人会话的 AstrBot 默认 Agent 应显式关闭。桥接监听器还会以高优先级调用 `stop_event()`，在任何 HTTP 请求前取得回复所有权；Core 故障时会把消息留在插件收件箱，不回落到第二个 LLM。其他插件仍可能在更早的优先级运行，必须在实机确认没有重复回复。不要对主人启用另一个独立聊天插件。

`Context.send_message()` 在此版本仅返回是否匹配到平台，发送异常会向上传播。成功表示调用链完成，不表示主人已读；它不提供 OneBot 消息 ID，因此首版 `provider_message_id` 可以为空，状态不明需人工核对。

## 开启记忆

先在真实 QQ 上测试正常消息、重复消息和故障恢复，确认仅一条主回复，再将 `memory_enabled=true`。停止 API/Worker 后运行迁移初始化，再启动服务：

```bash
docker compose stop companion-worker companion-diary-worker companion-api
docker compose build
docker compose run --rm migrate
docker compose up -d companion-api companion-worker companion-diary-worker
```

初始化会迁移至 `0004_diary`；开启 M1 时还会准备固定版本的中文嵌入模型，首次需要下载约 90 MB 的公开模型文件。Compose 将缓存放在 `/data/embedding-cache`，后续离线启动可复用；本地默认目录为工作区 `data/embedding-cache`。模型无法加载时会报 `MEMORY_INDEX_UNAVAILABLE`，应修复缓存或下载问题后重启。该缓存只存公开模型权重，个人记忆向量仍在受备份与遗忘机制管理的 SQLite 内。

使用“记住这个：…”验证新会话的不同问法、冲突提示、确认、纠正和忘记。相似检索返回的是候选，最终回复前还会检查它是否直接对应问题。语义核对调用使用同一个模型适配器，记入每日记忆预算；预算不足时聊天会继续，并明确说明记忆核对暂不可用。

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

升级前先备份，停止聊天/日记 Worker 和 API，再执行一次迁移，随后启动服务。当前四个 Alembic revision 支持空库和同版升级；含日记记录的 `0004_diary` 拒绝破坏性降级。`0003_memory_index → 0002_m1` 仅移除可重建的向量缓存，但不要直接降级删除 M1 事实表。应用回退前验证 schema 兼容性与最新遗忘回放，不能仅把数据库和日志都换成旧版本。

## 常见运行状态

- `MODEL_RETRYABLE`：最多 3 次指数退避，保留未知计费预留。
- `MONTHLY_BUDGET_EXHAUSTED`：停止新模型请求，普通消息得到明确失败提示；提高预算需修改配置。
- `DELIVERY_UNCERTAIN`：不自动重发；查看 QQ 后使用“核对发送”指令。新普通聊天仍可继续。
- `ERASURE_LEDGER_MISSING/MISMATCH/TRUNCATED`：停止服务，找回最新独立日志再恢复。
- `OWNER_IDENTITY_MISMATCH`：已有数据库属于另一主人，禁止直接更换 owner；新身份使用新数据库。
- `WORKER_ALREADY_RUNNING`：已有 Worker 持有 OS 锁，正常关停该实例；不要删除锁文件绕过检查。

原始事件按 `raw_event_retention_days` 到期清空；失去有效来源的记忆会降为待审查且不进入检索。长期保留策略应在实际使用后明确配置。
