# P3 只读插件安全审查

审查对象是固定上游提交及本轮独立适配层。结论：完整上游 Agent 不适合直接交给 Core 自动探索；
本轮把执行面缩为固定只读调用，复用 Core 的预算、权限、来源依赖与删除合同。
正式环境尚未启用，生产门禁仍单独保留。实际测试见 [p3-validation.md](p3-validation.md)。

## 发现与处置

| ID / 等级 | 固定源码证据与影响 | 本轮处置 |
|---|---|---|
| P3-S01 / 高 | dsh-bili-scroll `index.js:509–525` 的 `bili_reply(force=true)` 可以越过插件自动评论开关；模型能触达该工具时存在公开写入出口 | 正式工具目录没有 reply/DM/upload/login；未知工具及 `force` 参数在 I/O 前拒绝。原生试验实测 hidden write 调用失败 |
| P3-S02 / 高 | `vendor/bilibili-mcp/mcp_server.py:40–53` 在 BILI_MCP_TOOLS 为空时注册全部工具；仅关闭业务开关不足以限制目录 | 原生启动器要求精确非空四项白名单，实际 tools/list 比对；正式适配层不启动原始 MCP 文件 |
| P3-S03 / 高 | 小黑盒 `endpoints.py` 的 comment/create POST、`api_client.py:324` 的 send_comment，与 `adapter.py:226–317` 的通知/主动浏览服务、`main.py` 的 Agent hooks 混合在完整插件中 | 提取纯签名子集；独立源仅编译两个固定 GET 路径，不导入发送、登录、存储、FeedService、平台 adapter 或 main |
| P3-S04 / 高 | 原生 `bili_subtitle` 使用 SDK + aiohttp 下载，没有本文要求的全链路字节/请求上限，返回拼接文本时丢失时间戳；原 comments 可循环翻页 | 复用同一个 ReadBudget、20 秒 worker 时限、原始响应哈希与字幕 spans；评论只允许一页。未把原始工具输出当成 L2 |
| P3-S05 / 高 | 实测旧 bvid+cid player 请求有时给出与目标不匹配的 AI 字幕，且时间段超过目标时长；HTTP 200 本身不足以证明内容归属 | 使用上游的 aid+cid 参数，核对 metadata/player ID、AI 字幕 URL 的 aid/cid、实际使用的字幕时间范围。错配拒绝为 READER_SUBTITLE_TARGET_MISMATCH；早期数量作废，重新实测 |
| P3-S06 / 中 | 两个原始插件都保存长期平台登录态；DSH MCP 子进程继承环境、临时 Home/用户 Profile 混用会扩大权限 | 本轮只用主人新完成的独立登录，凭据存私密文件，版本/期限/开关/指纹检查，B站 Cookie 仅发给 api.bilibili.com，不发 CDN、模型或 Core |
| P3-S07 / 中 | `lib/asr.py` 可自动下载音频、Whisper 模型，未提供本项目要求的大小、CPU、时长与费用边界 | ASR、关键帧、L3 未安装或运行，后续单独验收 |

上游代码定位：[小黑盒固定树](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/tree/90d3c9d4d051f912d80ba02b38d5f8f0a5a95aac)、
[DSH 固定树](https://github.com/c1overzz/dsh-bili-scroll/tree/9a3e0e67d82feedf755c763807c806c4d125a245)。
上述风险描述针对原始组合方式，不表示本轮调用过这些公开写入功能。
GPL SDK 的许可边界另记于 [兼容性报告](plugin-compat-p3.md)，未作为正式依赖引入。

## 输入、权限与证据边界

- 外部文章、标题、评论和字幕只进入 `external_untrusted` 数据上下文。Adapter 不拥有 Goal、
  Diary、Outbox 或长期记忆写入 API；自我探索仍由 WakePlanner/Goal 的事务产生。
- Heybox 限定 HTTPS `api.xiaoheihe.cn` 的 feed/tree；B站限定 view/player/search/reply 的 GET
  及两类字幕 CDN 路径。没有任意 URL 或 HTTP method 透传。DNS 所有答案必须为公网地址，
  连接固定到已检验的 IP，TLS 保留原 hostname；不跟重定向、不解压响应、不无限重试。
- B站搜索混入的 ketang 课程卡片和无效 BV 被跳过；不会把任意链接作为视频目标。
- 账号撤销、文件替换或版本失配在下一次 I/O 前/结果提交前拒绝。平台拒绝触发当前进程的
  grant 熔断；需要操作员修改授权文件才能恢复。异常回执不输出 Cookie、签名参数或异常响应正文。
- 每个外部 GET 消耗 Core 已预留的次数和字节；真正网络不确定沿用 UNKNOWN 与预算保留。
  相同 lease/result 的重复提交只重放收据，不重新读取或发送。
- `account_api` 是 ReadingEvidence 的一个枚举扩展，不新增表；旧默认空字段指纹兼容。
  ArticleEvidence 保留原始响应摘要；分离的图片列表放在正文后按上游列表顺序登记，无法恢复
  的原文插图位置不猜测。最多两张图片引用不构成视觉分析。
- 删除分享根或发现观察会沿 event_dependencies 擦除派生观察、图片引用、兴趣/目标/分享候选；
  从旧加密备份恢复必须重放最新独立 ErasureLedger。本轮真实数据已验证相应闭环。

## 隔离证据与限制

原生 MCP 实验使用单独哈希锁定环境，精确工具白名单及无网络子进程；没有 DSH Profile、
完整 AstrBot、定时器、ASR 或账号文件。正式 Linux Reader 用非 root、只读根和只读源码挂载，
无网络、无能力、no-new-privileges、1 CPU / 768 MiB / 96 PID 实测默认拒绝及工具边界。
运行前后 13 个既有容器 ID、镜像、状态、启动时间逐项相同。

该目标的 cgroup peak 文件不可用，不把采样缺失写成零。Linux Chromium 的既有 sandbox
阻断保留，未添加 no-sandbox、privileged 或修改宿主安全设置；API 读取不依赖浏览器。
Windows 官方网页用于独立人工可视核对，图片/媒体与公共写请求不属于此次核对范围。

六个曾被清理策略拒绝的目录只做只读台账，本轮不删除、不改属主、不重试。
没有触碰在线 Android、Houdini、ADB、生产 QQ/NapCat/AstrBot/DSH，未调用真实 QQ 发送或付费 Provider。
生产 QQ、长期日记、异地恢复与账号长期稳定性仍需独立验收。
