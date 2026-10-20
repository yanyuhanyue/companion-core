# P5 本地验证记录

基线 `7bd47a659f60b4904b87508a3dbf0a4d6934d87d`；开始时远端 main 一致、无开放 PR，
main CI [39366854733](https://github.com/yanyuhanyue/companion-core/actions/runs/39366854733) success。
开发分支 `codex/p5-onebot-direct-bridge`，没有重合并旧 PR，没有部署。

## 已执行

| 检查 | 精确命令 / 结果 |
|---|---|
| 全量 lint/回归及 Chromium | `COMPANION_TEST_BROWSER=1`、工作区浏览器缓存，`./scripts/check.ps1`：696 passed, 2 skipped，537.85s；跳过为 Windows 无符号链接权限；另有既有 Starlette 依赖弃用提示 |
| 最终新增模块与修复回归 | `./scripts/check.ps1 -TestFiles <全部 tests/test_onebot*.py>`：76 passed，43.56s；全量收集开始后新增的部署/拒绝删除回执/进程崩溃/旧 Bridge 兼容用例均在此轮执行 |
| 包构建 | `uv build --offline --out-dir .local/p5/dist`：wheel/sdist 成功 |
| 发行检查 | `python scripts/verify_package.py --dist-dir .local/p5/dist`：无 Git 导出重建排除私密文件、wheel 初始化迁移通过；新增 Bridge 模块存在，拒绝私有目录、加密备份与原生二进制 |
| Compose | `docker compose -f compose.yaml -f deploy/compose.onebot-direct.yaml --profile qq-beta --profile manual-snowluma config --quiet`，仅合成参数：退出0，无容器启动 |
| 只读 VPS 预检 | 严格 SSH 主机校验，系统/资源/Docker/监听清单通过；已有服务保留，见 operations |
| 上游 | 官方 v2.0.3 源码、EULA、Docker 模板 commit 与注册表摘要元数据已核对，未下载原生发行包 |

本地 Docker Engine 未运行，容器构建/非 root 新卷检查由 CI 执行。CI 在 Linux/Windows 全量任务中
安装 `onebot` extra，执行真正的 pytest；容器任务增加独立 Bridge 构建、无网络 `--help` 与
新持久卷打开/重启去重检查。最终 CI 状态应以本 PR 当前 head 的全部 checks 为准，不沿用 main 结果。

## 新增行为检查

- `test_onebot_direct`：好友主人严格过滤、JSON/CQ/数组/媒体降级、字节/段/深度/时间限制、
  身份/endpoint/Secret/epoch 配置、Spool fsync/FIFO/重开去重/冲突/容量/单实例、
  无效202不丢消息、非精确/超大410不能清除正文，`os._exit(73)` 真进程死亡后 WAL 和 OS 锁恢复。
- `test_onebot_delivery`：实际 Core ASGI/SQLite 和 Mock OneBot，SENT/provider ID、超时/失败/畸形回执
  UNCERTAIN，无重发；begin-send 前/后及 result 后丢失 Core ACK，持久发送意图后重启；
  非主人会话、主动发送、媒体越界、账号不符、关闭发送开关在发送前拒绝。
- `test_onebot_runtime/network`：真实本地 WS 重连重放、真实本地 HTTP 身份/发送/认证、
  public DNS 在认证 I/O 前拒绝、WS 重定向拒绝。测试服务是 Mock，不是 SnowLuma 二进制。
- `test_onebot_erasure`：备份早于事件＋最新独立 Ledger＋待重放 Spool 不复活旧事件，
  无关聊天继续；COMPLETE 旧记录补建 tombstone；旧 AstrBot Bridge 消费410后继续聊天。
- `test_onebot_metrics/benchmark/deploy`：Core 真实阶段计时且无正文/账号，100 合成私聊/100去重、
  一次回复/FIFO、可选后台任务等待不阻塞；分证据汇总、nearest-rank、空缺值保留；
  Demo 所有能力/预算关闭、网络/卷分离、SnowLuma 内建状态回复关闭，生产门禁保持关闭。

读审发现并修复：畸形 Unicode 导致进程退出、遗忘后旧库重放、旧 AstrBot410兼容、
新卷0700权限，以及 receive/model/OneBot ACK 阶段戳精度。没有放宽身份或 UNKNOWN 合同。

## 性能和真实边界

100个 OFFLINE_FIXTURE 的原始匿名阶段记录与同轮 durable delivery 状态在
`.local/p5/evidence/offline-100.jsonl`、`offline-delivery-states.json`；
`benchmark_qq_beta.py` 汇总 JSON/Markdown。Windows 本地持久卷＋Core ASGI＋FakeModel＋Mock OneBot，
不代表网络或真模型成绩。Core fixture 的逻辑钟固定，因此 job age=0 不是实际排队性能结论；
4300 micro-USD 只是合成 usage 的预算计算，实际费用0，真人 QQ 和付费调用0。
真实 P50/P95、样本数、人类评分及 QQ 接收回执均保持 null/0/NOT_RUN。

`onebot_direct_bridge_code_ready=true` 依据本轮代码/本地合同检查；它不授予登录、发送或部署。
真实 OneBot、QQ 20场景、G1、20自然日日记、异地恢复、DSH插件与Windows实机门禁仍未通过。
许可、具体变更、QQ账号/时段/额度/回退、真模型预算须按 operations 后续逐项确认。
