# P3 实测、验收与回滚记录

任务 CC-NEXT-20261020-P3V2，验证日期 2026-10-20。代码基于 PR #14 的
`058cb62d7323ad23195a2077b96ede36fd24a0d2`，分支 `codex/v06-plugin-readonly-p3`。
main、#11–#14 的完整 HEAD、base、开工 CI 见 [冻结基线](plugin-compat-p3.md#冻结基线)
及 [查询快照](p3-baseline.json)。没有合并、变更 base、强推或生产部署。

## 结果与范围

| 门禁 | 结果 | 证据与限制 |
|---|---|---|
| CODE_READY | 是 | 固定只读目录、显式授权、请求/字节上限、撤销、幂等、擦除与旧协议回归 |
| SOURCE_READY / 小黑盒帖子 | 是，10/10 | 主人本轮独立扫码授权；10 篇不同实际帖子经原始 API 读取后被 Core 接收 |
| SOURCE_READY / 小黑盒推荐流 | 是 | 1 GET 得到 3 个 DISCOVERED，经真实 WakePlanner 决策；未把发现当正文 |
| SOURCE_READY / B站 L2 | 是，固定样本 1/3 | 1 个可核对目标与时间段的 L2；其余 2 个字幕 missing，保留 L1 |
| SOURCE_READY / B站搜索 | 是 | “独立游戏”搜索 3 个 DISCOVERED；单个 Goal 读取 DONE/L1，字幕 missing |
| SOURCE_READY / B站评论 | 未实测 | 固定首屏只读工具与预算通过 fixtures；没有写入 Core 的自动触发器 |
| CORE_INTEGRATED | 是 | 实际 ASGI HTTP/SQLite、lease/result、上下文、模拟回复、加密恢复与最新删除日志重放 |
| PRODUCTION_READY | **否** | 真实 QQ、长期日记、生产异地恢复及长期账号稳定性未验收 |

真实样本和 fixture 分开记录：[小黑盒](heybox-live-p3.json)、[B站](bilibili-live-p3.json)、
[原生离线工具](plugin-native-p3.json)、[机器门禁](p3-gates.json)。没有真实模型调用、
QQ 发送、公开评论或私信。Mock 回复形成 Outbox 行但不发送；不能据此宣称真实 QQ 已通过。

### 正文与字幕核验

小黑盒前三篇 `168860244`、`152986484`、`177617584` 还通过独立官方网页核对：
三个标题可见，正文开头/中间/结尾各 40 字共 9 处匹配，并逐张查看三张私密截图。
截图不随包或 PR 发布；这次代理可视检查不替代主人生产验收。
帖子正文保留来源响应哈希。图片只保留最多两张引用，未运行视觉分析；上游独立图片列表
没有原始插入位置时按列表顺序追加并标注。`truncated=true` 可能是图片数量截断，不表示已分析全帖图片。

B站最终固定样本：

| BV | 最终结果 | 请求数 | 字幕范围 |
|---|---|---:|---|
| BV1Sb41127UZ | L2 / available | 3 | 首 P，7 段，42.20–89.60 秒，目标时长 124 秒 |
| BV1Lb411h7gY | L1 / missing | 2 | 没有取得字幕 |
| BV1SE411v7qy | L1 / missing | 2 | 没有取得字幕 |

早期 bvid+cid 请求曾返回与目标不匹配的 AI 字幕，旧探针结果不计入成功数。
修复后改为 aid+cid，并检查 metadata/player ID、AI 字幕路径归属及字幕时长；错配返回
`READER_SUBTITLE_TARGET_MISMATCH`。重新实测才得到上表的 1 个 L2。
没有下载视频/音频、运行 ASR、提取关键帧或宣称完整观看。

### 可重复的验证入口

| 范围 | 命令 / 入口 | 实际结果 |
|---|---|---|
| Windows 全量初跑（含真实 Chromium） | `./scripts/check.ps1`，`COMPANION_TEST_BROWSER=1` | 573 passed、1 skipped、1 failed；失败是新增 FIFO 测试缺少模拟送达回执，已修复 |
| 修复后相关回归 | `./scripts/check.ps1 -TestFiles tests/test_plugin_readonly.py,tests/test_source_reader.py,tests/test_heybox_reader.py` | 130 passed；真实浏览器另在全量初跑和 CI 的 browser-reader job 验证 |
| 上游固定小黑盒 fixture | 独立 hash-locked 环境运行 api_client/signing/parsers/feed/topic/permissions 六组 | 58 passed |
| 原版 MCP 注册合同 | `scripts/verify_plugin_native.py --upstream-root .local/p3/upstream` | 真实 stdio tools/list 只有四项；hidden reply/force 和缺失授权均被拒；外部请求 0 |
| 合成回放 | `scripts/replay.py` | 100 事件，30/30 固定事实，recall@6=1.0；不代表真实模型准确率 |
| 六进程 smoke | `scripts/smoke.py` | API + 五类 worker、私聊送达协议、认证通过；不使用真实 QQ |
| 打包 | `uv build --out-dir .local/p3/dist`，`scripts/verify_package.py --dist-dir .local/p3/dist` | wheel/sdist、无 Git 源导出重建、迁移初始化；私密目录排除和 notices 包含检查 |
| 完整平台及镜像 | 本 PR 当前 HEAD 的 GitHub Actions | Windows/Linux 全量、真实 Chromium、原生插件、三类容器构建；最终 run 链接和状态记入 PR 描述 |

