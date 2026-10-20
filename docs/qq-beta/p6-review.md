# P6 独立审查与修复

审查范围：`7bd47a6...33dff118` 的 44 个文件；修复仅在隔离分支进行。
父审查覆盖 Core/隐私/旧 410 和集成证据，两个独立只读复核覆盖 Bridge 运行时及部署/统计。
本轮未发现可复现的 P0/P1；下列 4 项 P2 均已修复。前三项有原先失败的回归，
Compose 通过构建计划和上游导出器合同定位，并改由 CI 执行独立配置的实际构建。

## 已修复问题

| 等级 | 触发与影响 | 修复及验证 |
|---|---|---|
| P2 | 20,000 字符 `token=x ` 合法输入脱敏后膨胀到 27,500，落盘后被 Core 422 拒绝，阻塞 FIFO 重启恢复 | 正规化最终文本再次检查上限，落盘前拒绝；`test_expanding_redaction_is_rejected_before_it_can_poison_the_spool` |
| P2 | 旧 AstrBot 410 回执含重复 error 字段时，JSON 末字段覆盖会触发删除，违反精确终止合同 | 拒绝重复字段、畸形/额外/非擦除回执，保留 pending；`test_legacy_spool_preserves_non_exact_erasure_receipts` 四组 |
| P2 | 失败模型调用保留 UNKNOWN 预留，统计仅相加成功值；一次重试复现 1320 被报为 43 micro-USD | 失败结算亦发匿名预算/usage 状态，汇总 UNKNOWN 与日志完整性；CoreError/普通异常及日志中断回归 |
| P2 | 运行 Compose 同时将 image@digest 用作 build tag，`build --print` 证实 digest 被转交构建导出器，无法作为普通 tagged image 构建/加载 | 独立本地 `compose.onebot-build.yaml` 构建 `:review`；运行文件只有 digest，CI 改走真实 Compose 构建并验证两份配置 |

Compose 原因可核对 [Docker loader 的 NamedTagged 要求](https://github.com/moby/moby/blob/v28.5.1/image/tarexport/load.go#L136-L144)。
本机没有运行 Docker Engine，未把配置展开或构建计划当作镜像运行通过。

## 逐项审计证据

| 合同 | 检查与结果 |
|---|---|
| 唯一 owner/bot、friend 私聊 | direct 参数用例拒绝 self/group/temp/notice、错误 sender、布尔/浮点/前导零 ID、异常时间 |
| JSON/CQ/Unicode/上限 | 原始帧大小、深度、节点、重复 key、无效 UTF-8/surrogate、64 段、CQ 文本化与最终脱敏上限；媒体不转发 |
| Spool 先持久化/后 ACK | WAL/FULL、文件锁、重开、指纹冲突、epoch/身份绑定、磁盘及队列上限；精确 202 与 410；确认后清正文，哈希收据保留 |
| 发送不确定性 | 实际 Core ASGI 合同覆盖 begin 前/后丢 ACK、OneBot 已执行但 ACK 丢失、result ACK 丢失、持久意图后重启；不确定不补发 |
| 人工核对后迟到 ACK | 新增实际 Core CANCELLED/SENT 两组：旧租约被拒绝、Spool 隔离迟到结果、无再次发送 |
| 发送目标/段类型 | 固定 owner、仅 qq.private.reply、text array、auto_escape；模型 CQ 字符串保留为文字；probe 账号/status/version 全匹配 |
| 网络鉴权 | 公网 DNS 拒绝、私网 TCP pin、无代理/重定向；新本地 HTTPS/WSS 测试使用仅 DNS SAN 证书，验证原主机名、SNI、Host 与 bearer |
| 权限/卷 | Linux 容器 smoke 增强 UID10004、0700/0600、重新打开卷检查；额外 CI root 仅创建不兼容测试卷，再由 UID10004 验证拒绝，Bridge 不用 root 运行 |
| 部署边界 | Bridge 无 Core DB/模型 Key/QQ 卷/socket，内存256MiB/pids64、只读/cap-drop；SnowLuma 三卷/双管理回环端口，特权不扩散 |
| Compose | `verify_onebot_compose.py` 使用合成独立项目和显式 env-file；运行配置无 bridge build、无 OneBot/Core 发布端口；构建计划只有本地标签 |
| 擦除回放 | 事件创建前加密备份 + 最新 Ledger、COMPLETE 旧记录回放、原生/旧 transport 的精确 410；不修改 G1 状态 |
| 统计/包/CI | 匿名 Trace 与白名单字段、分 evidence、成本不当账单、真实延迟 null；四镜像、wheel/sdist/无 Git 重包、迁移初始化、备份回归 |

## 验证记录

- 基线独立复核：运行时 70 项通过，部署/统计 6 项通过；另有真实本地 TLS/WSS 与迟到回执合成检查。
- 首轮新增回归：6 failed / 2 passed，确认三个代码缺陷与统计完整性缺口。
- 修复后第一轮 direct/metrics/erasure：61 passed。
- 本地 Windows：`./scripts/check.ps1`，**715 passed / 2 skipped / 3 warnings，539.81s**；
  包含显式启用的 Chromium 测试。两个 skip 是系统没有 symlink 创建权限的
  `test_plugin_symlink_escape_is_rejected` 和 `test_grant_symlink_is_rejected`。
  warning 为 Starlette/httpx 弃用提示及 HuggingFace 缓存 symlink 降级；不影响已通过断言。
- OneBot 专项：`./scripts/check.ps1 -TestFiles <tests/test_onebot*.py>`，**87 passed，52.13s**。
- `verify_onebot_compose.py` PASS；`uv build --offline`、`verify_package.py` 的归档/无 Git 导出重包/
  wheel 迁移初始化 PASS；`scripts/replay.py` 与 `scripts/smoke.py` PASS。
- 当前新 CI 仍待运行，不能引用 #18 的 10/10；Linux 镜像及权限执行结果将在完成后补充。
- 本地生成临时文件位于工作区，禁止接触此前保护目录。自动审核拒绝了两个审核子任务各自临时目录的清理，理由均为 `blocked by policy`；未改工具重试。

## 覆盖边界与遗留

没有运行 SnowLuma、没有 QQ/付费调用、没有真客户端延迟，没有硬件断电或真实网络 soak。
本地 Windows 不证明 POSIX 权限；由新 Linux CI 的正/负卷 fixture 验证。
stage 日志可能因崩溃丢失，完整费用/UNKNOWN 需要持久数据库和账单对照。
G1 Reader 授权生命周期未关闭，来源关闭下的文字 Beta 仍需独立审批。
云端安全组、供应商快照、异地备份目标及生产恢复仍未验证。
