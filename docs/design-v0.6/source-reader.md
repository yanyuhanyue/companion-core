# B站独立只读 Reader（PR-A）

基线：`89c60f08ab4707f169d7fc268fc7d72a55faec78`（PR #12 修复候选）。
当前为 v0.6 开发增量；版本字段仍沿用 v0.5.0，不代表 v0.6 已发布。
验证记录见 [source-reader-validation.md](source-reader-validation.md)。

## 数据路径与读取范围

`source_reader` 是单独进程，经现有 `/internal/v2/readers/{adapter_id}/lease` 和
`/{request_id}/result` 与 Core 通信。Core 独占存储、预算、兴趣、目标、私人日记及 Outbox；
Reader 不导入 Core/Bridge，不接触数据库、模型密钥、主人身份或消息投递。

- L1：公开 API 的标题、UP主及简介。API 成功但简介为空时，尝试公开静态页面；可显式启用离线 Chromium 渲染页面内联数据。
- L2：公开播放器接口允许访问的第一分P字幕，优先中文，最多 120 段、8000 字符。记录每段起止时间，保留间隙和截断标记。
- 首页发现结果是 `DISCOVERED`。标题和实际简介／字幕读取成功才形成 `FETCHED`。
- 输出包含 L1/L2、第一分P、字幕状态和读取方法。没有图像／音频分析及完整视频覆盖，永不输出 `WATCHED`。
- 本增量不启用付费关键帧、转写、评论采样或账号登录。L3/L4、完整动态网站浏览另行实现和验收。

字幕状态为 `available`、`missing`、`restricted`、`unavailable`、`budget_limited`。
公开响应的 `need_login_subtitle` 也属于受限；不会将登录要求描述为视频没有字幕。
元数据访问受限时立即失败；字幕受限时可返回已取得的 L1，并明示字幕未读取。

## 证据、预算与失败

`ReadingEvidence` 保留抓取时间、层级、方法、分P、语言、字幕时间段，以及各成功响应的
SHA-256、原始字节数和去查询参数的来源 URL。签名字幕查询参数只在任务内使用。
内容始终作为不可信观察数据进入已有上下文，不产生工具许可。
依赖关系沿现有 Observation / exploration_evidence 传播；删除和恢复旧备份后重放最新
ErasureLedger 会清除正文及读取证据。

迁移 `0014_reader_budget` 为每个任务增加 `reserved_requests`、`used_requests`。
旧适配器默认预留 1 次请求；新 Reader 建议配置 4 次。Core 在租约事务中预留整个任务的
请求数与字节预算，结果结算实际用量。层级读取不能绕过每日／每月请求限额。
网络或结果回执未知时不重放外部请求；Core 保留 UNKNOWN／过期租约的完整预留。
旧 v0.5 result/observation 幂等指纹保持兼容。

每个任务最多 20 秒、8 次请求、1 MiB；以下示例进一步降为 4 次和 512 KiB。
外部响应只接受未压缩数据，拒绝跳转、压缩炸弹和超额内容。
DNS 每次解析后检查所有地址，只连接已校验的公网数字 IP，保留原始 Host、TLS SNI 和证书验证。
只允许固定 B站页面／API 路径及两种字幕 CDN，拒绝内网、元数据、混合公网／私网 DNS 响应。
不继承 HTTP 代理、Cookie 或 Core 请求头。

## 隔离环境启用

在独立测试数据库中执行已有 `python -m companion init` 完成迁移；迁移前按现有手册备份数据库
和独立遗忘日志。以下值是小额开发配置示例，应合入已有配置，不能直接覆盖主人配置：

```toml
observation_enabled = true
exploration_enabled = true
content_enabled = true
exploration_daily_requests = 20
exploration_monthly_requests = 100
exploration_daily_deep_reads = 5
exploration_max_response_bytes = 524288
exploration_daily_bytes = 2621440
```

在 Core 服务环境的 `COMPANION_OBSERVATION_ADAPTERS` JSON 中，给 B站适配器配置：

```json
{"bilibili_source":{"source":"bilibili","token":"<独立随机密钥，至少24字符>","reader_max_requests":4}}
```

