# 主人控制台与统一云预算

控制台读取现有任务、费用与来源记录，提供记忆确认／修正／遗忘、日记管理、运行暂停和有版本的配置修改。入口为 `/console/`，默认关闭。所有页面都需要主人登录；空列表、尚无运行记录、失败与离线分别显示。

界面分为今天、活动时间线、记忆、日记与成长、阅读与文件、表情素材、语音与节点、QQ 资料行为、能力与开关、运行诊断和配置。页面与接口的对应关系、设计稿和尚未实现的部分见 [主人控制台第二版](console-v2/README.md)。

## 配置与运行

在现有私有部署配置中设置：

```toml
[console]
enabled = true
public_origin = "https://console.example.test"
session_seconds = 3600
max_sessions = 5

[cloud_budget]
daily_micro_cny = 20000000
video_per_item_micro_cny = 1000000
asr_per_item_micro_cny = 500000
usd_to_cny_micro = 8000000
fx_source = "operator_planning_estimate"
fx_validity_seconds = 2592000
```

`COMPANION_CONSOLE`、`COMPANION_CLOUD_BUDGET` 可用相应 JSON 对象覆盖配置。运行前执行既有 `python -m companion init` 应用迁移，API 与独立 worker 使用同一数据库和私有配置。TLS 反向代理应只发布控制台静态路径及 `/v1/console`，其他内部传输接口继续保留原有网络边界。公网访问必须先验证 DNS、证书、反向代理与浏览器实际 Origin；仅保存新地址不代表域名切换完成。

`public_origin` 默认为空；启用控制台时必须显式给出规范 HTTPS Origin。
单机 Nginx 示例见 [console.nginx.conf](../deploy/console.nginx.conf)。公开入口只有
`/console/` 与 `/v1/console/`。静态请求先经 Core 的原生开关和凭据配置检查，成功后
通过 `X-Accel-Redirect` 转至 Nginx `internal` location，再读取回环端口 8766 的
独立 UI 镜像；直接访问内部资源路径返回 404。Core 监听回环地址：

```powershell
python -m companion serve --host 127.0.0.1 --port 8765 --forwarded-allow-ips 127.0.0.1
```

