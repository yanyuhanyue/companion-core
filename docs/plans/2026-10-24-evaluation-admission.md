# 固定上下文实验的原账本准入

本轮范围已由主人选择“纳入本轮 Core 改造”批准：离线实现无投递实验用途，原全局预算
之上再限 6 次 / $0.05，保留来源、撤销、停止与不确定性。部署、真实付费和私密传输
仍须后续单独授权。基线 main 为 85c98ec；独立于已冻结实验准备 PR101。

## 检查过的成熟实现与复用

- DSH [system-prompt 实现](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/system-prompt/src/index.ts)
  将完整静态指导、来源上下文和每次 assembly 的取消信号分开；本次保持冻结 payload，
  每次请求重新验证来源与当前授权，不把 prompt 当权限。已核查 `PromptSection.complete`、
  `PromptContext` 和 `AssembleContext.signal`，不执行或引入 DSH runtime。
- DSH [agent-loop](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent-loop/README.md)
  把已有 driver、请求步骤和取消职责分开；Core 仍只有原 job worker，不新增并列执行器。
- [Pydantic Evals](https://pydantic.dev/docs/ai/evals/getting-started/core-concepts/) 的显式
  case/input/metadata 与可检查输出适合冻结实验；本次使用既有 JSON 计划，无新增框架依赖。
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html) 的同库 IMMEDIATE
  事务用于原 budget 与实验子限制的原子竞争；本地余额副本不能取代原权威账本。
- [HTTPX hooks](https://www.python-httpx.org/advanced/event-hooks/) 与
  [transports](https://www.python-httpx.org/advanced/transports/) 支持实际序列化请求验证和
  mock provider；复用原 ModelClient admission fence，不另写付费 HTTP 路径。

现有机制审阅：`budget.reserve/complete`、`paid_budget.journal`、`cloud_budget.reserve`、
`live_text.guard/dispatch_fence`、`private_life`、`ConsentService`、原 `jobs/job_sources`、
`reply_revision`、`DerivedCleanup`、backup/erasure replay。

## 集成约定

新增用途称 `evaluation`，默认关闭。通过现有 owner control token 接口提交和读取；
授权仍使用现有 ConsentService，能力为 `model.evaluate`，session 精确绑定实验 manifest
哈希。预览不授予权限；先预览 hash，再显式授予 consent，再提交。既有 live/paid
合同、window 身份、原数据库及 journal 不变；窗口迁移后旧实验不继续执行。

每份 manifest 只建一个原生 job，按冻结顺序执行六项。稳定 request identity 和原 usage
防止重新提交、双 worker、进程重启或响应落盘失败导致重付。过期租约保守停止实验；
已派发未结算保留 UNKNOWN，不猜测成功、不自动补跑。正常 owner chat 优先。

manifest 和结果是可擦除的派生数据；关联原始 owner/private sources、已送达上下文与
原事件依赖，来源过期/撤回/擦除/授权撤销/停止均阻止后续 I/O。擦除清掉文本，保留原
财务责任；旧备份通过同一 erasure registry 重放。读取结果不会把它变成聊天或记忆。

本模块拥有新增 evaluation 文件与测试。共享 Core/budget/config/policy/API/erasure
接口及迁移由根指定的单一整合人协调；本分支不依赖其他未冻结补丁。

验证目标：定向回归，以及真实 TCP API、独立进程原 worker、原 Store/journal 和 mock
provider 的合成验收；覆盖与普通任务竞争、双 worker、撤回/撤销、STOP、过期、窗口
变化、重启、UNKNOWN、擦除重放、六项 payload 哈希不变、Outbox/Memory 零增量。