Reader 环境单独配置 `COMPANION_READER_TOKEN` 为该适配器密钥。
不得复用 Bridge／owner token。Core 中同一来源只配置一个实际领取该队列的 Reader；
使用此进程时关闭 Bridge 的 `bilibili_reader_enabled`，避免旧 Reader 竞争租约。
按需保留其他来源适配器的现有配置。所有功能在默认配置中仍关闭。

```powershell
uv sync --frozen --group reader
# 本地命令只使用私网 Core origin；不将 token 放入命令参数。
.venv/Scripts/python.exe -m source_reader --core-url http://127.0.0.1:8765 --adapter-id bilibili_source
# 单次领取使用 --once；公开单链接检查不连接 Core，也不写观察记录：
.venv/Scripts/python.exe -m source_reader --probe https://www.bilibili.com/video/BV1GJ411x7h7
```

Core 的 HTTP origin 仅接受 localhost／私网 IP；HTTPS 可使用受信任的服务域名。
Reader 只需要 httpx/httpcore；Playwright 是可选 reader 依赖组，不在 Core 默认安装路径。

## 可选 Chromium 与容器

`--browser` 默认关闭。显式启用前安装与锁文件一致的 Chromium：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/.local/playwright-browsers"
uv run --group reader python -m playwright install chromium --only-shell
```

浏览器只渲染经 PublicHTTP 取得的有界 HTML。单 context、单页面串行，5 秒渲染时限；
每任务销毁 context 和浏览器。首次文档由 route.fulfill 提供，其余导航、资源和 WebSocket 均阻断；
禁用 Service Worker、下载和权限，context 离线，CSP 限制连接、frame、表单和对象。
浏览器子进程只得到少量系统环境变量，不接收 Core／模型／SSH 凭据。
离线渲染不能支持依赖外部 JS 或登录的网站；受限目标如实失败。
Chromium sandbox 始终启用；宿主机不支持安全启动时返回不可用，不关闭宿主安全设施。

`deploy/source-reader.Dockerfile` 构建独立非 root 镜像，只复制 Reader 源码。
Python 基镜像固定 digest，uv/Playwright 固定版本，Python 依赖按锁文件哈希安装。
`deploy/compose.reader.yaml` 是 opt-in profile：只读根文件系统、移除 capabilities、
no-new-privileges、1 CPU、768 MiB、96 PID、128 MiB 临时工作区和 32 MiB `/tmp`。
不暴露端口、不挂载 Core 数据卷或 Docker socket。

在专用开发网络内设置 `COMPANION_READER_CORE_URL`、`COMPANION_READER_NETWORK`，
并将 Reader 专用密钥写入本地受限权限的 `privacy/reader.env`，再运行：

```sh
docker compose -f deploy/compose.reader.yaml --profile reader up --build bilibili-reader
```

容器示例默认只启用 API/静态页面。Windows 已运行真实 Chromium 隔离测试；Linux 容器中
Chromium 的 sandbox 运行能力必须针对部署主机独立验证，不能由镜像构建成功推定。
限额是配置硬上限，不是测得的常态或峰值资源消耗。

实现参考：[Playwright BrowserContext](https://playwright.dev/python/docs/api/class-browsercontext)、
[网络路由与 Service Worker](https://playwright.dev/python/docs/network)、
[容器隔离建议](https://playwright.dev/python/docs/docker)、
[HTTPCore Network Backends](https://www.encode.io/httpcore/network-backends/)。

## 回滚与待验收

停止独立 Reader，关闭该来源开关即可停止新读取。待运行租约进入 UNKNOWN，保留其预算。
保留最新 Core 代码与 `0014` 数据结构通常无需数据库降级。
若确需回退旧 Core，应先停止所有写入并完整备份；`0014` 在存在多请求预留记录时拒绝降级，
避免丢失账目。不要手工删记录绕过限制。恢复旧备份必须重放最新独立遗忘日志。

真实字幕 L2 可用性、Linux sandbox 运行、长期资源、真实 QQ 联调及生产
备份仍是单独门禁。PR #12/#11 的合并和生产启用由主人审阅决定。
