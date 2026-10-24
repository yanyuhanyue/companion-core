# P1–P4 后端与控制台差距报告（草稿）

截至 2026-10-25，本报告是等待最终组合候选的一次性收尾草稿，状态为 **DRAFT / IN_PROGRESS**。
UI 基线是实际 console-v2「能力与开关」页面候选 `0a1b301` 的 41 项能力；不是设计原型。
后端增量按 Root 指定的本地 `fbd9de4 + 95c03eb` 候选记账，两者尚不能写成 main 已有。
Root 提供的状态为 PR #109、#111 CI 已通过、尚未获得合并授权；本文不将 CI 成功当作合并。

**唯一最终集成回执 / CI 摘要：PENDING。** 最终候选冻结后由集成负责人提供一个权威链接，
本报告只引用该链接，不复制多套 CI、commit、tree 身份。现有分片回执只能证明各自范围，
不能替代组合迁移、真实 HTTP 入口与独立 worker 的集成验证。

## 状态如何阅读

| 维度 | 本报告含义 |
| --- | --- |
| 基础后端已实现 | 下表所写的有界原生能力已有实现；不代表完整 P1–P4 扩展、已合入 main 或已部署 |
| UI 已有 / 缺口 | 当前页面能操作、展示什么，以及仍缺少什么；UI 缺口不降低基础后端实现等级 |
| 自主调整接入 | 运行消费者确实读取 `fields` 对应的调整值；可保存授权或提议不等于已接入 |
| OFF / SHADOW / 授权 | 配置与执行约束独立于实现等级；SHADOW 不代表可执行，READY 不授予许可 |
| 真人验收 | 本轮没有提供最终组合版本的真人体验回执，统一为待验收；不推翻已有历史分片证据 |

以下 41 项均按已有基础后端范围标记“已实现”。部署是否可用、当下是否授权和是否已完成
真人验收，须分别读取真实运行状态与回执，不能由这个标记推断。

## 41 项基础能力与 UI 差距

