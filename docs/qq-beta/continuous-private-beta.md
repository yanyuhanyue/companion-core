# 72 小时私人连续运行

`window-run` 在一份明确批准、最长 72 小时的固定窗口内接收主人私聊并自动回复。
它沿用 Spool、签名捕获、HTTP v1、模型 admission、Outbox 和独立账本。
窗口身份、来源范围、预算合同、镜像与截止时间在启用前冻结；服务重启不创建新窗口。

## 配置与启动

Core 的 `live_text_window` 和 Bridge 计划中的 `live_text` 都必须显式包含
`automatic_reply = true`，并绑定相同的批准证据。缺省仍为逐条审阅；旧合同序列化不变。
新配置仍须完成原生 stopped-window handoff 与 open，保留全部前驱账本和删除记录。

```sh
python -m companion_onebot_bridge --config /private/bridge.toml window-run \
  --window /private/plan.json --inventory /private/inventory.json \
  --authorization /private/authorization.json
```

范围内的每条输入和回复由已批准的自动策略生成精确材料摘要，不伪装成人工逐条审阅。
原生输入签名、主人身份、Core 来源复验、租约、发送 fence 和结果确认仍在实际 I/O 前执行。
普通 daemon 不接管带有受控窗口账本的目录。

## 费用与资源

默认付费合同继续要求日、月、窗口金额。只有明确批准无周期金额上限时，才使用：

```toml
[paid_model]
unlimited_period_spend = true
```

此时省略 `daily_micro_usd`、`monthly_micro_usd`、`window_micro_usd`。
同时填写这些金额会被拒绝。该选项解除此聊天供应商的日/月/窗口及相关场景周期金额限制，
不会绕过累计账户合同；已有累计金额限制时须使用其明确的接续流程，不能同时设置此选项。

每次调用及每个逻辑请求的金额、Token、调用次数、单并发、输入/回复数量、图像容量和
Reader 网络资源仍独立检查。所有模型调用照常预留与结算，UNKNOWN 保留责任并停止继续派发。
结算使用调用时冻结的合同；之后修改配置不改变已发生费用的归属。
金额按配置费率计算，不代替供应商账单或账户余额。只读运维状态会显示
`budget.unlimited_period_spend`。

## 接收、恢复和停止

WebSocket 在模型工作期间保持连接。收到的主人消息在同一 Spool 内形成有界签名缓冲，
保存原始接收时间；缓冲签名验证后才进入原有捕获和批准流程。最多保留 32 条待处理输入，
并受 Spool 总容量限制。连续窗口的独立日志容量为 32 MiB；历史短窗口仍保持原容量。
重复平台消息不增加模型或发送次数。其他用户和群消息不会进入 Core。

每次仍只处理一个输入。捕获或上传已完成的输入可继续处理；发送已经开始但结果不明、
费用 UNKNOWN、来源变化或账本不一致会停止窗口，不能自动重发或清空责任。
空闲接收不写入 CAPTURING 状态，因此普通服务重启不会把空闲误判为未完成捕获。
SIGTERM 在当前输入处理结束后暂停服务，保留已签名的接收缓冲及原定截止时间。

`window-status` 使用只读数据库连接，运行中的接收器不阻塞状态查询。
`window-stop` 先写持久停止标记；运行中的接收器确认 Core 停止后退出。
截止时间也由 Core 和 Bridge 在派发边界检查。部署应另外配置独立的到期停止任务，
以覆盖进程退出或整机重启；该任务停止 Worker、Reader 和接收器，不回滚数据。
STOP 会清除本窗口尚未提交的接收缓冲，保留既有输入、发送和费用回执。

自动运行沿用所选消息、图片、近期历史及已确认事实的原有范围合同。
功能开关不会因为常驻运行而自动扩大；支持的组合与协议容量见
[试用额度配置](../trial-limits.md)。

## 验证范围

回归测试使用真实回环 WebSocket、Core HTTP 边界和 SQLite/Spool，模型及 QQ 供应商由
MockTransport 替代，覆盖连续消息、重复输入、断线、空闲暂停/恢复、签名缓冲篡改、
已捕获输入恢复、分段发送、未知投递、STOP 和到期。真实部署的启停、费用及主人体验另行记录。
