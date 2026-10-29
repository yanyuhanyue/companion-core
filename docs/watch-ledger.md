# 观看账本

B站视频或小黑盒帖子读完后，她会对每条内容记一笔自己的看法：打分（-2 到 2）、不超过 60 字的一句话看法，以及最多 3 个话题标签。聊天时，她可以引用最近的这些记录，也可以主动提起。设计由主人于 2026-10-29 确认：使用模型判断，保留期跟随来源，允许主动提起，小黑盒也记入。

## 流程

1. 生活 Worker 的 `watch` 通道在 `exploration` 授权下运行。它扫描最近 `lookback_hours` 小时内状态为 FETCHED 或 WATCHED 的 `observation.bilibili` / `observation.heybox` 观察，给还没有记录的内容加一条 `PENDING`。同一个链接只记一次。
2. 读取深度只根据证据判断，不采信模型的说法：
   - 有视频分析为 `media_sampled`；
   - 有字幕为 `subtitle`；
   - 有评论为 `comments`；
   - 有正文为 `text`；
   - 否则为 `title_only`。`title_only` 不调用模型，直接 `SKIPPED`。
3. **补评：** 同一个链接之后来了读得更深的观察（例如正文读完后补上的视频分析）时，已结束的记录会改为指向新观察，重新判断一次。读得更浅的观察不会触发重评；正在调用或调用结果不明（UNKNOWN）的记录保持不动。
4. 每条记录调用一次 `watch_review` 场景。
   - 这是一次后台调用，不绑定任务，使用 JSON 模式，输出上限 `max_output_tokens`，每日上限 `daily_micro_usd`。
   - 调用沿用原有的预算、OwnerAuthority 和 UNKNOWN 规则；有主人消息待处理时让路。
   - 资料按数据传入，资料中的要求不执行。
5. 失败处理：
   - **准入被拒且未发出请求：** 记为 `RETRY_WAIT`。超出每日上限时等到次日。
   - **输出格式不合法：** 用新请求重试，最多 `max_attempts` 次。
   - **计费不明：** 记为 `UNKNOWN`，永不重放。
   - **来源变化或被删除：** 记为 `CANCELLED`。

## 删除

`erasure.py` 注册了 `watch-ledger` 清理器。来源观察被遗忘、来源授权撤销或原始事件到期时，对应记录一并删除，旧备份恢复后会重放删除。聊天引用只读取来源仍然有效的记录。

## 启用

默认关闭。关闭时付费合同快照不变，部署本功能属于发布预检的 A 或 B 类。启用会把 `watch_review` 的每日上限写进付费合同快照，因此需要新的 OwnerAuthority policy，其中包含 `exploration` purpose，属于 C 类。可以和加入 `evaluation` purpose 的授权修订合并进行。