| # | 能力 ID / 名称 | 基础后端范围（已实现） | console-v2 已有 / 仍缺少 | 自主调整 |
| --- | --- | --- | --- | --- |
| 1 | `memory.manage` 记忆管理 | 查询、确认、纠正、忘记 | 已有管理操作；缺有界来源原文、真实实体关系与召回解释 | 未声明 |
| 2 | `memory.recall` 记忆检索 | 有来源约束的检索 | 已有配置；缺召回依据、热温冷解释与调节视图 | 未接入 |
| 3 | `diary` 日记 | 读取、纠正、忘记、暂停 | 已有管理及即时暂停；授权预算调整仍未消费 | 部分 1/2 |
| 4 | `consolidation` 夜间整理 | 原有夜间整理流程 | 已有配置；缺 N1/N2/N3 阶段、进度、检查点与重试视图 | 未接入 |
| 5 | `reflection` 反思 | 有来源支持的反思建议 | Life 可读建议；缺主人会话内接受/拒绝操作 | 未接入 |
| 6 | `goals` 目标与承诺 | 原有目标与承诺记录 | Life 可读列表；缺接受、取消、步骤及执行回执下钻 | 未接入 |
| 7 | `wake` 自主活动调度 | 受限调度与暂停 | 已有局部 decisions 和部分调整；缺完整认知、执行反馈链 | 部分 3/4 |
| 8 | `affect` 情绪观察 | 明确反馈与衰减 | 已有配置；缺来源、衰减、撤回、重置检查视图 | 未接入 |
| 9 | `expression` 表情回应 | 受限选择及投递流程 | 已有配置；缺每次选择、重复控制与效果评价视图 | 未接入 |
| 10 | `expression_learning` 表达习惯学习 | 有来源的表达偏好 | 已有配置；缺偏好/兴趣证据和跨日变化下钻 | 未接入 |
| 11 | `proactive` 主动私聊 | 原有主动私聊与暂停 | 已有模式操作及配置；缺逐次决策与时机反馈追踪 | 未接入 |
| 12 | `social` 社交语义判断 | 有来源的分享候选 | 已有配置；缺候选至表达结果的完整关联 | 未声明 |
| 13 | `reading.web` 网页阅读 | 授权网站读取 | 内容列表可读；缺授权签发/撤回、手动读取；自定义 adapter ID 映射不完整 | 未接入 |
| 14 | `reading.bilibili` B 站读取 | 原有读取与回执 | 内容列表可读；缺授权管理和手动读取；端点由部署保管 | 未接入 |
| 15 | `reading.heybox` 小黑盒读取 | 原有读取与回执 | 内容列表可读；缺授权管理和手动读取；端点由部署保管 | 未声明 |
| 16 | `exploration` 内容发现 | 受限内容发现 | 已有配置和内容列表；缺发现理由、候选及深读回执关联 | 未接入 |
| 17 | `bilibili.video` B 站视频分析 | 受限视频分析 | 已有配置及通用内容证据；缺采样覆盖率和工具结果细节 | 未接入 |
| 18 | `model.understand_video` 视频理解模型 | 仅在本轮实际开放工具时调用 | 共享视频配置；缺开放工具与来源细节；开关不授予付费许可 | 未声明 |
| 19 | `qq.files` 私人文件 | 文件解析与任务记录 | 任务可读；缺上传、重解析与页级证据下钻 | 未声明 |
| 20 | `qq.images` 私聊图片理解 | 认证图片理解 | 已有配置及通用任务视图；缺来源图像证据检查 | 未接入 |
| 21 | `qq.animation` 私聊动图理解 | 受限动图理解 | 通用任务视图；缺帧证据检查，原生无独立动图总开关 | 未声明 |
| 22 | `qq.voice.asr` 语音转写 | 转写任务与节点状态 | 只读节点/任务状态；无转写原文、音频、播放、重试/取消操作 | 未声明 |
| 23 | `qq.voice.tts` 语音回复 | 合成与原投递回执 | 只读节点/任务/投递状态；无音频播放、重试/取消操作 | 未接入 |
| 24 | `qq.poke` 回应拍一拍 | 受限拍一拍回应 | 已有嵌套开关；缺批次和动作检查视图 | 未接入 |
| 25 | `qq.poke.native` 回拍一下 | 独立许可与原生回拍回执 | 已有嵌套开关；缺具体回拍动作及回执下钻 | 未声明 |
| 26 | `qq.account.qzone_publish` QQ 空间发布 | 独立授权发布与回执 | 账号视图只读；缺精确内容/受众/有效期审批及发布对账 | 未声明 |
| 27 | `qq.account.nickname` 改昵称 | 独立许可与回执 | 账号视图只读；缺计划批准、拒绝和撤回 | 未声明 |
| 28 | `qq.account.signature` 改签名 | 独立许可与回执 | 账号视图只读；缺计划批准、拒绝和撤回 | 未声明 |
| 29 | `qq.account.avatar` 改头像 | 已审核素材与回执 | 账号视图只读；缺头像预览及计划批准、拒绝、撤回 | 未声明 |
| 30 | `qq.profile.observe` 资料观测 | 原有资料观测 | 已有配置和只读账号信息；缺证据至后续计划的完整关联 | 未声明 |
| 31 | `unicode.emoji` Unicode 表情 | 保留输入原文 | 已改为无独立开关说明；不再误用表达开关控制输入协议 | 未声明 |
| 32 | `qq.face_names` QQ 表情名称 | 在富媒体范围内识别原生名称 | 已显示真实富媒体范围；无独立开关，还受已批准窗口约束 | 未声明 |
| 33 | `qq.face_images` QQ 表情图像 | 在图片许可内接收认证表情图 | 已改用共享 `content_enabled`；捕获、审批及 Bridge 条件仍由后端核对 | 未声明 |
| 34 | `memes.catalog` 表情目录 | 已审核素材目录 | 已改用真实 `expression_enabled`；当前展示理解任务，缺上传、编辑、审核、隔离流程 | 未声明 |
| 35 | `memes.community_import` 社区表情获取 | 受限素材导入 | 已有配置和理解任务投影；缺导入素材的完整审核、隔离界面 | 未声明 |
| 36 | `memes.search` 表情搜索 | 配置搜索服务的发现路径 | 已有配置和理解任务投影；缺搜索候选至采用结果的完整关联 | 未声明 |
| 37 | `memes.semantic` 表情语义理解 | 有来源的理解任务 | 已有任务状态、原因及详情；缺素材编辑、审核、隔离操作 | 未接入 |
| 38 | `media.voice` 网页语音会话 | 独立媒体会话 | 仅能力/配置；控制台没有启动/关闭会话；独立 media_web 不等于已集成 | 未声明 |
| 39 | `media.camera` 摄像头 | 受限摄像头观察 | 仅能力/配置；缺控制台会话操作与观察证据视图 | 未声明 |
| 40 | `media.android` 安卓设备 | 受限设备观察 | 仅能力/配置；缺控制台会话操作；不代表 Computer Use 已完成 | 未声明 |
| 41 | `executive` 执行任务 | 独立审批执行器 | 缺主人控制台提交、审批、撤回、任务详情；READY 不证明 DSH 实机完成 | 未声明 |

