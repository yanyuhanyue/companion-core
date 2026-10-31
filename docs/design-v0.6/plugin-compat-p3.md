# P3 插件只读兼容性与运行合同

任务：CC-NEXT-20261020-P3V2。2026-10-20 开工重新查询 GitHub；本文件描述独立开发分支，
不表示下面的代码已经进入 main。实测数量与门禁见 [p3-validation.md](p3-validation.md)。

## 冻结基线

| 对象 | 完整 HEAD | base / 状态 |
|---|---|---|
| main | `676f6143a40d08ffe44862a8897b4953dfa6c02e` | 正式主分支 |
| PR #11 / release/v0.5 | `aee7b16fd4808bb78597f68e79836d146cc00e5b` | main / OPEN |
| PR #12 / fix/v05-release-gates | `89c60f08ab4707f169d7fc268fc7d72a55faec78` | release/v0.5 / OPEN |
| PR #13 / feat/v06-source-browser | `c74cae69022fa09543a4e59209002c5282784140` | fix/v05-release-gates / OPEN |
| PR #14 / feat/v06-heybox-reader | `058cb62d7323ad23195a2077b96ede36fd24a0d2` | feat/v06-source-browser / DRAFT OPEN |

开工各 PR 的检查数为 6 / 6 / 8 / 8，全部成功；没有 reviews。没有合并、强推或改动这些 base。
本轮分支 `codex/v06-plugin-readonly-p3` 直接基于 #14，新增 Draft PR 以该分支为 base。
#11 的旧 HEAD 不包含 #12 的修复，发布顺序仍需单独决定。

## 实际审计的上游

