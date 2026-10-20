# P8-A 离线就绪复核

本报告记录离线候选、历史手机证据和本轮只读主机观测。P8-A 完成须同时满足本地检查、
独立风险修复验证和最终固定 HEAD 的 push/PR CI；具体最终提交与 CI 由实际 PR 和私密
validation manifest 关联，不把旧提交的检查当成新提交通过。当前运行仍是 Demo，
本轮没有新增 QQ 真实验收，没有付费请求、部署或真实资料备份/恢复。

## 固定基线

| 对象 | 执行开始读取的完整 SHA | 依赖 |
|---|---|---|
| main | `7bd47a659f60b4904b87508a3dbf0a4d6934d87d` | #11–#17 已合入 |
| #18 | `33dff1186899d3a91f4c2023ab0d594024761971` | main |
| #19 / P8 基线 | `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a` | #18 |
| #20 / P7 报告 | `904eafbb698ce8bfa1cdf7aa0a89ef6aab78dfbf` | #19 的报告支线 |
| #21 / 原 G1 候选 | `907d62c1491e0a3613a7653f2d4eb0275ec4a357` | #19 的功能支线 |
| 实际运行源码 | `940b6f28c16f7d495c3ff86e6b5bc7fad63e917a` | 配置来源 `P7-DEMO-1` |
| P8 报告/回归提交 | 实际 P8 PR head 与最终 manifest | 从 #19 新建；不合入 #20/#21 开发 |

开始时四张 PR 的 head/base 均与任务书相同，40 个 job 逐项成功。
每组 run/job 链接、提交时间和核验时间见 [证据索引](p8-evidence-index.md)。
随后任何合并状态变化单独记录在最终 GitHub PR 状态与集成清单，不能改写这张开始快照。

任务书默认仅 Draft；主人最新请求明确授权适当合并和删除已整合分支。
本轮以该明确授权处理版本控制，在可复核的测试/独立修复之后再执行。
代码合并不授予部署、收费、QQ、记忆或真实恢复权限；无强推/重写历史。

## 运行路径和保护边界

实际普通聊天路径保持 QQ → SnowLuma OneBot v11 → 原生 Bridge/Spool →
Core HTTP v1 → chat worker/model adapter → Outbox → 同一 Bridge 的 `deliver_tick`。
`python -m companion_onebot_bridge --config ...` 启动 Bridge；其 `delivery_enabled=true`
会开启持续发送，不能作为有次数/时间上限的窗口执行器。P7 操作者辅助脚本不在 #19 产品内，
不能假装存在仓库内的限额发送 CLI。新窗口执行器的审查/证据要求见
[待审批窗口](p8-live-window-proposal.md)。

Core 模型适配器不承担存储、授权和发送；聊天不经过 DSH，不引入 AstrBot/NapCat。
配置模板 `deploy/qq-beta-core.example.toml`、`deploy/onebot-bridge.example.toml`
继续 Demo/发送关闭/记忆和自主能力关闭。Reader 没有单独 `reader_enabled` 产品字段，
本报告该状态指无 Reader worker 且 observation/exploration 等入口关闭，不能发明一个配置键。

保护对象包括现有服务、QQ 三卷、Spool、Core DB、最新独立 Ledger、备份/密钥材料和被拒清理的目录。
只读 SSH 仅获取所选元数据与匿名计数，无正文/Cookie/密钥输出，无服务启停或主机变更。
本次观测为 schema0015、observations0、events5、Outbox SENT5/attempts5、无 grant 表，
delivery=false，原有服务所选身份/挂载/镜像/状态/启动/restart/网络/安全元数据与 P7 前快照一致。
这不代表完整业务内容、磁盘、账单或恢复能力已验证。

## 门禁

| 项目 | 精确结论 | 来源 |
|---|---|---|
| P8-A | OFFLINE_READY；完整组合已验证，最终同 HEAD CI 须由对应 PR 核验 | 本页与最终 PR/manifest |
| 历史手机回复样本 | 5/5，各输入一条回复；本轮新增 0 | 固定 P7 人审与本轮计数一致 |
| 严格首批场景 / QQ01–20 | 0/5、0/20；PARTIAL/NOT_RUN | [独立空验收表](p8-human-acceptance.md) |
| 真实客户端 E2E | 成对样本0；P50/P95 均 null | 原组件 ACK 段不能补数 |
| 恢复 | 仅初始空 Core 离机恢复已验证；真实非空/整机/原服务仍 BLOCKED | [恢复审批包](p8-host-recovery-review.md) |
| G1/M1 | Reader 未部署/关闭，M1 未授权/关闭 | [G1 独立审查](p8-g1-independent-review.md) |
| 付费模型 | 实际调用0；参数/金额待主人确认，执行门禁有缺口 | [预算合同](p8-model-budget.md) |
| 生产 | `production_ready=false` | [机器门禁](p8-gates.json) |

## 独立发现与处理

