# 受限工具执行（PR-G）

默认 `executive.enabled=false`、`allow_changes=false`、日调用上限为 0。普通私聊不进入工具
循环；原 M0/M1 `/internal/v1/tasks` 和 `/approvals` 继续拒绝。新端点只接受独立管理身份。

## 已实现的能力

| Capability | 等级 | 执行边界 |
|---|---|---|
| `windows.read_text` | L0 | 显式工作目录中的 UTF-8 文本，最多 12000 字符 |
| `windows.write_text` | L2 | 精确路径与文本审批后创建新文件，不覆盖已有文件 |
| `dsh.run` | L2 | 精确 prompt/预算/调用数审批；一次隔离 SDK 会话 |

注册表固定，不能通过模型参数添加能力；系统命令、管理员操作、公开发布等 L3 不存在可调用
入口。外部观察不允许作为工具请求授权来源，必须绑定尚未擦除的主人私聊事件。配置中的目录
别名映射由管理员维护，不能由请求改变。读取拒绝绝对路径、`..`、隐藏文件、符号链接、
Windows junction、硬链接及非允许的文件后缀。目录应是专用的交付目录，不能设为用户主目录、
Core 数据目录、QQ 插件目录或凭据目录。

实际文件操作使用已打开的句柄：Windows 从盘符根目录开始逐层持有目录句柄，禁止共享删除或写入，拒绝重解析点，然后在同一文件句柄上读写及校验。POSIX 使用目录描述符与 `O_NOFOLLOW` 逐层打开。检查后替换目录无法把访问重定向到另一个目录；已有写入者或目录锁冲突会使请求失败关闭。依据 [CreateFile 的共享和重解析语义](https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew)。

审批固定绑定能力、目录别名、参数、预算、最大模型调用、输入字节、输出 token 与超时的
SHA-256。审批单独提交，最长一小时有效；更改请求需要新 idempotency key 和新审批。
执行前再次校验能力、审批、隐私版本和来源。发送前在同一事务保留最坏费用和调用次数；
RUNNING 后响应丢失、超时、陈旧 lease、无效回执均转 UNKNOWN，保留预算，不自动重试。
仅 LEASED（尚未发送）可在超时后重新领取。撤销 RUNNING 表示结果不确定，不能声称外部效果
已回滚；远端 DSH 的硬超时和模型授权到期仍生效。

真实成功回执才写 `source_kind=tool_result` / `FETCHED` Observation，保留 task、scope hash
和源事件依赖。FAILED/UNKNOWN 不生成已完成经历。DSH 的终止事件、非空最终文本和工具错误
共同检查；它返回的文字标识为会话结果，不作为外部效果已核验的声明。模型输出始终是数据。

## 独立 Windows 服务 / MCP

`companion.executor_gateway:create_executor_app` 同时提供 `/internal/v1/execute` 和认证
`/mcp`（JSON MCP initialize、tools/list、tools/call）。只暴露 `execute_scoped` 工具，输入
必须为完整的审批后 envelope。Core 使用 HTTP v1，独立服务不 import Core 或 AstrBot。

Windows 主机为普通用户启动：设置独立 `EXECUTOR_TOKEN`，`EXECUTOR_STATE` 指向专用状态
文件，`EXECUTOR_WORKSPACES` 是 JSON 目录别名映射；仅明确需要创建文件时设置
`EXECUTOR_ALLOW_CHANGES=true`。运行 `python -m uvicorn
companion.executor_gateway:app_from_env --factory --host <Tailscale-IP> --port 8090
--no-access-log`。限制主机防火墙仅允许 Core 的私网地址。掉线后任务成为 UNKNOWN，不改用
另一个高权限执行器。状态库只留随机任务 ID、带密钥的请求指纹和尝试状态，不保存正文。

## DSH 容器与硬预算

`deploy/compose.executors.yaml` 是独立 opt-in `tools` profile，启动后 DSH 仍默认禁用。
它只挂自身执行标记卷，未挂 Core 主数据库、QQ 凭据、宿主目录或 Docker socket。
SDK 使用官方 `sdk-minimal`，每次创建独立 home/work，完成或失败后清理全部会话文件。
DSH 进程以 UID 10002 运行；只读根文件系统、私有 PID namespace、无新增特权、CPU/内存/
进程上限生效。监督进程仅保留容器内切换身份和清理任务所需能力；DSH 无法读取它的认证环境。
超时后清理整个专用 UID，包含脱离父进程组的后台进程。

执行容器只有 `internal:true` 网络，没有公网出口；只可访问独立预算代理。真正的模型
API key 只存在代理容器。每个任务获得独立、短期、可撤销的 opaque token，代理对每次
模型调用（含重试和上下文压缩）预留费用，强制模型、输入字节和输出 token 上限，耗尽即拒绝。
代理重启丢失授权，因此失败关闭；Core 的最坏费用预留仍持久保留。费用以管理员配置的上界
价格估算，不声称等于账单实付。SDK 可能用尽调用额度而不能完成任务，此时保持 UNKNOWN。

模型上游必须为 HTTPS。专用模型凭据应在供应商侧另设硬消费上限。Compose 的模型代理有
出口，执行容器没有；不可给执行容器添加第二张联网网络。Core 若在 Docker，加入已创建的
`companion-tool-private` 网络后，以 `http://172.30.91.10:8090` 访问网关；只共享网络，
不共享数据卷。主机上 Core 可以使用 `http://127.0.0.1:8090`。

## 隐私与恢复

`erase_executive(store,event_ids)` 根据授权源或结果事件找到任务，擦除参数、scope hash、
结果引用、审批与证据边，撤销 lease。传递遗忘通过既有 event_dependencies 同时擦除工具
Observation。备份恢复必须调用 `restore_executive`：审批全部撤销，RUNNING→UNKNOWN，
其他未完成任务→CANCELLED，旧备份不能重新取得执行授权。运行中擦除后的迟到结果不能落库。
用户明确要求创建的工作目录文件属于交付物，不是 Core 派生表，不会因遗忘对话而静默删除。

## 验证与启用边界

本地测试覆盖真实文件读写、MCP/HTTP 认证与回执、越界路径拒绝、独立精确审批、过期和撤销、
预算/次数预留、UNKNOWN 不重试、lease CAS、隐私与恢复钩子、模型代理硬限额及 SDK 结果分级。
另含真实 Windows 目录联接替换、持有目录期间的重命名/写入拒绝，以及工具 Worker 独立实例锁回归。
DSH 测试使用 SDK 替身；没有启动真实 DSH 会话、付费模型或 Windows 高权限工具。真实 SDK
镜像启动、目标 Tailscale 联调及供应商价格/凭据仍需启用前在专用环境验收。

协议依据：官方 [Python SDK](https://github.com/deepseek-ai/deepseek-harness/tree/master/python/sdk)
与 [sdk-minimal 组成](https://github.com/deepseek-ai/deepseek-harness/tree/master/packages/bundle/sdk-minimal)。
现有 [dsh-Agentlink](https://github.com/hootandy321/dsh-Agentlink) 的 Web Host 协议提供会话
协调，但不具有本方案需要的逐调用硬预算，因此使用官方 SDK 加隔离预算代理承接工作任务。