表中“未声明”指没有进入这版 18 项可委托调整清单，不表示基础能力不存在。“未接入”
仅指自主调整消费者未接入，不表示主人配置、原暂停机制或安全停止失效。
表情目录等未完全展开的 UI 流程仍需最终候选逐项核对；本草稿没有重新运行浏览器检查。

## 18 项自主调整的字段与真实接入

基线来自 console-v2 原生 `DELEGABLE` / `WIRED`，共 **18 项能力、38 个字段条目，4 个已接入**。
`owner`、`propose`、`self` 表示主人保留控制、允许提议、允许在范围内自行调整；新授权默认
`owner` 且不允许暂停。调整只能缩小运行范围，不开启主人关闭的能力，也不提高花费。

| 能力 | 可声明的 fields | 已消费的 wired | 尚未消费 |
| --- | --- | --- | --- |
| `memory.recall` | `pause`、`memory_daily_micro_usd`、`extraction_min_events`、`extraction_idle_seconds` | 无 | 全部 4 项 |
| `diary` | `pause`、`diary_daily_micro_usd` | `pause` | 日预算 |
| `consolidation` | `pause` | 无 | 暂停 |
| `reflection` | `pause`、`reflection_daily_max_suggestions`、`reflection_max_daily_calls`、`reflection_daily_micro_usd` | 无 | 全部 4 项 |
| `goals` | `pause`、`goals_max_active` | 无 | 全部 2 项 |
| `affect` | `pause` | 无 | 暂停 |
| `proactive` | `pause`、`proactive_daily_limit`、`proactive_interval_seconds`、`proactive_idle_seconds` | 无 | 全部 4 项 |
| `wake` | `pause`、`wake_max_daily_runs`、`wake_min_interval_minutes`、`autonomous_daily_micro_usd` | 前 3 项 | 自主活动日预算 |
| `exploration` | `pause`、`exploration_daily_candidate_limit`、`exploration_daily_deep_reads`、`exploration_daily_requests` | 无 | 全部 4 项 |
| `expression` | `pause`、`expression_cooldown_minutes`、`expression_selection_probability` | 无 | 全部 3 项 |
| `expression_learning` | `pause` | 无 | 暂停 |
| `memes.semantic` | `pause`、`meme_caption_daily_micro_usd` | 无 | 全部 2 项 |
| `reading.web` | `pause` | 无 | 暂停 |
| `reading.bilibili` | `pause` | 无 | 暂停 |
| `bilibili.video` | `pause` | 无 | 暂停 |
| `qq.images` | `pause` | 无 | 暂停 |
| `qq.poke` | `pause` | 无 | 暂停 |
| `qq.voice.tts` | `pause` | 无 | 暂停 |

页面详情已单独显示接入范围；保存授权、提议或调整，以及列表的火花图标，都不能证明
所有字段已影响运行。日记为部分接入 1/2，调度为部分接入 3/4，其余 16 项尚未接入。

## P1–P4 新增范围、默认模式与验收