可信列表填写直接连接 Core 的固定代理 IP；多个地址用逗号分隔。不设置时忽略全部转发头，
也不会因环境中的 `FORWARDED_ALLOW_IPS=*` 意外开启信任。当前入口拒绝通配符、域名和
网段，避免把客户端直接连接当可信代理。名单只影响 Uvicorn 的客户端地址/协议还原，
不替代 owner token、会话、Origin、CSRF 或内部 API 认证。
单代理示例用 `$remote_addr` 覆盖外来 XFF；多级代理须先核对实际受信链，不能照抄信任任意地址。
这样登录失败按还原后的来源限流，不会让同一代理后的所有访问者共享失败次数。
容器部署使用实际代理容器地址，并保持 Core 端口不公开；示例本身不是部署完成证据。
这里复用 [Uvicorn 的代理信任配置](https://www.uvicorn.org/settings/#http) 和
[Nginx 的请求头覆盖规则](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header)。

### 独立 UI 镜像

[console-ui.Dockerfile](../deploy/console-ui.Dockerfile) 从唯一源目录
`companion/console_static` 复制四个资源。Core 不再读取包内资源，也没有静态交付 fallback。
首次切分需要配套部署 Core gate、UI 镜像及代理配置；以后仅 UI 修改可以单独替换 UI 容器。
生产镜像内文件保持不可变，不挂载业务源码或可变资源目录。

UI 使用官方 `nginx:1.31.8-alpine`，固定多架构索引 digest
`sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60`。
来源为 [官方镜像清单](https://github.com/docker-library/official-images/blob/master/library/nginx)
和 Docker Hub Registry `library/nginx` 的该版本 manifest。内部跳转和直访拒绝复用
[Nginx X-Accel 语义](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_ignore_headers)
及 [internal](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal)。

将 `console-static-headers.nginx.conf` 安装为 `/etc/nginx/console-static-headers.conf`，
并使用完整的 `console.nginx.conf`。静态成功、Core 拒绝和代理错误均设置同一 CSP、
`no-store`、nosniff 等头；隐藏静态上游同名头以免重复。API 响应继续使用 Core 的安全头。
内部跳转不向 UI 传 Cookie、Authorization、原始请求头或请求体。Core 的禁用 404、
缺 owner credential 503 及错误正文直接透传，UI 故障返回代理错误，不回退包内页面。
原 owner cookie 路径 `/v1/console`、Secure/HttpOnly/Strict、Origin、CSRF 和幂等契约不变。

[compose.console.yaml](../deploy/compose.console.yaml) 只新增 UI 服务，映射回环端口，
使用独立 bridge 网络、非 root 用户和只读根目录；它不加入 Core 网络。Docker 的
`internal` 网络在本次 Engine 验证中未建立宿主端口映射，因此宿主 Nginx 方案采用
[仅回环发布端口](https://docs.docker.com/engine/network/port-publishing/)，通过实际绑定检查
保证后端不向外网发布。设置 `COMPANION_CONSOLE_UI_IMAGE` 为
验证过的 `repository@sha256:...`，由现有部署流程使用 `--no-build --no-deps` 仅替换
`console-ui`；保留前一镜像 digest 用于回滚。该 overlay 不会公开 Core 端口；Core 的
回环监听或已有私网连接以及实际可信代理 IP 仍由部署者配置。

UI/API 兼容性沿用 `/v1/console`，发布清单绑定已验证的 UI 和 Core OCI digest；
发布前对实际组合运行 TLS/浏览器契约检查。`overview.schema_version=1` 只断言该响应的
结构版本，不代表全部 UI/API 已兼容。没有新增 UI 版本协商协议。

### CI 接入与合成检查

以下构建和开发验证在本地或正式 CI runner 执行；生产宿主只部署已验证产物。
容器 identity 保持不变不能证明 CPU／IO 没有干扰现场运行。

Windows 和 Linux 均使用真实 Nginx 及同一份部署配置，启用检查后缺少 Nginx 必须失败。
Windows 使用 [官方原生发行包](https://nginx.org/en/docs/windows.html)，版本 1.31.8、
ZIP SHA256 `fbb1501009be34655cc238d460714cf20391b639013f4c73859813d7938783fd`；
`scripts/check_console_proxy.ps1` 下载至工作区并校验哈希。Linux 显式安装 Nginx 或设置
`COMPANION_TEST_NGINX`，不通过平台条件跳过代理检查。已有 Console 浏览器公共 fixture
也经过 TLS Nginx。HTTP 检查验证合成 CA 和 IP SAN；浏览器仍使用隔离测试证书例外，
不作为公网 DNS／证书信任验收。

```powershell
./scripts/check_console_proxy.ps1 -Python <project-python>
```

CI 需预先安装锁定版本的 Chromium，并保存 `.local/test-results/console-proxy.xml`。
Linux 可以使用 [console-check.Dockerfile](../deploy/console-check.Dockerfile)，提供
本轮已构建的不可变 Core 和 Reader 镜像 ID，复用其 Python 依赖和浏览器运行库。
设置 `COMPANION_TEST_CONSOLE_UI_URL` 为私网候选 UI 容器地址，既有浏览器流即可使用实际
OCI 资源；凭据隔离与后端中断用例仍启动独立原生后端，明确覆盖这两个故障边界。
UI 镜像替换检查需另存 Core／Bridge／Reader 容器 identity 前后快照，确保仅 UI 被替换。
`scripts/smoke_console_ui.py` 只创建、替换、回滚和清理自己的回环 UI 测试容器，对三个
指定运行时仅做只读 identity 检查。两个 UI 镜像必须是不同的不可变 ID；替换版本的四个
资源须与当前源码逐字节一致，回滚版本须与首次读取的哈希一致。收据记录各受保护容器
原有的运行／停止状态；状态保持不变不代表已停止的 Bridge 恢复，也不替代 QQ 验收。

```text
python scripts/smoke_console_ui.py --ui-image <baseline-image-id> --replacement-ui-image <candidate-image-id> --core-container <core> --bridge-container <bridge> --reader-container <reader>
```

CI workflow 和通用发布脚本由 CI 负责人接入，本片不修改其队列或发布规则。

登录密码来自已有 `COMPANION_CONTROL_TOKEN`。浏览器使用 Secure、HttpOnly、SameSite cookie；写入同时要求精确 Origin、CSRF 与幂等键。传输令牌不能代替主人会话。密码错误限流和会话数量／期限由服务端执行，退出后清除界面上的私人内容。

一般配置保存为加密的待生效版本，返回保存版本、当前生效版本和字段修改记录。运行中的请求继续使用原配置；按现有停止、备份、窗口交接、重启流程应用新版本。密钥只写，空白界面不等于没有配置；返回的 `null` 保留原密钥，明确写入空字符串才清空。配置密文由主人控制令牌派生密钥保护，备份与恢复必须同时保管原令牌；直接更换令牌不能解密旧版本。

通过控制台修改聊天或图片服务 URL 时，必须使用 HTTPS。若目的地址改变且已有密钥，
必须在同一次修改中明确提供新目的地凭据（或明确清空）；`null` 不能把旧密钥静默带到新地址，
跨主机也不能重复提交同一个旧密钥。同一地址的只写密钥回读仍保持原语义。
这是配置编辑时的防误用边界；更改地址本身不会发起请求。聊天模型实际调用还须匹配原
paid endpoint、窗口配置/凭据快照和 ModelClient 最终 HTTP 请求检查，重启不会自动取得新授权。

独立云预算策略即时约束以后准入的调用。它不重置任何原生费用行、累计账本、旧预留或未决责任，也不替代现有场景／窗口／月度限制。已经启动的任务继续用冻结的模型价格、汇率和任务上限快照。

## 金额口径

金额使用整数微元：`1000000` 为 1 元。默认云端日额 20 元，整个视频任务 1 元，单个输入语音任务 0.5 元。当前网页媒体会话以一次会话作为 ASR 任务边界；QQ 语音接入后必须使用相应原始消息任务标识。

成本按配置费率和汇率估算，不等同于服务商最终账单或保证上界。未知价格、过期汇率和无法计价的未决调用会阻止新的云调用。已结算、已预留和 UNKNOWN 都占用额度；跨日未完成的预留仍计入当前可用额，避免午夜重置后重复花费。界面的“已用”已经包含三者，不能再次加上预留。

已验证本地执行关闭的模型调用，可通过[操作员账务入口](qq-beta/model-accounting.md)追加暂估或服务商账单凭证。它保留原 UNKNOWN 结果与预留责任，并同步更高的记账金额；账单确认不等于恢复了模型回复。

确认账单超出原预算限制会持久停止后续调用；仍可追加更高的真实费用，但不会解除停止。已验证账单写入独立账本失败时，`PENDING_JOURNAL` 保留已知费用并阻止新调用；完整账本可用同一请求和证据恢复，损坏账本需保留证据后修复，入口不会自动截断或覆盖旧链。

原有无限金额开关不能越过统一 CNY 限额。事务内先检查和记录云预留，再写原生准入；云拒绝不得遗留独立账本头或重复调用。结果未知不自动重付。已有视频独立结算流程的验证回执可解除对应 UNKNOWN 分类；没有可信结算证据时保留责任。

恢复备份后，备份无法证明后续是否又发生过云费用，因此所有新云预留进入待核对状态；已知历史仍可查看，可用余额为零。重启、调高额度和跨日不会清除这个状态。当前模型账本尚不能证明全部工具与语音费用，本版不提供跳过核对的重新启用入口；需要保管完整的独立费用证据，恢复后由部署负责人核对。

## 诊断与来源

能力“可用”表示已知本地准入条件允许，不表示远端在线、已读过网页或已执行动作。Worker 状态来自真实循环记录。记忆和内容列表沿用来源、保留期、撤回和遗忘过滤；预算和诊断不显示私人文档全文、cookie 或模型密钥。

开发期间的能力实现标记由 `companion/console_static/app.js` 中的
`SHOW_IMPLEMENTATION_STATUS` 统一控制；正式发布时置为 `false` 即可隐藏。
实现程度、当前运行许可和真实体验验收是独立信息；界面尚缺的操作与详情单独记录。

控制台写操作保留最小幂等回执。已审计的执行前拒绝记录为 `REJECTED`，仅保存错误码与
HTTP 状态；同键、同内容重试返回原错误，不重新调用原生操作。错误条件后来变化也不会
把这个旧请求变成新操作；明确的新操作使用新幂等键。同键改内容仍返回冲突。

当前有限清单为：记忆不存在／非活动、空白修订、凭据修订拒绝；日记修订输入无效；
配置禁用的日记或主动分享启用请求。它们分别在原生写入前失败或在其事务回滚后返回。
日记不存在不在清单中，因为原生查找前可能清理过期证据；未知异常一律不推断无效果。
若操作可能已提交但回执未完成，仍保留 `STARTED` 并返回待核对。浏览器不会显示成功或
自动换键重做。先读取原生记录核对结果；投递核对继续使用原生独立回执恢复，不套用拒绝清单。

## 文件与视频准备模块

`companion/file_parser.py` 提供有界 txt／md／docx／文字 PDF 解析，以及隔离 LibreOffice 的旧 doc 转换。部署镜像为 `deploy/file-parser.Dockerfile`；调用 `parse_isolated` 时必须使用已安装镜像的固定 digest／image ID。子容器无网络、无宿主挂载、无 Core 凭据，使用非 root 用户、只读根目录、资源限制和独立可写 scratch。缺少 Docker／镜像／转换器时返回不可用，不改用宿主解析。扫描 PDF 无文字时明确标注；本模块未提供 OCR。

Linux 容器 CI 运行 `scripts/smoke_file_parser.py --image <固定镜像ID>`，验证合成文档与真实转换器。单元测试中的低层 `parse_document` 只用于合成数据或已经隔离的进程，本身不是隔离边界。此模块尚不代表 QQ 文件接收链路已启用。

`companion/video_sampling.py` 根据逐条字幕和弹幕提出少量帧时刻与重点区间，保留时间、来源和选择理由。关键词、主题变化和局部弹幕密度属于选取线索；输出不声称已观察画面。实际下载、片段边界验证、模型输入与累计视频预算由后续完整视频任务接入。
