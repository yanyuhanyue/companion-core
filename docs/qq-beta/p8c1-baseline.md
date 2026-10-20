# P8-C.1 基线锁定与交付范围

基线读取时间：2026-10-20 13:31:58 UTC / 21:31:58 Asia/Shanghai。
本轮依据用户提供的 P8-C.1 任务书及直接提出的 QQ 指令防误触要求。
任务书文件 SHA-256：`e81a929fb6c92a9b02211413c8be41ab1446a06615870385a98c7f980db1de13`。
以下是执行起点快照，不代表之后的分支状态或任何实机状态。

## 固定输入

| 对象 | 完整 SHA | 起点状态与验证 |
|---|---|---|
| main | `bbd712c1a79497762275e718e9f76959bedf6583` | [CI 39422750301](https://github.com/yanyuhanyue/companion-core/actions/runs/39422750301)，7/7 job 成功 |
| [PR #30](https://github.com/yanyuhanyue/companion-core/pull/30) | `4b791ba7d7bfa2a19aa19f1760ef168c93bd5a46` | OPEN / Draft；[push](https://github.com/yanyuhanyue/companion-core/actions/runs/39424479772)、[PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39424489045) 各 7/7 成功 |
| [PR #31](https://github.com/yanyuhanyue/companion-core/pull/31) | `0c26f2e538c4172865d9e1473c5601cb0972c41d` | OPEN / Draft；[push](https://github.com/yanyuhanyue/companion-core/actions/runs/39425606792)、[PR](https://github.com/yanyuhanyue/companion-core/actions/runs/39425616690) 各 7/7 成功 |

两个 PR 的 base 均为表中 main，已实际读取 GitHub 状态核对。检查逐项包括 preflight、Linux、Windows、container、
browser-reader、plugin-native 和 ci 总检查，不仅引用汇总灯色。
主线已含原 #24–#29 的预算、发送窗口、离线基准、恢复材料、模块边界修复及 CI 分流。
产品迁移 head 为 `0017_paid_model_gate`；没有从实际部署目标读取数据库来确认运行 schema。

## 本轮工作

1. **显式 QQ 指令。** 记忆、日记、发送核对、目标、承诺及自主活动等内置命令统一要求起始 ASCII `/`。
   裸文本、引用中出现的命令、相似前缀不触发控制操作；旧已标记命令记录的隐私与遗忘回放需保持兼容。
2. **C0 公开材料修正。** #30 原版本的目标连接标识及 Schema 固定值先替换为匿名私密引用与盘点哈希，
   保留六项准入合同和合成实验。历史残留如实记录，不在新测试、日志或报告复述原值。
3. **C1/C4 离线兼容。** 保留原 Demo 校验，在默认关闭的独立批准范围内验证普通文本聊天与既有有界窗口联用；
   继续复用同一预算、Spool、Outbox 和独立 journal，不以常驻发送循环代替次数上限。
4. **C2/C3 提供方与恢复材料。** 只读核查真实提供方能否满足现有费用合同；不可证明时保留阻断。
   旧实验主机与最终目标使用不同私密身份引用，真实恢复、部署和各能力的准入范围分别列出。
5. **独立审查和集成。** 对具体修复与新代码进行非作者审查；以最终组合及其精确提交 CI 验收，
   不用原始输入的成功记录替代修改后的结果。

可选 C5 检索性能评测保留为独立后续工作，不占用本轮上述必需交付的关键路径。
#31 已通过的 Worker 重启和 Reader 容器成功 fixture 不重新开发；只在组合中保留并验证。

## 环境与操作范围

本轮代码、测试和缓存位于各自独立工作树；原 main 工作目录与用户附件保持分离。
本轮已明确授权在验证和独立审查通过后合并开发分支。
该版本控制操作不授予部署、付费模型、真实 QQ、真实数据复制/恢复或设备操作权限。
不改写已推送历史，不清理已被拒绝的受保护目录。

历史测试主机仅有历史 Demo 五条手机回复记录，严格场景及成对手机 E2E 仍无新证据。
最终目标的 Core、镜像、配置、运行 schema 和恢复能力没有在本轮实测。
公开材料仅使用 `private_target_ref` 与 `target_inventory_sha256` 等引用，不保存真实连接参数。
已有受限 Windows 文本文件能力不意味着 Core 已启用设备执行。

所有真实 usage、账单、手机端延迟及恢复指标在缺证据时继续为 null / NOT_RUN / BLOCKED。
本轮完整离线交付也不等于 `production_ready=true`。
