# P7 固定基线与执行范围

任务 `CC-NEXT-20261020-P7-SNOWLUMA-QQ-BETA`，核验日期 2026-10-20，Asia/Shanghai。

| 对象 | 固定 SHA / 最新只读状态 |
|---|---|
| main | `7bd47a659f60b4904b87508a3dbf0a4d6934d87d` |
| [Draft #18](https://github.com/yanyuhanyue/companion-core/pull/18) | `33dff1186899d3a91f4c2023ab0d594024761971` → main，OPEN、MERGEABLE、10/10 success |
| [Draft #19](https://github.com/yanyuhanyue/companion-core/pull/19) | `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a` → #18 分支，OPEN、MERGEABLE、10/10 success |
| 运行源码候选 | `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`；应用源码与 `c1986a39178755ddec20f330523cfdeee68be685` 相同 |
| P7 报告提交 | 由 P7-A PR 的 head SHA 和私有 final manifest 单独标识；不混作运行源码 SHA |
| 实机配置版本 | `P7-DEMO-1`，仅服务器私密目录保存实际配置 |

两张旧 PR 没有正式 review、行内审查评论或 issue comments。没有 SHA 漂移。
#18 检查为 [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39373376544) /
[PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39373401280)；
#19 为 [push](https://github.com/yanyuhanyue/companion-core/actions/runs/39377694338) /
[PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39377697730)，每组五项成功。
P7-A base 保持 #19 分支；未合并、改 base、强推或删除分支。

本轮从 #19 固定提交在隔离 worktree 重跑完整 Windows 检查，715 passed / 2 skipped /
1 warning，543.56s，Chromium 显式启用。复核重点为 #19 相对 #18 的 24 文件净增量，
没有重新开发 Bridge 或扩大 Reader/G1 功能。检查细节见 [代码审查](p7-code-review.md)。

## 主机与授权事实

首次 P7 只读盘点仍为 Ubuntu 24.04.4、x86_64、8 vCPU、Docker 29.7.2、Compose v5.5.0，
3 个容器、2 个运行。容器身份/镜像/状态/端口、挂载/启动时间/restart count、
网络、卷、监听与 P6 相同。17 个 bind 条目对应 16 个来源，21 个路径权限元数据检查无错误。
真实路径、账户身份和拓扑只存在私有证据；没有复制原服务业务正文。

供应商实例概要显示系统盘 80GB、无额外数据盘。已查看概要、控制菜单、设置，
未发现快照入口，费用/一致性/存储位置仍未知，不认定为“不支持”。
原服务没有独立恢复点，详见 [恢复方案](p7-recovery-plan.md)。

主人随后明确说明主机没有生产任务，专门用于 Companion Core，允许自由使用。
本轮据此按专用实验主机继续隔离安装，保留原服务；这是实验部署授权例外，
不改变“整机恢复点尚未建立”的事实，也不是 QQ 登录/发送或付费模型许可。
主人另行提供唯一 owner/bot 身份，仅写入服务器私有配置，不在公开报告复述。

## SnowLuma 来源

固定 v2.0.3 源码 `db142db9e6398c779283eab9afc3552da1e2c361`：
[info.ts](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/info.ts)
给出的版本合同为 `SnowLuma` / `2.0.3-node`，online/good 必须都为 true。
[LICENSE](https://github.com/SnowLuma/SnowLuma/blob/v2.0.3/LICENSE) 为源码可见非商业许可；
[EULA 5.4](https://github.com/SnowLuma/SnowLuma/blob/v2.0.3/EULA.md) 的自动部署书面许可
沿用主人已确认的邮件声明，状态 `PASS_OWNER_ATTESTED`，未读取或复制邮件原件。

官方 Docker Framework 固定提交 `3f388b17f8baf47b4c2c9606b8a3121eb8599fef`。
本轮刷新 registry 元数据仍为 index
`sha256:84e6b7800b4002040f39f1b89687d66874afafa352fa2c2928e4f0560b6b6b6c`，
选择 linux/amd64 固定镜像
`motricseven7/snowluma@sha256:78b66d568e3c0991a8ba89bfc188281f87d5d45f32e259c885f43e2312ff0f4a`。
源码/registry 与实际已启动状态分别记录在机器门禁中；不以 tag 元数据证明 QQ 实机握手。

主人另行批准登录与首批发送窗口。精确 OneBot 身份/online/good/版本与真实 friend WS 通过；
5条 Demo 回复经手机确认每次1条、无重复，最终 Outbox SENT=5/attempts=5，Spool accepted=5。
发送窗口已关闭，常驻 delivery=false；memory=false、真实客户端延迟 null，付费模型/DSH/Windows 为0。
严格五场景仍 PARTIAL、完整20场景签收0/20，production_ready=false。

后续主人已确认Reader资料保留语义，并形成独立[G1 Draft #21](https://github.com/yanyuhanyue/companion-core/pull/21)
候选`907d62c1491e0a3613a7653f2d4eb0275ec4a357`。其本地731项通过与发布包/回放/冒烟结果
单独记录；它与本P7报告均以#19为base，尚未部署，不改变本报告的运行源码和关闭开关。
