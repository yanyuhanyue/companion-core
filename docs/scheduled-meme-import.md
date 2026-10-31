# 定时回填与表情包入库

`scheduled_meme_import_enabled` 保留每日历史回填任务。它补收 WebSocket 断线期间遗漏的
主人消息，复用原签名捕获和 Core 接纳；素材获取、识图与发送由 MemePump 和 Core 完成。

回填仍受当前主人授权、最近 20 条记录和原 30 秒新鲜度限制。它不会把旧聊天图片或
`/memes` 下的文件直接变成可发送素材，也不会为缺少来源证明的图片创建识图任务。

## 配置与时间

```toml
scheduled_meme_import_enabled = true
history_backfill = true
meme_enabled = true
meme_semantics_enabled = true
meme_discovery_enabled = true
```

任务每天在 Bridge 进程本地时间 03:30 运行。生产容器使用 UTC 时，对应北京时间 11:30；
Core 的 `timezone` 不会改变 Bridge 进程时区。配置修改后按现有发布流程重启 Bridge。

## 素材入库与发送

当前已认证的主人消息包含支持的表情意图，且没有可用候选时，后台通过原 discovery
任务从已批准的社区来源获取图片。语义目录为空时，即使前台候选查询失败，原回复完成后
也能从同一条已接纳消息启动发现，不会让空库一直等待一次成功的前台查询。

图片经过来源与许可审核、下载用量结算、Core 原生 caption job、模型费用结算和本地
索引后，才可在后续聊天轮次进入候选。发送仍经过原 Outbox、概率、冷却和平台 ACK。
预算不足、来源不符、没有合适候选或主人拒绝图片时继续文字回复。

详细合同见[表情入库识图与聊天检索](qq-beta/memes-semantic.md)。

## 回填状态

成功回填状态保存在 Bridge 可写的 `data_dir/meme_import_state.json`，不写只读安装目录：

```json
{"last_scan_time": "2026-10-31T03:30:00+00:00", "total_runs": 1}
```

这只说明历史回填完成，不证明素材已入库或图片已发送。回填返回 `UNAVAILABLE` 时保留
上次成功状态。实际验收应分别检查 discovery 结算、caption 成功与费用、语义素材和
向量，以及图片 Outbox 的平台 ACK。