| 项目 | 固定提交 / 版本 | 许可与使用方式 |
|---|---|---|
| [AstrBot 小黑盒适配器](https://github.com/674537331/astrbot_plugin_xiaoheihe_adapter/tree/90d3c9d4d051f912d80ba02b38d5f8f0a5a95aac) | `90d3c9d4d051f912d80ba02b38d5f8f0a5a95aac` / 1.3.3 | MIT；提取纯 GET hkey 子集，参考端点、参数和正文形状 |
| [dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll/tree/9a3e0e67d82feedf755c763807c806c4d125a245) | `9a3e0e67d82feedf755c763807c806c4d125a245` / 0.1.14 | 项目及 vendor MCP 为 MIT；参考四个只读工具及搜索字段 |
| heybox-core | `2fd1bbed98c4f82a99d432048e6d2f25bfaeb0a9` | 上游 notices 标注 MIT，hkey 的转引来源 |
| heybox-bot | `c2b5797c69d54439a77a4f0e7e54e88567e07c75` | 上游 notices 标注 MIT，Web 客户端合同的转引来源 |

完整署名和 MIT 文本随包保存在 `source_reader/THIRD_PARTY_NOTICES.txt`。
独立原生试验实际解析出的 `bilibili-api-python==17.4.2` 标注 **GPL-3.0-or-later**，
不能把它笼统归为 MIT。它仅安装在隔离的 native-probe 环境；正式 Reader 不导入或分发该 SDK。
正式依赖和 `uv.lock` 没有增加这个包或 MCP。离线试验的完整依赖与发行包哈希见
[upstream-native-p3.lock](upstream-native-p3.lock)，包括 MCP 1.30.0、HTTPX 0.28.1、
aiosqlite 0.22.1、pytest 9.0.2、pytest-asyncio 1.3.0。

`api_client.py` 可以独立导入，但携带登录、评论发送、默认 GET 重试和宽泛客户端；正式读取没有整体导入它。
`feed_service.py` 依赖 Repository、模型事件分发及 reviewed delivery；它不是可以直接接入 Core 的纯读取函数。
`topic_service.py` 的 GET 辅助逻辑可单独研究，本轮两个 Heybox 能力只用推荐流和帖子树。
DSH 的 `sdk-minimal` 仍使用一次性 Home，不加载 Desktop/Web Profile。两个完整平台 Agent 都未启动。

## 静态能力映射

| 能力 | 实现与输入 | 外部 GET / 单次上限 | Core 接入 |
|---|---|---|---|
| heybox.feed.read | NativeHeyboxSource；首屏、最多 40 条 | `api.xiaoheihe.cn/bbs/app/feeds`；1 请求 | discover → DISCOVERED |
| heybox.post.read | NativeHeyboxSource；规范帖子 ID | `api.xiaoheihe.cn/bbs/app/link/tree`；1 请求 | read → 正文、ArticleEvidence、最多 2 图片引用 |
| bilibili.search.read | BilibiliTools；操作员固定关键词 ≤100 字、最多 20 视频 | `/x/web-interface/search/type`；1 请求 | discover；跳过课程卡片、无效 BV 和重复项 |
| bilibili.subtitle.read | 复用 BilibiliReader；单个规范 BV / 首分 P | view、player、字幕 CDN；通常 3 请求，上限 4 | ReadingEvidence；可用才 L2 |
| bilibili.comments.read | 单个 BV、最多 20 条、仅首屏 | view、`/x/v2/reply`；2 请求 | 诊断工具输出；本轮没有自动写入 Core 内容/经历的触发器 |
| bilibili.video_info | 单个 BV，关闭字幕步骤 | view，必要时页面回退；最多 2 请求 | L1 元数据 |
| bilibili.audio.transcribe | 未实现、未安装、未启用 | 无 | 后续独立资源/授权门禁 |

这是固定目录，未添加通用插件扫描、动态工具透传或第二个 Planner。
所有新模式均需显式 CLI 参数或 Compose profile。每次读取重新校验账号授权文件的版本、
期限、开关及指纹；平台凭据留在独立 Reader，Core 只接收已有 lease/result。
Bridge 继续仅经 HTTP v1 传输私聊；模型适配器没有增加存储、权限或发消息职责。

单次 worker 最长 20 秒、最多 1 MiB、最多 8 请求，实际由 Core lease 的更小额度约束；
本轮 live 验收配置为每条 512 KiB，Heybox 1 请求、B站 4 请求。源层逐 GET 消耗同一 ReadBudget，
不存在 facade 内部隐藏多次请求。网络不确定仍为 UNKNOWN，实际用量为空并保留预留，不重放 I/O。
账号 API 明确使用 `account_api`；已有公开路径和旧幂等指纹保持兼容，没有数据库迁移。

## 可重复的原生试验

在工作区 `.local/p3/upstream/{heybox,bili}` 检出上表 SHA，创建独立 Python 3.12 环境。
`uv pip sync --require-hashes docs/design-v0.6/upstream-native-p3.lock` 必须指向该独立环境。
运行该环境的 Python：

```text
scripts/verify_plugin_native.py --upstream-root .local/p3/upstream
```

脚本核对 HEAD 与已跟踪文件，拒绝带账号文件的 MCP 试验目录。
真实 stdio initialize → tools/list 返回四个只读工具；`bili_reply(force=true)` 调用被拒绝，
未登录字幕读取失败。子进程禁止 socket connect/getaddrinfo/sendto；Windows 只在导入上游之前
建立事件循环自己的 socketpair。输入使用显式最小环境，不继承 DSH Home 或用户 Profile。
这证明原生客户端和 MCP 注册合同，不等同于完整 AstrBot/DSH 平台安装验收。

原版小黑盒的六组 fixture 测试共 58 项通过；真实源测试另列，不混入这个数量。
Linux CI 会重建独立环境、重跑 tools/list 与这 58 项测试。

## 显式启动、授权与回滚

```text
python -m source_reader --source heybox --heybox-native --account-file <private-grant.json> --core-url <private-origin> --adapter-id <heybox-id>
python -m source_reader --bili-tools --account-file <private-grant.json> --search-keyword 独立游戏 --core-url <private-origin> --adapter-id <bili-id>
```

`COMPANION_READER_TOKEN` 仍为该观察适配器专用 token；不使用 owner control、Bridge 或 QQ token。
账号授权文件由操作员在独立登录会话中提供，最大 8 KiB。共同字段为 `enabled`、有时区的
`expires_at`、`cookies` 和精确 `upstream_commit`；Heybox 另需 `account_id` 与 32 位十六进制
`device_id`，B站要求 SESSDATA。仓库没有保存凭据示例值，也没有自动提取既有 Cookie 的路径。
Linux 文件只允许属主访问，运行用户需能读取；Windows 使用工作区私密目录的 ACL。
读前、读后检查撤销/身份变化；账号文件失效后不回退到别的账号或 Profile。

Compose 的 `heybox-native`、`bilibili-tools` profile 默认关闭，账号文件为只读单文件挂载，
不存在则启动失败；不挂载 Core DB、ErasureLedger、QQ 卷或 Docker socket。
配置只授予 1 CPU / 768 MiB / 96 PID、UID 10003、只读根、cap_drop ALL、no-new-privileges。
源错误只结束该读取任务，普通聊天通过自己的 FIFO 继续。

回滚：停止新增 Reader，撤销其 Core 观察 token 或关闭观察/探索开关，将本地 grant 的
`enabled` 设为 false。保留原聊天 worker、Outbox、UNKNOWN 预留和独立 ErasureLedger。
不清数据库、不降级 0014、不修改既有 PR 历史。换上游版本前重做许可、工具目录、端点和实测审查。

## 2026-10-31：账号推荐流发现

主人选定的本轮范围是账号推荐流与离线／影子表达评估。
推荐流仍经过独立 Reader 的 Reading v1 HTTP 边界；表达评估的入口与人工尺度见
[CC-STYLE-04 增量](../experiments/bili-style/04-evaluation.md#2026-10-31近期表达重复评估)。

### 上游增量与复用

本次只读检查 `dsh-bili-scroll` 提交 `2c7b3f14f525360f8657b102ec453fd8f02baf3a`，
该树的 `package.json` 为 2.3.1。这里记录的是新的参考基线，前文 P3 验收记录仍对应原固定树。

| 来源 | 核实内容与 Core 采用范围 |
|---|---|
| [上游 MCP 源码](https://github.com/c1overzz/dsh-bili-scroll/blob/2c7b3f14f525360f8657b102ec453fd8f02baf3a/vendor/bilibili-mcp/mcp_server.py) | `bili_recommend` 调用 `homepage.get_videos`，筛选普通视频；Core 参考发现来源和卡片形状 |
| [上游值班说明](https://github.com/c1overzz/dsh-bili-scroll/blob/2c7b3f14f525360f8657b102ec453fd8f02baf3a/prompts/duty.md) | 推荐发现、近期表达参考与有限故事板读取；本轮实现前两项，故事板属于已有独立视频能力的范围 |
| [SDK 17.4.2 发行包](https://pypi.org/project/bilibili-api-python/17.4.2/) | 复核 P3 隔离环境已锁定的发行包中 `bilibili_api/homepage.py` 与 `data/api/homepage.json`：推荐端点是 `/x/web-interface/wbi/index/top/feed/rcmd`，使用 WBI 签名 |

推荐流实现使用现有 HTTP 客户端和有界请求预算。WBI 签名从 `bili_media.py`
抽到无 Core 依赖的 `source_reader/bili_signing.py`，两个调用方共用；其已有参考来源见
该模块注释。没有添加或分发上游 SDK；正式依赖与锁文件不变。

### 配置与行为

在现有 B站适配器配置中明确选择推荐流，继续使用原有 token：

```toml
[observation_adapters.bili]
source = "bilibili"
# token 沿用本适配器已有值
source_grant_required = true
reader_max_requests = 2
bilibili_discovery = "recommend"
```

独立进程启动方式：

```text
python -m source_reader --source bilibili --protocol reading-v1 --account-file <private-grant.json> --core-url <private-origin> --adapter-id bili
```

`--bili-tools` 模式也接受 Core 明确下发的推荐流选择。来源授权文件、Core 中注册的读取授权、
观察／探索开关、OwnerAuthority 和探索额度仍分别生效。登录状态和 WBI 参数一次 GET，
推荐列表一次 GET，共用同一租约、字节和请求预算，最多返回 20 条普通视频；过滤直播、
番剧、广告、无效 BV 与重复链接。无可用视频返回 `DISCOVERY_EMPTY`，不生成阅读经历。

`bilibili_discovery` 默认 `popular`；默认值不增加既有配置序列化字段。
已有兴趣选择的搜索请求继续携带 `query`；无搜索词的发现才使用配置选定的推荐流。
因此现有兴趣搜索与探索分配可以继续工作，推荐画像本身不被当成兴趣多样性的保证。

迁移 `0058_bili_discovery` 在请求中冻结 `discovery_mode`；历史请求保持 `default`。
新配置不把排队中的热门发现升级成账号推荐。推荐请求只交给 Reading v1 Worker；
关闭推荐配置会使尚未执行的推荐请求取消，在途请求在下一次验证时停止。
每次外部 I/O 继续验证原租约与原授权；账号失效后本次发现结束。

返回结果只有标题和链接，使用 `DISCOVERED/link_and_title`，证据记录
`discovery_mode=recommend`，来源归属保留实际 `source_grant`。Core 将该标记与原请求核对。
随后阅读字幕、形成兴趣或分享仍由既有流程决定；发现列表不证明看过视频。

### 验证范围

`tests/test_bili_recommend.py` 覆盖账号失败、协议拒绝、卡片筛选、搜索并存、冻结来源，
以及真实 Core HTTP 与独立 Reader CLI 的两种运行模式；外部 B站响应为合成输入。
独立进程不导入 Core 存储依赖，完成后再次领取不会重读或产生消息。
现有视频、Reader 授权／擦除、探索、迁移与风格评测作为相关回归检查。
真实账号推荐质量尚未验收。
