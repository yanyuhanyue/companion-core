# 小黑盒独立只读 Reader

工作包：`CC-NEXT-20261019-P2 / R2`。固定开发基线为
`c74cae69022fa09543a4e59209002c5282784140`（PR #13，尚未合并）。

本增量扩展既有 `source_reader` 进程和 Exploration / Observation / Content 数据路径。
当前真实公开静态页面测试 **10 条、正文成功 0 条**，均返回页面外壳。
这份实现提供有界读取、明确失败、图文来源合同和已配置服务的连接能力；
不能据此声称小黑盒真实正文接入或生产验收已经通过。
完整门禁见 [p2-validation.md](p2-validation.md)。

## 读取模式

默认 `--source heybox` 仅读取规范化公开帖子页面的 `<article>` 正文。
页面标题、站点简介、导航、HTML 壳、图片 URL 均不能单独构成已读正文。
脚本、表单和按钮文本不进入文章；不加载页面 JavaScript、不登录、不跟随跳转。
访问限制返回 `UPSTREAM_ACCESS_LIMIT`；无正文返回 `READ_BODY_UNAVAILABLE`。
公开页面模式不推断首页为空，自主发现明确返回 `DISCOVERY_SOURCE_REQUIRED`。

如果未来主人显式配置兼容只读来源，可指定 `--source-url`。该模式仅发送：

| 请求 | 固定约束 |
|---|---|
| `GET /api/feeds?page=1&limit=N` | N ≤ 40，重复 URL 去重，只有标题的结果是 DISCOVERED |
| `GET /api/post/detail/{id}` | page/index/limit/is_first/owner_only 均为 1；只解析 result.link |

接口沿用原 Bridge 可选来源合同，参考已核验的
[xhh-workshop feed 路由](https://github.com/Sakura520222/xhh-workshop/blob/3f1ca9aee836691f134a2c744ff64fe6f3534ef6/crates/xhh-http/src/routes/feed.rs)。
本轮用户确认没有这类服务，因此未安装服务、未配置平台账号，也没有真实服务成功样本。
不复制第三方 Rust、登录或签名实现；不调用其写帖、评论、点赞或 Agent 路由。

Source 服务 token 与 Core 适配器 token 分离，来源只允许固定 origin；明文 HTTP 限
localhost／私网 IP，HTTPS 校验证书。请求不继承代理或 Cookie，不跟随响应中的重定向。
服务响应 `relogin`、`login`、`need_login`、`show_captcha`、`forbidden` 立即报告受限。
受限结果不会触发另一个入口的自动重试。

## 图文与证据合同

Reader 解析 `result.link.text` 的文字／HTML／图片块，按原顺序生成文字和 `[图片N链接]`
占位标记。记录最多 2 个所属平台 CDN 图片引用及其文字偏移，图像理解继续由已有 Content
图像流程和独立费用预算负责。图片 URL 不等于图片已分析。
无可核实文字的纯图片帖子返回未读到正文，不能假装识别出图中内容。

`ReadItem.article` / `ObservationPayload.article` 为可选证据：

- `method`：`public_html` 或 `source_api`；`fetched_at` 为实际取得响应的时间。
- `response_sha256` / `response_bytes`：原始有界响应的摘要和字节数。
- `images`：去查询参数的同平台 CDN URL、在正文中的 `text_offset`，保持顺序。
- `truncated`：正文或图片因限制未完整收录；文本最多 11500 字符。

Core 校验来源是 heybox / FETCHED / text、采集时间、图文引用与响应用量相符。
正文与引用进入同一 Observation 依赖链，旧备份恢复时仍重放最新独立 ErasureLedger。
观察仅作为 `external_untrusted` 资料进入当前聊天；不修改权限、人设或工具策略。
新字段写入已有 JSON 证据列，本增量没有新数据库迁移。
缺省 article 不参与旧指纹计算，v0.5 和 PR #13 既有幂等回执继续兼容。

QQ 卡片、分享 URL、`/a/{id}` 和规范帖子路径统一为 `/app/bbs/link/{id}`。
支持数字及受限的 12 位十六进制标识；重复 link_id、路径注入、凭据和外部域名被拒绝。
标识识别不意味着帖子存在或正文可访问，实际读取结果另行判定。

## 预算与进程边界

复用 PR #13 的原子请求／字节预留；小黑盒每次读取仅 1 个 GET，适配器建议
`reader_max_requests = 1`。任务总时限 20 秒，绝对上限 1 MiB，示例使用 512 KiB。
拒绝压缩响应、超长声明和超限流式响应。公开连接复用校验全部 DNS 答案并固定公网 IP
的网络后端，保留原 Host、TLS SNI 和证书验证。
网络结果 UNKNOWN 保留全部预留；结果回执不确定时不重新读取外部来源。

来源进程不导入 Core/Bridge，不持有数据库、主人 token、QQ 会话或模型密钥。
Source／浏览器故障不会停止普通聊天；聊天只在既有有界等待窗口内等待内容，失败或到期后
明确告知未取得内容。

## 在隔离环境运行

先升级 Core 到包含此合同的代码，再配置独立的 heybox 适配器。
主人分享模式可保持 `exploration_enabled = false`，只开启 observation/content；
所有生产配置仍由主人控制，以下仅为开发示例：

```toml
observation_enabled = true
content_enabled = true
exploration_enabled = false
exploration_daily_requests = 10
exploration_monthly_requests = 100
exploration_daily_deep_reads = 5
exploration_max_response_bytes = 524288
exploration_daily_bytes = 2621440
```

Core 的 `COMPANION_OBSERVATION_ADAPTERS` 增加以下适配器，保留其他已有适配器：

```json
{"heybox_source":{"source":"heybox","token":"<独立随机密钥，至少24字符>","reader_max_requests":1}}
```

Reader 环境单独设置同一适配器的 `COMPANION_READER_TOKEN`，关闭 Bridge 中竞争同来源租约
的 heybox Reader，然后执行：

```powershell
.venv/Scripts/python.exe -m source_reader --source heybox --core-url http://127.0.0.1:8765 --adapter-id heybox_source
# 公开单链接检查：不连接 Core、不写观察、仅输出状态和证据摘要。
.venv/Scripts/python.exe -m source_reader --source heybox --probe https://www.xiaoheihe.cn/app/bbs/link/168860244
```

若已有经主人授权的服务，再单独设置 `COMPANION_HEYBOX_SOURCE_TOKEN`，并传
`--source-url https://<受信任来源主机>`。Reader 不代替服务登录，也不读取浏览器 Cookie。
未配置时无需该 token。

`deploy/compose.reader.yaml` 新增 opt-in `heybox-reader` profile，复用非 root Reader 镜像和
1 CPU / 768 MiB / 96 PID、只读根文件系统、临时工作区、移除 capabilities 的限制。
它使用单独的 `privacy/heybox-reader.env`；B站 profile 继续使用自己的环境文件。
配置解析已用占位开发参数验证，未读取真实私密环境文件。

```sh
docker compose -f deploy/compose.reader.yaml --profile heybox-reader up --build heybox-reader
```

## 回滚

先停止新的 heybox Reader，并关闭该来源的开关；未知租约保留预算，不重放外部 I/O。
本增量没有数据库结构变更，正文／图片证据仍由既有删除链清除。
如回退 Core 到 PR #13，先停止会提交 article 字段的新 Reader；旧服务不接受新字段合同。
保留数据库与最新独立遗忘日志的备份，不通过删记录解决兼容或降级问题。
`0014_reader_budget` 的多请求历史仍禁止破坏性降级。
