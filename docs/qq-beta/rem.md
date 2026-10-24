# 私人 REM 创作

REM 使用已有 Memory 事实生成明确标为虚构的私人创作，默认关闭。
本模块已经接入原生 Core job、预算与擦除链路；自动 selector 与控制台 API
由集成候选接线。仅有模块测试通过不表示后台已自动运行，也不表示真实模型或 QQ 已验收。

## 配置和授权

`Settings.rem` 使用 `RemConfig`。默认 `enabled=false`，每日和每月预算为零。
启用配置必须同时提供正的 `daily_micro_usd` 与 `monthly_micro_usd`。
执行还要求 live 模式、Memory 开启、当前主人授权包含 `rem` 与 `memory`，
以及与完整 REM 配置相符的 `capability_policies.rem` 指纹。
配置变更后必须重新完成原有授权流程，不能沿用旧指纹。

默认本地时间 04:00 后可调度，每个本地日期和时区最多创建一条记录。
从最多 20 条近期候选中选取最多 3 条有效、已确认、无冲突的私有 Memory，
并固定事实内容与完整来源证明。素材不足时安静跳过。
输入上限默认 2400 token，输出上限默认 600 token；来源与记录保留期默认均为 30 天。
有效来源保留期取 REM、主人授权、原始事件保留策略中最短值。

## 执行与记账

`rem.schedule_due(core)` 创建原生 `jobs(kind='rem')`，由
`Core.run_once(job_id=...)` 执行。原有预算预留、请求校验、来源检查、
主人输入优先级与 ErasureLedger 共同约束执行。
REM 不提供模型工具，每次生成最多一次模型调用。

模型可选择 `DREAM` 或 `SKIP`。服务器为结果标注 `kind=FICTION`，
探索问题标注 `state=UNVERIFIED`。结果不写入事实、观察、目标、日记或消息 Outbox。
`SKIP` 也保留已发生调用的原生账目。

无效结果失败后不重复支付；超时与调用后进程退出保留 UNKNOWN 责任，
重启不会重试该任务。UNKNOWN 仍受共享预算的对账限制。
已结算的无效 REM 结果不会阻止独立 Diary 任务执行。
发送前确认没有 provider I/O 的主人输入抢占，使用原有 `model.not_dispatched`
证据结算为零费用；预派发时间戳本身不证明 provider 已收到请求。

## 读取、遗忘与恢复

`rem.records(core, limit=20)` 返回任务状态、明确的虚构标记、来源 ID、
有效创作及原始 `model_usage` 摘要。外层控制接口必须验证主人身份。
来源失效、撤回或记录到期后不再返回正文、问题或来源列表。

来源遗忘通过原有依赖与擦除注册表清除 REM 私有内容。
记录自身到期时，`Runtime.expire_raw_events()` 只对 REM 派生事件写入
ErasureLedger；原始 Memory 与其来源不因 REM 到期而被删除。
恢复旧备份仍重放擦除，保留模型费用和不确定责任。

## 复用依据与验证边界

实现前检查了 DSH 的
[jobs-local 实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/jobs/jobs-local/src/index.ts)
以及 [Letta Memory 文档](https://docs.letta.com/configuration/memory)。
DSH 的执行前容量检查与终态结算约束用于对照检查；其进程内 job 存储不用于本项目持久化。
后台记忆整理的分离方式用于对照边界；本项目的 REM 产物始终是虚构作品。
持久化、恢复、权限、费用与擦除均复用 Companion Core 的既有机制。

`tests/test_rem_native.py` 使用真实签名输入审批和 TCP ingest、原生 Core worker、
原生 SDK/预算/擦除流程，仅模拟 provider HTTP。
`tests/rem_process.py` 在独立进程验证正常完成、调用后退出及恢复不重付。
测试回执由集成交接文件统一记录；自动调度、控制台入口与真实环境验收需要
在最终组合候选上另行确认。