原 #21 的独立审查复现两处 P1：Core 撤销发生在租约返回后、外部 GET 前时仍可读一次；
grant 遗忘遗漏通过显式 `supersedes_memory_id` 继承的主人纠正记忆。
独立 G1 补丁保留在单独支线，红/绿回归与后续审查见该报告，不混入 P8 QQ 分支。
即使修复通过，已授权请求与实际 I/O 之间的分布式竞争仍需准确说明，最终提交复核不可移除。

P1 付费启用阻断项：当前聊天日金额、单次金额、授权窗口累计总额、统一模型并发门禁缺失，
soft context 不是完整当前输入硬限额。未知价格聊天的现有行为不能作为费用保护。
本轮只准备合同，未扩大产品能力；这些事项须在收费前实现/验证并另获费用许可。

P2 发布证据缺口：真实非空/整机恢复、严格真人语义和故障场景、完整客户端计时、
真实 Reader 生命周期、自然日记与长期稳定性仍未证明。没有 P0 新发现；未知项不升级为 PASS。

## 验证记录

Windows/Python3.12.10，锁依赖 `uv sync --frozen --extra onebot --group reader`；
Chromium 使用工作区已有缓存，HF 离线。完整检查已通过：726 passed、2 skipped、1 warning，593.28s；两个 skip 是 Windows symlink 权限条件，warning 为既有 Starlette/httpx 弃用提示。结果已写入机器门禁。

已复用 OneBot/M0/M1/恢复故障用例，并新增11个有失败检测点的合成回归：
多轮已发送/未知回复上下文、soft 溢出、输入输出脱敏、预算预留/UNKNOWN、
模型 Stub 请求/错误、拒绝帧后的 FIFO 继续、磁盘容量原子性。
[离线清单](p8-offline-regressions.md) 记录146项受影响用例通过和11项新用例通过，
[恢复包](p8-host-recovery-review.md) 记录15项合成恢复检查通过；不累计成真人场景数。

实际入口：`./scripts/check.ps1`、`scripts/replay.py`、`scripts/smoke.py`、
`scripts/verify_onebot_compose.py`、`uv build`、`scripts/verify_package.py`。
本机 Docker daemon 未运行，Linux 镜像/UID10004/0700卷/错误 owner 拒绝由同 HEAD 的
CI `container` job 执行；本机该项是 `SKIPPED_BY_ENV`。浏览器和 native fixture 结果单独标识。
命令退出码、时间、通过/跳过数和归档 SHA256 保存在私密 manifest；公开不放真实正文或身份。

## 回退与下一步

没有部署变更可回退。版本控制使用保留祖先的 merge，后续如撤销代码应新增可审查 revert，
不覆盖主线或真实 DB。若将来批准部署，固定镜像/配置、先获得最新非空备份和独立 Ledger、
在隔离目标恢复；UNKNOWN 保持，不重发，不回退到丢失新 Ledger 的旧库。
G1 新 ledger/迁移的旧二进制兼容限制继续适用，不能直接降级真实数据。

优先事项：补齐并离线验证聊天费用/并发门禁；确认明确模型/费率/额度；
确定真实非空与宿主备份审批；最后才申请有硬上限的新 QQ 严格窗口。
M1 和 Reader 仍分别申请，不能捆绑开启。

下轮可执行提示词：
“基于最终已接受的 P8 与 G1 修复固定 main SHA，先只做离线工作，补齐聊天单次/日/月/累计费用与
并发硬门禁，使用 Stub 验证未知费率、失败/崩溃预留与超限停止；保持 Demo、QQ发送、M1、Reader
关闭。准备明确候选、真实非空恢复及新QQ窗口审批包，未分别获批前不部署、不收费、不发送。”

## 后继修复与完整组合验证

独立 [G1 后继补丁](https://github.com/yanyuhanyue/companion-core/pull/23) 固定 `6d200378aaeed5a13e2aa76a676cb0c7d8e594c4`，保留原始审查结论，
并分别修复每次账号 GET 前的 Core 授权复核与显式纠正后继的遗忘闭包。
最终定向81项、原两条失败复现和第二位审查者独立7项均通过。
已丢失必要归属的旧 COMPLETE 以明确错误阻止启动/恢复发布；没有推断归属或追加恢复期 Ledger。
真实 Reader/G1 仍未部署、未授权启用。

完整组合验证提交 `fbb5861f336d3cd6da74b13ae67454cfd715aad9`：**771 passed、2 skipped、
1 warning，605.83s**，Chromium 执行、ruff通过。
它包含本P8回归、#21原实现、上述G1补丁和固定P7报告，额外验证组合后的源码，
不把P8单支线测试冒充已验证G1组合。最终报告提交仅追加文档；代码/测试哈希由私密清单核对。
精确最终 P8 与 G1 补丁 HEAD 的 push/PR CI 须实际全部成功，最终 run ID 与 SHA
在对应 PR 和最终 manifest 中关联；本条不预填尚未发生的 CI 结果。
