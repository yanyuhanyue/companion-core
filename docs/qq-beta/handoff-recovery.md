# 停止窗口的交接恢复

私聊撤回可能先于原消息入站：Core 已保存真实签名输入批准和独立撤回账本，而原事件、任务和回复均不存在。交接允许这种已封闭输入，但必须同时验证原窗口签名、主人／协议批次／消息标识、独立账本中的 `RECALLED` 记录、已应用的完整记录和两个删除标记。只有一个来源标记、待匹配通知或普通遗忘不能代替该证明，也不会重建已删除消息。

## 撤销尚未准备成功的交接

Bridge 在请求 Core 前先持久化 `HANDOFF_REQUESTED`。如果随后没有拿到确认，保持 `HANDOFF_UNCONFIRMED`，不得重新开启旧窗口、改写日志或直接换一个后继窗口重试。

`POST /internal/v1/live-text/handoff/abort` 沿用内部 Bearer 身份，接收严格的两个字段：

```json
{
  "window_sha256": "失败后继计划的64位SHA256",
  "bridge_journal_head_sha256": "该HANDOFF_REQUESTED记录的64位SHA256"
}
```

这是撤销入口。它只在原窗口已停止、费用与投递全部封闭、原独立账本保管关系一致，并且后继从未创建窗口额度、模型准入、输入批准或 `PREPARED` 记录时成功。撤销允许代码版本变化或计划到期；普通打开、准入和发送的版本校验不变。

Core 通过原生预算账本追加一次同一原窗口的 `window_stop`，推进独立日志头。旧后继计划仍绑定旧日志头，因此即使数据库回滚，也不能再次准备该计划。Core 返回 `ABORTED_BEFORE_PREPARE`，同时绑定失败计划、原计划、交接范围、原预算头、新预算头、隐私版本、遗忘账本摘要及请求中的 Bridge 日志头。相同请求只返回已保存回执；其他日志头或已经使用过的后继拒绝撤销。日志已落盘而数据库未提交的情况仍要求账本核对，不自动补写。

Bridge 只有在本地完整记录及这个真实 Core 回执全部吻合时，才追加 `HANDOFF_ABORTED` 并把原窗口投影恢复为 `STOPPED`。原消息、计数、费用和日志记录保留。随后重新生成经批准的新计划，使用已经推进的两侧日志头；不能复用被撤销的旧计划。

部署负责人保持工作进程停止，使用失败后继的原计划文件执行：

```bash
python -m companion_onebot_bridge --config /run/bridge.toml window-abort-handoff --window /run/failed-successor.json
python -m companion_onebot_bridge --config /run/bridge.toml window-status
```

撤销命令读取已保存的计划，只访问 Core；它返回上述九字段回执。新计划生成器读取保留的 Bridge 日志及其已核验的最新日志头。Core 回执丢失时，相同撤销请求可取回原回执；本地日志已落盘但数据库提交失败时继续要求核对，不能手工改写状态或重复追加记录。

Core 已经 `PREPARED`／打开、存在未决模型或投递、来源或账本变化、任何回执不一致时都不适用此流程。此入口不发送 QQ 消息，不调用模型，也不退还费用。
