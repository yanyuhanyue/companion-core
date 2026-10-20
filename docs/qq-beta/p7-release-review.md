# P7 阶段发布复核

当前结论：**代码就绪，专用实验环境已启动但 QQ 未登录；未达到 QQ Beta 验收或生产发布条件。**
运行代码 `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`，配置 `P7-DEMO-1`；P7 报告提交由本 PR head 与私有最终清单单独记录。
本轮没有源代码/迁移修改，旧 #18/#19 保持原有 stacked 关系。

## 已完成与实机证据

- #19 相对 #18 的 24 文件独立复审，无新增可复现缺陷；715 passed / 2 skipped / 1 warning，Chromium 已执行。
- 合成 Compose/build plan、100-event replay、API/worker/auth smoke、wheel/sdist、无 Git 重包排除与迁移初始化通过。
  wheel SHA256 为 `b9c74360fc7cb7a2d50f6abe509153de58b3854c55e5486989000fa186ed0cb4`，与 P6 相同。
  P7 报告 head 的最终 CI 另见本 PR checks/私有最终清单。
- 主人明确说明无生产任务、专用 Companion Core 实验主机并允许使用；执行方据此按实验例外继续隔离安装。
  原服务恢复点仍不存在，没有改为 PASS，也没有复制原服务业务正文。
- 官方 SnowLuma 按 linux/amd64 固定 digest 拉取；Core/Bridge 在目标机从固定 Git archive 构建，
  归档 SHA256 `fbb94835a502447c44edf289d5dd75069c9cc574c8338e094cb6c0f5b5ef7c4b` 校验通过。
- Docker 29 的镜像存储为本机构建产物提供了真实 RepoDigest；运行 Compose 三组件均使用 image@sha256，
  没有把 config/image ID 或 :review 标签冒作可用的 runtime digest，也无需引入额外 registry。
- 新增 SnowLuma、Core API、聊天 Worker 正在运行，迁移退出 0；API live HTTP 200/healthy，
  SnowLuma WebUI/noVNC HTTP 200，supervisor 的 qq/snowluma 均运行。
  Linux QQ 包元数据为 `3.2.32-52194`；经本地 SSH 隧道看到 WebUI 登录页显示 v2.0.3，
  SnowLuma 的 OneBot 版本尚未实测，未登录管理页或 QQ。
- Bridge 镜像在本机隔离容器中验证 UID10004、新卷0700/文件0600、重新打开去重，通过后退出。
  正式 Bridge 尚未启动，不调用 OneBot，不租用或发送 Outbox。
- 初始 Core 加密备份 868452 bytes 与最新 Ledger 已到主人工作站，
  通过 SHA256、独立解密/新路径恢复、最新 Ledger 回放、SQLite integrity/foreign key 检查。
  events/Outbox/ModelUsage 均为0。这只证明空初始 Core 状态，不能证明原服务或生产恢复。
  密钥另存 Windows 当前用户 DPAPI，已验证能解密；完整离线密钥托管尚未建立。
- 原有3个容器的完整所选元数据、挂载/镜像/状态/启动/restart/网络/权限均未变，
  原网络/卷/监听保留，原服务重启操作0次。
  只新增4个本项目容器（3运行、1迁移退出）、2个专用网络、6个专用卷（含合成 smoke 卷），
  管理监听仅回环，公网新增监听0。

| 组件 | 实际 RepoDigest |
|---|---|
| bridge | `companion-p7-bridge@sha256:8367e1d85b90df577df5aaa8c3da0d771cc4545ae5bb241d7f1b45c0ed3e760f` |
| core | `companion-p7-core@sha256:5eb8045c47f62f68202e164bb3fcb7f93be4633c31a15d55047743bc436b7891` |
| snowluma | `motricseven7/snowluma@sha256:78b66d568e3c0991a8ba89bfc188281f87d5d45f32e259c885f43e2312ff0f4a` |

## 配置与数据边界

新项目、目录、网络、卷全部独立命名，不复用 1Panel 网络。公开报告不包含真实服务器路径、
IP+端口拓扑、QQ ID、Token 或私有配置。实际 owner/bot 仅进服务器私密配置；两个 token 与 epoch
在服务器新建并持久保存。Bridge UID10004 配置0600，Core UID10001 配置0600，父私密目录0700。
Bridge 只挂其配置/Spool；Core DB 与 Ledger 分卷，SnowLuma QQ 三卷独立；无 Docker socket。
SYS_PTRACE/seccomp=unconfined 仅在 SnowLuma，Core/Bridge 只读根、drop ALL、无新特权。
内存/PID 上限：Bridge 256MiB/64，Core API 与 Worker 各1GiB/96，SnowLuma 3GiB/512。
所有服务停止/回退只针对本项目，不执行全局 prune 或 down -v。

`mode=demo`、`delivery_enabled=false`、`memory_enabled=false`，Reader/diary/探索/唤醒/社交/
content/executive/media/Android 全关闭，模型 Key 未配置，真实付费调用0。
原生组件许可沿用主人已确认的邮件声明，未独立读取或公布原件。

## 停止点与后续批准

QQ 未扫码，OneBot login/status/version 与 WS 事件未实测，首批0/5、累计0/20，
真实端到端 P50/P95 null。提供 QQ 号码只完成了私有身份配置；不能自动扫码、发消息或使用付费模型。
下一步是主人批准并扫码专用测试账号，再仅验证 get_login_info/get_status/get_version_info 与被动 WS，
要求精确 SnowLuma/2.0.3-node、身份和 online/good；delivery 继续关闭。
确认唯一 Bridge 接管和积压清单后，再分别批准最多5条的具体时间窗口及手机观察人；
首批通过后再批准扩展20场景。跨进程 monotonic 不相减，OneBot ACK 不当手机收到。

G1 Reader grant 生命周期保持 FAIL，另行澄清自然过期、撤销后续访问、撤销既有处理授权；
不在 QQ 安装里扩大重写，不启用 DSH/Windows/公开副作用。
原服务恢复点、生产异地恢复、真实费用、长期稳定性和20自然日记仍缺失，production_ready=false。

## 失败回退与保全

身份/版本或权限不符、意外公网暴露、重复/错误目标、UNKNOWN、Spool 冲突/容量等情况停止新组件。
只停本项目 Bridge/Worker/API/SnowLuma，保留新卷、稳定 epoch、DB、最新 Ledger 和匿名证据；
不影响原有1Panel/OpenResty/redroid。不 down -v、不清空队列、不将 UNKNOWN 设回待发；
真人核对后才按认证流程收口。代码回退使用本表固定已审摘要，不回退 Ledger。
新初始 Core 恢复在工作站新路径完成，没有覆盖在线库。

Windows 恢复辅助脚本首次漏关 SQLite 连接，造成临时文件清理失败；已修正并用另存 DPAPI
密钥重新通过恢复。自动审批拒绝了一条含复核/清理的命令，仅返回 blocked by policy；
该命令未执行，原临时恢复目录保留且未重试删除，修正后的新一轮临时副本已正常清理。
保留目录只含初始空 Core 恢复数据，不含 QQ 对话；私有最终清单记录其位置。
