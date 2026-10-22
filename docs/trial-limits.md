# 试用额度配置

试用样本规模使用一份 TOML。旧窗口的默认值与已冻结计划继续保留；新增窗口可以配置
超过 5 或 15 的次数，不需要修改 Core、Bridge 或测试源码。

```toml
[trial_limits]
max_inputs = 15
max_model_attempts = 15
max_send_attempts = 15
max_visible_replies = 15
max_input_chars = 500
max_reply_chars = 500
```

示例位于 [trial-limits.example.toml](../trial-limits.example.toml)。在仓库根目录运行：

```powershell
uv run python -m scripts.trial_limits trial-limits.example.toml
```

命令只输出生成新草稿所需的三组数值字段，不读取密钥、连接服务、生成授权、开启窗口
或修改账本。部署控制程序也可调用同一个入口：

```python
from scripts.trial_limits import load_limits

fields = load_limits(profile_path).project()
paid_draft.update(fields["paid_model"])
window_draft.update(fields["bridge_plan"])
scope_draft.update(fields["live_text_window"])
```

更新必须发生在冻结新付费合同、Core 配置、窗口计划与批准哈希之前。沿用现有流程
绑定准确身份、候选、绝对起止时间、账本、前驱交接和批准材料。窗口中的 live_text
scope 不包含 plan_sha256；最终 Core 授权对象再绑定完整窗口计划哈希。

## 各项计数

| 配置 | 含义 |
| --- | --- |
| max_inputs | 获准捕获并接收的主人输入数 |
| max_model_attempts | 模型尝试总数，图片理解、记忆复核与聊天分别计数 |
| max_send_attempts | 外部消息发送尝试数；分条的每一条各占一次 |
| max_visible_replies | 获准可见回复条数，不能超过发送尝试数 |
| max_input_chars / max_reply_chars | 单输入与整组回复的字符上限，缺省仍为 500 |

次数相互独立，不按输入数推算模型次数。图片或记忆流程会使用多次模型调用，预算应
据实际流程安排。Core 对已排队回复预占发送槽位，同时受发送次数与可见条数约束；
Bridge 保留两项独立计数及最终派发检查。

次数接受严格正整数，技术范围与现有累计账本一致，为 1 到 2^31−1；字符上限为现有
HTTP 文本合同的 20,000。帧字节、Spool 容量、上下文和 Token 限额仍可更严格。
所有约束取最严者，调大本文件不会覆盖付费合同、累计额度或供应商限制。

## 保留的边界与清理范围

- 认证与主人私聊、独立捕获签名、来源证据、准确窗口绑定、幂等与删除回放继续执行。
- 费用和 Token 的预留、日/月/账户累计额度、暂停与 UNKNOWN 责任继续保留。
  投影只提供 window_calls，不自动提高任何金额、每日次数或并发额度。
- 旧计划和审批内容保持原字节身份；修改额度不能原地扩大旧批准，也不能通过重启、
  换窗口、恢复旧备份或换 Key 清零已发生的责任。
- 单并发、未完成输入的顺序门禁、绝对截止时间和 STOP 继续保留。这些约束涉及当前
  有状态投递合同，不能靠删除参数校验扩大。
- 已清理 5/15 次与 500 字的试用硬上限；普通单条回复也可以声明发送槽位，无需开启分条。
- 最多 3 条分段目前有 SQLite CHECK、消息键和片段协议约束。扩大它需要数据迁移与
  旧版本兼容验证；本轮额度配置不会绕过这些校验。记忆候选数量等模型结构合同也保留。

真实试用沿用用户当前批准的对象、时间和费用范围。一次准备好同一窗口的额度与验收
场景，使用一份冻结计划；范围内的自动检查、记录和停止无需逐步骤重复确认。
