# P5 预检、部署草案与回滚

## 已做的只读盘点

2026-10-20 经既有主机 Key 严格校验，以用户指定的 SSH 端口连接测试 VPS，未更改服务器。
Ubuntu 24.04.4 LTS / kernel 6.8.0-138-generic / x86_64，8 CPU、内存 15 GiB（约 12 GiB 可用）、
根盘 78 GiB（61 GiB 可用）、swap 2 GiB；时区 Asia/Shanghai。
Docker Engine 29.7.2、Compose v5.5.0 可用，UFW inactive。

服务器已有 1Panel/OpenResty、redroid 测试容器和回环 ADB；公网现有端口 80/443/SSH/1Panel。
因此它不是空白机器。没有停止、替换现有容器、调整防火墙/安全组、接入 Android、
安装 AstrBot/NapCat/SnowLuma 或上传项目配置。机房安全组未取得管理权限，状态未知。
仅从主机监听无法证明外网可达性。完整命令范围为 OS、uname、nproc、free、df、id、date、
timedatectl、docker version/compose/network ls/ps、ss、ufw status，无账号或配置正文读取。

## 许可及版本

[upstream.json](upstream.json) 固定官方代码与只读注册表元数据。
[SnowLuma v2.0.3 EULA 5.4](https://github.com/SnowLuma/SnowLuma/blob/v2.0.3/EULA.md)
要求自动化脚本部署专有组件前取得书面授权；
[LICENSE](https://github.com/SnowLuma/SnowLuma/blob/v2.0.3/LICENSE) 是源码可见非商业许可。
当前没有相应书面授权证据，因此提供人工清单及 dry-run 草案；没有自动拉取或运行原生组件。
官方镜像的 registry digest 是候选元数据，不代表容器内版本或 QQ 版本已验证。
独立发行包内组件不并入 Core/Bridge 镜像。不要运行陌生的一键 root 脚本。

## 批准后按阶段实施

1. 保存本机房快照/现有服务清单，确认独立测试目录、卷和网络无冲突。重新核对官方镜像
   digest/架构、EULA 范围与变更授权；由有权操作者完成许可允许的安装。
   只给独立 SnowLuma 容器官方所需的 SYS_PTRACE/seccomp=unconfined、1 GiB shm。
2. 审查 `deploy/compose.onebot-direct.yaml`，管理口只映射 127.0.0.1:5099/6081。
   OneBot 3000/3001 不映射，Core 8765 不映射。SnowLuma 只接 qq-protocol-internal；
   Bridge 接此网和 companion-internal；Core 只接后者。两张私有 bridge 网络允许应用所需出网。
   不连接现有 1Panel 网络，不挂 Docker socket/SSH/模型 Key/Core DB。
3. 保留 SnowLuma 三卷与登录态。用 SSH 隧道访问管理页；专用账号登录必须由主人批准并扫码。
   对照 `deploy/snowluma-onebot.example.json` 人工设置账户 OneBot，禁用内建
   `statusCommand.enabled`，避免 `#sl` 与 Core 双回复；使用标准 array、关闭 self report。
   不启用其它自动回复、HTTP 回调、反向 WS 或公开写功能。
4. 私有 `bridge.toml` 使用真实 owner/bot、两个独立 CSPRNG 32 字节以上 token，不能与模型 Key 相同。
   单次生成新的随机 epoch 并保留，配置只允许 uid10004 读取（0600、目录0700，绑定路径父目录受限）。
   使用 `python scripts/probe_snowluma_direct.py --config <private-file>`：仅 login/status/version，
   账号/online/good/2.0.3-node 全符合后继续；`private_message_delivery_verified` 仍为 false。
5. 在独立 Core 测试目录采用完整 `deploy/qq-beta-core.example.toml`，显式 `mode=demo`，
   Demo 费率和总预算为 0（不是实际模型报价），所有 `*_enabled=false`，
   executive.enabled/allow_changes=false，各执行/来源/多媒体预算 0；另审查 content.vision。
   `.env` 中 `COMPANION_MODE=demo`、模型 Key 留空，仅同一 owner/bot 和 Bridge token。
   不复制带 live 环境覆盖的旧 `.env`。先仅启动 migrate/API/chat worker，不使用其它 profiles。
   验证 schema head、API live/ready、单 Worker、Core DB 与最新独立 Ledger。
6. 独立构建并审核 Bridge 镜像：`docker compose -f deploy/compose.onebot-build.yaml build onebot-bridge`，
   本地标签为 `companion-onebot-bridge:review`，此构建文件不要与运行配置合并。
   记录其本地 image ID；进入部署注册表前不得假冒 image@sha256。
   当前受控窗口的 `image_ids` 直接固定 Core/Bridge/protocol 三个实际 `sha256:64` Docker ID，
   宿主核对 container.Image 等于 docker image inspect Id；Bridge 无 Docker 读取权限。
   若运行配置采用 registry pull ref，另记真实 RepoDigest；它不替代窗口 image_ids，不默认 latest，也不要求为本地 ID 发布镜像。
   Bridge 无默认自动重启；检查 ws_connected、Core ack 和本地 Spool，初始 delivery_enabled=false。
   开启发送前确认 Core 没有历史 PENDING/SENDING/UNKNOWN，并记录队列；配置开关不是发送许可。
7. 人工批准具体 owner/bot、Asia/Shanghai 时段、5 条上限及回退人后，才可开启首轮文字测试。
   后续 20 场景不擅用第三人的 QQ；负面用例优先隔离合成。发信未知先人工 QQ 核查。
   DeepSeek 真模型另需日/月/总费用、模型/费率、凭据范围；凭据只在受限服务端输入。
   然后单独批准 M1。Reader、DSH 和 Windows 功能继续关闭。

本地 Docker Engine 当前未运行；没有为验证而启动/修改本机系统服务。
Docker 构建验证交给独立 CI 容器 job，Compose dry-run 用脱敏假值和 `config` 校验，不能视作部署。
进入实机测试前，须核对 CI 与候选代码 SHA 对应，签入任何新 Secret 都应停止并处理泄漏。

## 回滚与故障

- 身份不符、冲突 ID、队列满、错误主人、重复回复、账号异常时先停新增 Bridge 的输入与发送；
  不影响其他服务。WS 短暂断线用 0.25–30 秒有界退避，401/403、格式合同或磁盘错误停止等待检查。
- Outbox `SENDING/DELIVERY_UNCERTAIN` 由真人先查 QQ，再用主人`/发送状态`、`/核对发送`收口；
  禁止改回 PENDING、重放发送、清空 Spool 或复用新 epoch 来解除错误。
- 停 Core chat worker/API 前收敛已确认入件；保留 Spool、Core DB、最新 ErasureLedger、
  SnowLuma 三卷、加密备份及匿名证据。恢复到新数据库路径，回放最新独立 Ledger；
  不将旧 Ledger 与旧 DB 一起回退。单库演练通过不代表异地恢复通过。
- 需要代码回退时先停 Bridge，记录 candidate/image digest/队列状态，恢复经审核旧镜像；
  不降级业务 schema、不删最新日志。本轮未改变迁移头。
- G1 来源授权历史归属仍未闭合；此处的 event tombstone 修复不等于 G1 完成。
  DSH 仍是一次性 sdk-minimal Profile，未继承桌面插件。能力目录和 Windows Gateway 留到独立 PR。
- 旧保护目录遵循已有清理拒绝记录；不换工具重试。新测试仅清理本轮拥有的可再生路径，
  保留 `.local/p5/evidence`、发行包和审核来源，用户附件与审计 worktree 不动。
