# P10 本地运维观察

`python -m companion status` 只读现有配置和数据库，不构造 Core/Runtime、不加载 BGE、
不调用模型，不初始化身份、迁移、恢复或修复账本。缺库、旧 schema、未初始化身份、
UNKNOWN/未结算预算、投递不确定及过期租约都会显式报告。输出只有计数、时间、资源
元数据和安全阻断代码，没有正文、密钥、路径、端点或 QQ 标识。

SQLite 使用 `mode=ro`、`query_only` 和单次读事务，包含已提交 WAL。SQLite 可能使用
自己的 SHM 协调文件；这不是“完全无文件系统活动”。无法安全读取就报告失败，
不会使用忽略 WAL 的 immutable 模式，也不复制、checkpoint 或修复数据库。

默认只读 schema/capability、队列和预算聚合、资源元数据及独立账本的短头身份。
`database.integrity=NOT_CHECKED`、`checked=HEADER_ONLY` 不证明恢复一致性。显式执行
`python -m companion status --integrity` 才做一次 SQLite quick_check 和有大小上限的
独立账本完整核对；仍不重放或修复。完整检查超过读取上限时保持阻断。

连续采样默认在本地进行，输出必须是不存在的新文件：

```powershell
python -m companion observe --output .local/observation-run.jsonl --samples 12 --interval-seconds 5
```

`--url http://127.0.0.1:8765` 可观察已运行的 API；采集器只访问带原认证的
`/internal/v1/ops/status?summary_only=true`，不启动 API/worker，不执行原 live window
完整 guard。地址支持显式 IPv4/IPv6 literal，例如 `127.0.0.1` 和 `[::1]`；默认必须
loopback，域名直接拒绝。HTTPS 正常验证信任链及 IP SAN。禁止跳转和环境代理。
外部 IP 目标还必须明确给出 `--allow-external`、
`--evidence-type authorized_runtime` 和 `--authorization-sha256`。哈希只是既有批准的
引用，不会创造部署、数据或网络授权；此轮没有运行外部目标。
单次 HTTP 连接和完整响应接收共用 5 秒绝对期限，超时取消 I/O；接收完成时再次核对
snapshot UTC 新鲜度。远端响应只投影固定聚合字段；未知附加字段丢弃，未知阻断代码、
错误类型、非法状态或越界计数让该次采样失败，原响应不会进入观察文件。

`budget.calibration` 直接复用预算模块对原始只读 SQLite 连接的 usage 聚合，只有
SETTLED 付费样本进入误差校准，UNKNOWN 预留不算实际 token。按配置费率计算的 USD
估计不是供应商账单；没有校准样本时 currency 为 null。

每条记录保留实际 UTC、单调时钟、采集耗时、实际间隔、失败和 API instance。
间隔超过 `--max-gap-seconds`（默认 interval×1.5+0.1）、采集本身过慢、墙钟跳变、
API instance 改变、失败或已观察阻断都会断开 clear segment。不同 run id 不拼成连续
记录，不能追加旧文件；强杀或中断后没有 end 记录的文件不完整。单次采样覆盖时长为
0，摘要只给最后一段实际观察跨度，不累加缺失间隔或多段时长。
命令正常退出只表示完成诊断或采样，仍须读取 blockers/failures，不能据退出码签收健康。

API alive、schema 可读、空队列及按时采样都不能证明 worker 正在推进。现有系统没有
独立 worker heartbeat，输出明确为 `worker_progress=UNOBSERVED` 和
`worker_health_proven=false`；本地数据库路径也无法识别 worker 重启。HTTP instance
变化只证明 API 实例变化。`clear_segment_observed_seconds` 表示采样检查未见阻断，
不等于连续健康、端到端可达、正式部署或 production_ready。

本轮验证使用本地合成 SQLite/HTTP 和秒级实际等待。没有真实 QQ、供应商、付费模型、
告警联系人或真实小时/天数运行证据。观察文件不是第二份收件、发送或预算账本，
不会改变任何额度、UNKNOWN、租约、遗忘记录或业务状态。