全量覆盖旧 0003/0004/0013/0014、有数据重开、旧幂等指纹、UNKNOWN 预留及独立最新
ErasureLedger 恢复。本轮不新增迁移。CI 当前 HEAD 才是最终平台回归结论，初跑失败记录保留。

Linux 目标用固定 P2 runtime 镜像加只读新源码挂载验证默认拒绝和工具边界，2.689 秒退出 0；
见 [Linux 实测](linux-readonly-p3.json)。UID 10003、1 CPU、768 MiB、96 PID、根只读、
无网络/能力/新增特权。cgroup peak 文件不可读，指标为 null，不能声称峰值为零。
完整新镜像由 CI 构建。前后 13 个既有容器的 ID、镜像、运行状态和启动时间完全一致。
Linux Chromium 仍保留 P2 `No usable sandbox` 阻断；没有改变宿主隔离参数。

## 验收矩阵与剩余门禁

| AC | 状态 | 说明 |
|---|---|---|
| 01–03 | PASS | 静态四工具、无独立 Agent/定时器；版本失配 PLUGIN_UNAVAILABLE |
| 04–06 | PASS | HTML 外壳拒绝；10 篇正文；过期/验证码/签名/401/429 fixtures 有限失败并熔断 |
| 07 | PASS（引用范围） | 图片引用与分析分离，空内容不假装读图 |
| 08–10 | PASS（已列样本） | L1 保留、1 条真实 L2、缺字幕明确降级；未启用 ASR |
| 11 | DEFERRED | 没有视觉/L3，输出明确声明读取范围 |
| 12–16 | PASS | 外部内容不可信、URL/DNS/重定向/字节限制、凭据隔离、重放/UNKNOWN、擦除恢复 |
| 17 | PASS | 两个来源都不可用时，模拟 FIFO 回执后普通聊天继续 |
| 18 | PARTIAL | Linux 浏览器旧阻断保留，API Reader 独立工作 |
| 19 | PASS（主机快照） | 13 容器未变；在线 Android/Houdini/ADB 未操作，未新增长期监测证据 |
| 20 | 以最终 CI 为准 | 跨平台全量、镜像、回放与打包；详见当前 HEAD 的 checks |
| 21 | PASS | force、未知工具/字段 I/O 前拒绝；无公开写入口 |
| 22 | OPEN | >=20 条真实 QQ 与 >=20 自然日日记需独立人工验收 |

两个新账号 grant 已在验证后设置 `enabled=false`；没有全局退出或修改原在线账号。
私密凭据、扫码材料、浏览器 Profile 保留在忽略目录，不入包/报告。
六个此前被清理策略阻止的目录只做了[只读台账](retained-caches-p3.json)，没有删除重试或权限修改。
安全与许可审查见 [plugin-security-p3.md](plugin-security-p3.md) 和
[plugin-compat-p3.md](plugin-compat-p3.md)。

## 回滚及后续动作

停用 opt-in Reader/profile，关闭其授权文件或撤销对应观察 token 即可停止新增来源。
保留普通聊天 worker、数据库、Outbox、UNKNOWN 预留和独立 ErasureLedger；不降级迁移、
不清数据、不改已有 PR 历史。上游更新需重新核对完整 SHA、许可、工具目录和真实样本。

1. 人工审阅新 Draft PR 和 #14 → #13 → #12 → #11 依赖，决定顺序后再合并。
2. 单独完成真实 QQ 私聊、断线重连与 UNKNOWN 恢复验收；本轮未执行真实发送。
3. 完成 20 个自然日日记人工验收及生产异地加密恢复/最新独立删除日志重放。
4. 在明确账号期限、调用额度和监测方式后做来源长期稳定性验收。
5. 图片视觉分析、评论实源或 ASR/L3 各自单独定义权限、预算与样本；当前图片只有引用。