| 范围 | 本地实现 / 集成状态 | UI 操作或展示缺口 | 默认模式 / 约束 | 验收状态 |
| --- | --- | --- | --- | --- |
| P1 对话策略与 Affect 重置 | 模块、原生重置入口及来源屏障已有本地候选；不计为 main 已交付 | 缺策略效果、Affect 来源/重置检查面板 | `conversation_policy` 默认 OFF；SHADOW 不修改实际策略输出 | 离线分片已验证；最终组合与真人体验待验收 |
| P1 固定上下文评价 v2 | 新计划和计量来源校验已完成离线分片；原生 evaluation 读取适配由 R1 集成，整体 IN_PROGRESS | 缺创建/取消/结果/真人评分的控制台完整流程 | `evaluation_enabled` 默认 false；36-case 文件只用于离线准备，原生 pilot 仍限六请求 | 合成计划 0 次真实调用；组合 HTTP/独立 worker 待验证，自然度未评价 |
| P2 记忆分层、长期经历与解释 | IN_PROGRESS；按各分片候选收敛，未将完整 P2 计为完成 | 缺热温冷、升降温、Episode/Timeline、共同经历与召回来源解释 | 权限、来源、擦除约束保持；最终配置默认值待候选核对 | 最终组合与真人长期体验待验收 |
| P2 N1/N2/N3 | IN_PROGRESS；原夜间整理存在，分阶段实现与消费者尚在集成验证 | 缺阶段状态、检查点、失败恢复和摘要资格视图 | 新阶段的实际启用条件由最终候选核对；不从旧开关推断已启用 | 最终阶段/恢复链待验收 |
| P3 完整自主认知与 full authority | IN_PROGRESS；已有局部 OwnerAuthority 与 wake/decisions 不代表全消费者迁移完成 | 缺完整候选、SKIP 原因、步骤、等待/放弃、证据及执行反馈关联 | 本地 `owner_runtime` 默认 OFF；SHADOW 不授权执行；既有 Life 默认为 OFF | 全链路、迁移恢复及真人自主行为待验收 |
| P3 REM | IN_PROGRESS；尚无本草稿可据以宣布完成的最终候选 | 缺 opt-in、虚构标记、来源撤回、记录与预算界面 | 保持默认关闭要求；最终实现默认值和撤回行为待核对 | 未开展真人验收 |
| SDK / DSH 执行闭包与 Bridge 精简 | IN_PROGRESS；已有独立执行器/Reader 不等于新 SDK 和所有传输边界完成 | 缺执行审批、撤回、UNKNOWN 对账与工具许可范围展示 | 独立许可与原 Core 权威保持；M0/M1 不执行受限设备/DSH/公开动作 | 最终入口、独立 worker、实机证据待验收 |
| 统一调度与 token Measurement | 原生计量接口及评价来源分片已有本地证据；完整调度/消费者闭包 IN_PROGRESS | 缺对用户有用的资源、排队、前台抢占与计量解释；不要求另造页面 | estimate / exact_local_render / unsupported 分开；本地精确不等于 hosted 计费精确 | 分片验证复用；最终组合待验收 |
| P4 统一表达意图及草稿发布 | IN_PROGRESS；现有社交、账号发布与表情能力不是完整统一表达层 | 缺沉默/研究/日记/创作/私聊/草稿/延后关联，精确受众审批、撤销和发布反馈 | 草稿不自动成为发布许可；公开评论、点赞、身份操作仍需独立授权 | 最终实现和真人反馈待验收 |

这里的 IN_PROGRESS 表示已进入本轮实现/集成工作，不声称每个子模块均已有可运行代码。
虚拟房间等后续范围不作为 P4 完成门槛。真实模型、QQ、账号、设备动作及真人测试仍按原
授权边界进行；不因为配置为 ENABLED、CI 通过或一个 UI 开关打开就推断已获执行许可。

## 标记移除与最终收尾

console-v2 的临时实现标记由 `companion/console_static/app.js` 中单一
`SHOW_IMPLEMENTATION_STATUS` 控制。设为 `false` 可移除列表和详情中的全部实现标记；
此前浏览器证据已确认 41 项能力 ID/原运行状态保持一致，自主调整接入提示仍显示。
运行状态与接入范围具有操作意义，不随临时实现标记删除。

已修正 Unicode/QQ 表情输入及表情目录的错误总开关映射，结论只适用于指定 console-v2
候选。已有手机端返回按钮在特定滚动位置被顶部遮挡的限制仍保留，不将其写成已修复。

本草稿的只读事实来自既有 `console-ui-capability-baseline.json`、console-v2 marks receipt、
实际 `delegation.py` 的字段/消费者声明，以及 Root 指定的候选状态。来源摘要与 41/18 项
逐项核对保存在本工作树 `.local/p1-p4-status-report/source-facts.json`，不作为另一份集成回执。
本轮只新增报告、核对源码事实并运行 Docs 检查；没有改 UI、重跑已通过的浏览器检查或尝试
此前被自动审批拒绝的缓存删除。

收到最终组合候选后，一次性核对：各新模块实际完成范围、默认 OFF/SHADOW、真实消费者、
迁移/擦除/UNKNOWN 与预算保持、UI 缺口变化、真人验收记录；随后替换顶部 PENDING 为唯一
集成回执/CI 摘要链接。没有证据的格子继续保留待验证，不能用分片成功推断整体完成。
