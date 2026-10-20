# P6 R0/R1 验收基线

任务：`CC-NEXT-20261020-P6-SNOWLUMA-LIVE-BETA`，2026-10-20。
执行范围为独立审查、必要小修、合成验证、只读主机盘点和独立 Draft PR。

## 冻结的上轮候选

| 对象 | 核实结果 |
|---|---|
| main / #18 base | `7bd47a659f60b4904b87508a3dbf0a4d6934d87d` |
| #18 head | `33dff1186899d3a91f4c2023ab0d594024761971` |
| #18 状态 | OPEN / Draft / MERGEABLE / CLEAN；reviews 为空 |
| 差异 | 44 文件，1 commit，+2897 / -6 |
| Push CI | [39373376544](https://github.com/yanyuhanyue/companion-core/actions/runs/39373376544)，5/5 success |
| PR CI | [39373401280](https://github.com/yanyuhanyue/companion-core/actions/runs/39373401280)，5/5 success |

上述检查属于上轮 SHA，不能证明本次修订。新候选位于隔离 worktree 的
`codex/p6-readiness-review`，独立 Draft 以 #18 分支为 base；#18 和 main 均保留。
本次修订的测试与提交证据见 [p6-review.md](p6-review.md) 和 [p6-gates.json](p6-gates.json)。

## 主机与操作边界

用户明确确认“仍用这台主机，保留现有服务”。SSH 使用既有主机 Key 严格验证，
仅查询系统、资源、容器、网络、卷、监听、UFW 和挂载元数据；不读取容器环境或账号数据。
脱敏前后快照见 [p6-host-readonly.json](p6-host-readonly.json)。
实时盘点为 **3 个容器、2 个运行**，与任务书中“13 个”不符，按实际值记录。
1Panel/OpenResty/redroid、端口、绑定目录与数据均保留。云安全组和供应商快照不可见，
独立备份目标未确认，因此主机部署门禁仍为 `BLOCKED_HOST`。

## SnowLuma 固定来源与许可决定

上游 v2.0.3 commit `db142db9e6398c779283eab9afc3552da1e2c361`：
[info.ts](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/info.ts)
返回 `SnowLuma`、`${APP_VERSION}-node`；
[version.ts](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/common/src/version.ts)
读取根 package.json 的 `2.0.3`。因此保留 Probe 的精确 `2.0.3-node` 校验。
这是源码合同核对，运行二进制与 registry 内容尚未实证。

[固定 EULA 5.4](https://github.com/SnowLuma/SnowLuma/blob/v2.0.3/EULA.md)
对专有组件自动脚本部署要求事先书面授权。主人已确认取得邮件书面证明，并明确确认覆盖
v2.0.3 专有组件在该主机的官方镜像拉取、安装与启动。许可范围状态为
`PASS_OWNER_ATTESTED`；本轮未读取邮件原文，授权方/日期/编号未在本轮提供，不对外公布私有证明。
此项是主人声明，不是对原件的独立审验，也不代替运行变更及 QQ 测试窗口的批准。
官方镜像、架构及只读 digest 元数据沿用 [upstream.json](upstream.json) 的历史时间戳；
未来安装前须重新核对，不能把 registry 元数据标成运行证据。

本轮停在 R0/R1。QQ 扫码、收发窗口、唯一对象、首轮 5 条上限与回退人、付费模型预算、
PR 合并和安装启动均需各自授权。Reader/DSH/Windows/Android 保持后置；G1 仍 FAIL。
