# 定时表情包导入

## 功能说明

每天自动扫描主人的私聊历史消息，将包含图片的消息导入到表情包发现队列，复用现有的 discovery 预算和策略。

## 工作原理

- **触发时间**：每天凌晨 03:30（本地时间）
- **扫描机制**：复用 bridge 的 `history_backfill` 功能
- **预算管理**：使用现有的 `discovery_daily_budget`，不修改账号策略
- **去重保证**：Core 自动识别重复素材，不会重复导入

## 配置

在 `bridge.toml` 中添加或修改：

```toml
# 启用定时导入（默认 true）
scheduled_meme_import_enabled = true

# 必须启用历史回填功能
history_backfill = true

# 表情包功能需要开启
meme_enabled = true
meme_discovery_enabled = true
```

## 禁用定时导入

如果需要临时关闭，设置：

```toml
scheduled_meme_import_enabled = false
```

重启 bridge 后生效。

## 状态追踪

导入状态记录在 `companion_onebot_bridge/state/meme_import_state.json`：

```json
{
  "last_scan_time": "2026-10-31T03:30:00+00:00",
  "total_runs": 15
}
```

- `last_scan_time`：上次扫描时间
- `total_runs`：累计执行次数

## 日志监控

每次扫描会记录日志：

```
INFO: Starting scheduled meme import via backfill (run #15)
INFO: Scheduled import completed: 3 messages backfilled
```

失败时会记录错误：

```
ERROR: Scheduled import failed: <error details>
```

## 注意事项

1. **预算限制**：如果当天 discovery 预算已用完，扫描会停止
2. **不影响即时回复**：定时任务在后台独立运行
3. **回填窗口**：只扫描最近 20 条历史消息（backfill 的默认设置）
4. **首次运行**：会尝试导入历史消息，后续每天增量扫描
5. **失败处理**：单次失败不影响次日执行

## 手动测试

修改定时时间为 1 分钟后：

```python
# 在 scheduled_import.py 中临时修改
schedule.every().day.at("15:35").do(job)  # 改为当前时间+1分钟
```

观察日志输出验证功能正常。

## 依赖

- `schedule>=1.2,<2` (已添加到 pyproject.toml)
- 已配置的 OneBot 实例
- Core API 可访问

## 与其他功能的关系

- **history_backfill**：共享底层回填逻辑
- **meme_discovery**：共享预算和去重机制
- **continuous replies**：不冲突，独立运行
