# P10 真实运行记录

更新日：2026-10-21。本页记录已结束的实际运行；批准原件、目标定位及原始回执保存在受限工作材料中。
本页不授予下一次部署、私人数据、QQ 或模型调用权限。`production_ready=false`。

## 首轮纯文字窗口

运行候选为 `1e10535fd11a66b5b4e0fbca724c20a1b77ce2cc`。
Core、Worker 和 Bridge 使用新建镜像及最新数据的独立副本；原聊天服务已停止。
Core 初始化、迁移、最新删除 Ledger 回放及启动检查通过，实际 schema 为 `0017_paid_model_gate`，
最终 SQLite integrity 为 `ok`。这证明启动与数据检查通过；空队列没有证明 Worker 已处理真实模型任务。

| 实际事件 | UTC 时间及结果 |
|---|---|
| 文字窗口打开 | 2026-10-21 05:37:25.247241 |
| 单次认证接收启动 | 05:37:43.372803 |
| 接收失败并关闭 | 05:37:43.392183；没有捕获输入 |
| Core 确认停止 | 05:37:43.431767 |
| 新聊天容器停止完成 | 05:40:33.201；API、Worker、Bridge 均停止 |
| 最终核验完成 | 05:49:10.865649；新建设置容器也已退出，期限守卫在确认停止后退出 |

此次新增认证输入、模型 admission、真实模型请求、QQ 发送尝试均为 **0**。
原库的 5 条事件、5 个 SUCCEEDED 作业、5 条 SENT Outbox 和 5 条 SETTLED usage 保留，
这些是先前数据，不计作本轮成功对话。手机签收、供应商 usage、端到端延迟与多轮效果仍无样本。

只读协议诊断确认：配置指向的 OneBot HTTP/WS 端口没有监听，HTTP 连接被拒绝。
SnowLuma 容器处于运行状态，但 QQ 登录及协议健康状态 **未得到证明**。
本轮没有重启 SnowLuma、修改协议配置、重新登录、重连接收或补发消息。

Core STOP、Bridge STOP、独立 Budget Journal 的停止记录与 Window Journal 已核对。
最新 Core DB、Spool、删除 Ledger、两个 Journal 及运行证据完整保留；
新旧聊天进程保持停止。此次批准范围已结束，后续真实动作需要新的明确范围。

## 验收边界

后续离线修复包括开窗前协议检查、命令失败回执保留，以及
[停止窗口的显式交接](p10-stopped-window-handoff.md)。这些修复尚不能替代下一轮实机验收。

本轮结果为 `CORE_RUNTIME_VERIFIED_CAPTURE_BLOCKED_PROTOCOL_NOT_LISTENING`。
它不构成 QQ 端到端验收、真实费用校准、连续健康观察或私人版发布签收。
更早的 [离线集成记录](p10-continuity-operations-integration.md) 与
[审批模板快照](p10-contracts-evidence.json) 保留当时的状态，本页补充本轮实际结果。
