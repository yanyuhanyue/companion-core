# P7 对 #19 相对 #18 的独立复审

比较 `33dff1186899d3a91f4c2023ab0d594024761971...940b6f28c16f7d495c3ff86e6b5bc7fad63e917a`：
24 文件，+1154/-23，4 commits。旧主线 #11–#17 不计入本次增量。
逐项读取变更及相关 Core/Bridge/Spool/网络/预算/部署/包合同；未发现新的可复现 P0/P1/P2，
本轮没有修改应用代码或迁移。结论只适用于 `CODE_READY`。

| 合同 | 复核证据与结论 |
|---|---|
| 脱敏长度膨胀 | normalizer 在入 Spool 前检查最终 20,000 字符上限；膨胀负例通过，不再毒化 FIFO |
| 旧 410 回执 | bounded receipt 拒绝重复 JSON key；只有精确 EVENT_ERASED 终止；畸形/附加字段/OTHER 保留 pending |
| UNKNOWN 预算 | CoreError 与普通异常均保留 UNKNOWN 预留并发匿名失败 usage；不改变错误码/重试判定；日志不替代持久 ModelUsage |
| Compose 构建与运行 | 本地 build 只有 :review tag；runtime Bridge 只有 digest；合成独立 env/config 展开与 build --print 通过 |
| 入站与去重 | friend + 唯一 owner/bot/sender、自回声/群/临时过滤、帧与复杂度/Unicode 限制、稳定 epoch/指纹冲突、WAL/FULL 先持久后 ACK |
| Core HTTP v1 | 真实 ASGI 的认证/202/精确 410/幂等保持；Transport 不导入 Core，适配器不承担存储/发送 |
| 发送与重启 | begin-send 后的 durable uncertain 意图、ACK/result 丢失、人工 SENT/CANCELLED 后迟到结果隔离；不盲目重发 |
| 目标与文本 | 固定 owner、仅 qq.private.reply、text array + auto_escape；模型 CQ 不转成动作 |
| 网络 | 私网 DNS/TCP pin、无代理/重定向；HTTPS/WSS 原 hostname/SNI/Host 校验；合成 CA 不放宽生产 TLS |
| 隐私与恢复 | 加密旧备份 + 最新 Ledger、事件创建前 tombstone、COMPLETE 回放和 late receipt 回归通过；不冒充 Reader G1 闭包 |
| 指标 | 匿名白名单与 evidence 分栏；只汇总组件 elapsed_ms，不减跨进程 monotonic；UNKNOWN/成本需数据库核对 |
| 容器 | UID10004、只读/cap_drop/无新特权、256MiB/PID64、0600/0700；旧 owner 拒绝由 Linux CI 验证，不能在 Windows 伪造 POSIX 结论 |
| 发布包 | .local/.env/config/DB/Ledger/备份/附件/浏览器输出与原生组件排除；归档、无 Git 重包及 wheel 迁移验证单独运行 |

## 实际验证

- Python 3.12.10；`uv sync --offline --frozen --extra onebot --group reader` 固定锁依赖，独立 venv。
- `COMPANION_TEST_BROWSER=1`、工作区 Chromium 缓存、`HF_HUB_OFFLINE=1` 后运行
  `./scripts/check.ps1`：**715 passed / 2 skipped / 1 warning，543.56s**，ruff PASS。
  全量已包括 87 个 OneBot 用例，不以重复跑同一套测试增加证据数量。
  两个 skip 是 Windows 的 plugin/grant symlink 创建权限；warning 是 Starlette/httpx 弃用提示。
- `python scripts/verify_onebot_compose.py`：PASS；Docker Compose v5.5.0，仅合成配置，未联系本地 daemon。
- `python scripts/replay.py`：100 合成事件、30 facts、recall@6=1.0、public_actions=0。
- `python scripts/smoke.py`：本地 Demo API/worker/认证与私聊投递合成检查 PASS。
- `uv build --offline` 与 `python scripts/verify_package.py --dist-dir .local/p7/dist`：PASS，
  包含无 Git 导出后带私密 canary 的重包排除与 wheel 迁移初始化；sdist 包含全部9份 P7 报告。
  wheel SHA256 为 `b9c74360fc7cb7a2d50f6abe509153de58b3854c55e5486989000fa186ed0cb4`，
  与 P6 相同，应用代码没有改变。
- 固定源码的 Linux/Windows、Chromium、native fixture 与四镜像/权限 CI 均为 #19 两组 5/5 success；
  P7 报告 head 的新检查另见 P7-A checks/最终提交记录，不能混用 SHA。

任务书示例的 `python scripts/smoke_onebot.py` 是 Linux Bridge 镜像内检查，
会断言 os.geteuid()==10004 且没有 Core/AstrBot。Windows 开发 venv 不满足它的前提；
本轮保留该校验，在目标主机的隔离 Bridge 镜像中运行，不删断言或伪造本机通过。

## 部署前后边界

仓库 Compose 是模板；正式实验配置另在私密目录固定 Core/Bridge/SnowLuma 三个 RepoDigest，
不直接复用模板的共享项目/网络名或 Core 本地 tag。新项目/网络/卷均有独立 P7 命名空间。
现有服务不加入新网，不挂 Docker socket，不共享 QQ/Spool/Core DB/Ledger/模型 Key。
后续运行和恢复结果见 [release review](p7-release-review.md) 与机器证据。

目前无真实 OneBot/QQ、付费模型、真实网络 soak 或 20 自然日记证据。
Reader grant 自然失效、撤销访问与撤销既有处理授权的语义尚未明确，G1 维持 FAIL，
另行澄清并独立 PR；不以 event tombstone 测试关闭 G1。
