# P4 真实 QQ 私聊验收单

状态 **NOT_RUN / 0 条**。以下是待执行用例，不是实际发送记录。
前提为 P4-0 修复候选审查及新 CI、P4-1 隔离集成通过，再由主人明确授权：独立测试账号、
唯一私聊对象、Asia/Shanghai 起止时段、消息/来源预算、模型费用上限、回退负责人和方式。
没有授权前保持测试发送器、账号登录和付费模型关闭。

| ID | 待执行场景 | 核验重点 | 实际 / trace |
|---|---|---|---|
| QQ01 | 普通问候 | 只有 Bridge 应答；自然度人工判断 | NOT_RUN / — |
| QQ02 | 连续对话 | 顺序、单回复所有权 | NOT_RUN / — |
| QQ03 | 跨轮上下文 | 只引用真正收到的上一轮 | NOT_RUN / — |
| QQ04 | 明确记忆引用 | user/self 主体不混淆 | NOT_RUN / — |
| QQ05 | 小黑盒正常正文链接 | 真实来源进入回复、有范围 | NOT_RUN / — |
| QQ06 | 另一布局/长帖 | 截断透明、图片未识别不冒充 | NOT_RUN / — |
| QQ07 | 失效/限制链接 | 坦诚失败，下一条聊天继续 | NOT_RUN / — |
| QQ08 | B站有字幕视频 | 真实 aid/cid/时间段一致，L2 非看完 | NOT_RUN / — |
| QQ09 | B站无字幕视频 | L1 不冒充视频体验 | NOT_RUN / — |
| QQ10 | B站错误链接 | 无越权出网、普通聊天可继续 | NOT_RUN / — |
| QQ11 | QQ JSON 卡片 | 正确目标，原始卡片不泄露凭据 | NOT_RUN / — |
| QQ12 | 单/双图片与失败降级 | 两槽位；图像模型另需费用授权 | NOT_RUN / — |
| QQ13 | 重复 QQ 入件 | 同 source_event_id 不重复回复 | NOT_RUN / — |
| QQ14 | 独立测试 Bridge 重启/Spool 重放 | 重启前后同一回复意图 | NOT_RUN / — |
| QQ15 | Core/API 断开再恢复 | 入件持久化，人工查看无丢失/重发 | NOT_RUN / — |
| QQ16 | Reader 断开/旧自主任务 | 普通 QQ FIFO 继续，UNKNOWN 不重读 | NOT_RUN / — |
| QQ17 | 暂停/恢复自主活动 | 暂停不抑制主人普通消息 | NOT_RUN / — |
| QQ18 | Quiet Hours/每日额度 | 只有明确授权的主人私聊主动分享 | NOT_RUN / — |
| QQ19 | 发送结果未知 | 人工查 QQ 后核对；不补发 | NOT_RUN / — |
| QQ20 | 请求遗忘+旧加密备份恢复 | 最新独立 Ledger，来源与所有派生项不复活 | NOT_RUN / — |

20 条只是下界，故障场景不足时增加记录；不要强制制造有泄漏风险的真实故障。
每条需记录本地时间、脱敏 trace/message ID、固定代码 SHA、预期/实际投递状态、
来源级别及引用范围、是否仅 Bridge 应答、主人自然度/误引/过度主动评价。
公开材料仅保留脱敏标识和统计；完整私聊/账号/凭据不入 Git、CI artifact 或模型上下文。

`SENDING` 失联后转 `DELIVERY_UNCERTAIN`，只允许真人先核对 QQ，再通过主人命令
`核对发送 <message_id> 已发送` 或 `核对发送 <message_id> 取消` 记录结果。
取消不补发。不要手工把不确定行改回 PENDING，也不把模拟回执写成真实记录。

机器门禁 `real_qq_acceptance` 的每条真实审核记录使用
`id`、`kind="LIVE_HUMAN_ACCEPTED"`、`evidence`（私有证据引用）三个字段；
`real_qq_private_messages_accepted` 必须等于唯一记录数。日记审核另用
`local_date`（YYYY-MM-DD）、`timezone="Asia/Shanghai"` 并按真实自然日去重。
这些字段是审核记录，编辑字段本身不能代替授权或人工验收。
