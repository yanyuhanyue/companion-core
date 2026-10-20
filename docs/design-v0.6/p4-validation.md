# P4 开发验证记录

2026-10-20，任务 `CC-NEXT-20261020-P4`。基线、发现和修复见
[独立审查](p4-release-review.md)，[机器门禁](p4-gates.json)，[实时基线](p4-baseline.json)。
本轮范围为审计、最小修复、隔离验收和运行准备；生产服务没有启用。

## 执行层级

- **历史真实证据**：P3 的十篇正文、B站固定 1 L2/2 L1；#16 的 14 图引用与 3 次视觉识别。
  保留原日期、SHA、哈希及原始失败历史，不把它们当作 P4 的新读取。
- **本轮集成**：实际 Bridge 主人事件处理器/Spool、ASGI HTTP、SQLite、Source Reader、
  Core 上下文和 Outbox，以及生产 `deliver_expression` 的 begin-send/result 协议。
  AstrBot SDK 用本地 stub，未运行真实 AstrBot/NapCat，未启动无限 `pump` 循环。
- **离线来源**：十个来自固定 P3 公开证据的 URL/标题，正文明确为脱敏占位；
  不包含历史原文，不重新证明十种真实正文布局。B站和故障为协议 fixtures。
  所有来源 socket 由内存后端接管；账号 grant 只有合成凭据。
- **模型/图像/发送**：确定性 Mock，从收到的上下文生成回放文字；发送回调只存内存并
  返回模拟回执。QQ 图片测试直接使用合成 PNG 的 claim/upload HTTP，未下载 QQ 原图，
  下载队列由既有测试覆盖。不能据此宣称真实模型抗注入或真人自然度通过。
- **生活**：Heybox、B站、跨平台三条链各七个模拟日；合计三个独立演练库。
  同主题字段显式指定、语义去重用确定性 stub，未验证自动主题聚类、语义模型质量。
  **真实 QQ = 0 条；真实日记人工审核 = 0 天；新 Live 源 GET = 0；付费模型 = 0。**

## 可重复入口

在仓库根目录运行，所有缓存、数据库和临时数据留在 `.local`。

```powershell
$env:COMPANION_TEST_BROWSER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = (Resolve-Path .local/playwright-browsers).Path
./scripts/check.ps1

# 只重现本轮集成时，使用项目检查入口的文件参数：
./scripts/check.ps1 -TestFiles tests/test_p4_bridge_integration.py,tests/test_p4_reading_to_life.py,tests/test_p4_reader_security.py,tests/test_p4_gates.py

# 仅验证证据结构，不执行任何发布动作。
.venv/Scripts/python.exe scripts/verify_p4_gates.py
.venv/Scripts/python.exe scripts/verify_p4_gates.py --require-production
```

最后一条应以退出码 **1** 拒绝当前生产门禁。它是本地证据一致性校验，不能自行创造主人
许可，也不验证外部签名；真实审核记录应从私有人工验收产生，不能手填模拟结果解锁。
校验要求真实 QQ 记录与计数一致、日记按有效自然日/时区去重、未满足门禁时不能标 true。

| 验证 | 本轮实际状态 |
|---|---|
| Reader 最小红绿 | 目标错配和文件替换 4 个原始反例先失败再通过；预算熔断另有先红后绿 |
| Reader 相关三文件 | 96 passed / 1 skipped；Windows symlink 创建权限限制 |
| 七日生活链 | 3 passed；Wake→发现→Goal→实际 fixture 读取→兴趣→Nightly/日记→Social→Outbox；重启/删除/恢复 |
| B站/门禁收口 | 9 passed；修正演练时钟与构造参数，计数/同日伪二十天反例通过 |
| 本地全量 | **628 passed / 2 skipped**，515.56 秒；Ruff PASS，四项本机 Chromium 实测通过；两个 skip 是 Windows symlink 权限限制 |
| 独立打包 | PASS；`uv build --offline --out-dir .local/p4/dist`，`scripts/verify_package.py --dist-dir .local/p4/dist` |
| 新 SHA CI | PENDING；最终 Draft PR 关联的两轮 rollup 是权威结果 |

打包独立检查 wheel/sdist、无 Git 源码导出重建、私密 canary 排除、notices 与安装后的迁移
初始化。uv 提示缓存位于源目录；实际 archive 内容检查确认 `.local` 被排除。

新增 46 项：14 个成功集成场景、12 个失败/竞态场景、10 项 Reader 安全/兼容检查、
3 条七日生活链、7 项正式门禁/隔离配置检查。本轮完整执行含两个本机权限 skip；
跨平台 CI 继续核对 POSIX 分支。初期演练中构造参数/时钟配置失败已修正；
完整检查基于收口后的代码，不以早期失败替代最终结果。

## 场景矩阵

PASS 后的括号标明证据类型；未执行实际外部动作的项目继续保留 NOT_RUN/BLOCKED。

| ID | 状态 | 本轮证据 / 限制 |
|---|---|---|
| A01 | PASS（GitHub 实时读） | #15 固定 SHA 两轮共 10 jobs 全绿；#16 同样 10/10；新修复分支另查 CI |
| A02 | PASS（代码） | #11 不含 #12 七项修复；逐层 HEAD/base 固定，未 merge/rebase/force-push |
| A03 | PASS（代码+包） | 正式依赖/Reader Dockerfile 与隔离 GPL 锁分离，notices 入包 |
| A04 | PASS（fixtures/新 CI native） | 四工具、force/写/未知 I/O 前拒绝；未启动任意插件扫描 |
| A05 | PASS（十个脱敏回放） | `test_p4_ten_redacted_source_samples_reach_bridge_reply`：Bridge→Reader→上下文→实际回复→模拟回执→旧备份擦除 |
| A06 | PASS（合成图像） | 单/双 QQ 槽位经 Mock 分析才入上下文；历史 #16 Live 仅 3/14 图；无本轮纯图片帖 Live |
| A07 | PASS（协议 fixtures） | B站 L2 有片段、目标错配 fail closed；P3 原 L2 范围不扩张 |
| A08 | PASS（协议 fixtures） | B站缺字幕 L1 可回复，未声称看完 |
| A09 | PASS（合成七日） | DISCOVERED 不能完成 Goal；FETCHED 后完成，依赖保留 |
| A10 | PARTIAL | disabled grant 在新 I/O 前拒绝，迟到结果/擦除回执拒绝；自动撤销既有派生项为 G1 FAIL |
| A11 | PASS（隔离 HTTP） | 单源唯一 lease；重启重复入件只读一次、回复一次；结果幂等由既有与新七日测试覆盖 |
| A12 | PASS（合成） | timeout 后普通 FIFO 继续；暂停不抢普通聊天；Social 等待普通回复 |
| A13 | PASS（协议 fixtures） | 401/403/429/验证码/relogin 有界失败，普通聊天继续；本地预算不误熔断账号 |
| A14 | PASS（合同） | 帖子伪指令保持 external_untrusted，费用/动作类型不改变；非真实模型质量评测 |
| A15 | PASS（隔离恢复） | 每个脱敏回放及三条七日链：源删除→旧加密备份+最新 Ledger，无来源/日记/分享复活 |
| A16 | PASS（合成预算/原回归） | 源网络 UNKNOWN 保留预留、不再读；模型未知费用由原有预算/恢复测试覆盖，未付费实测 |
| A17 | NOT_RUN（真实 QQ） | 本轮仅 Bridge Spool 重启/重放合成验收；不计真实 20 条 |
| A18 | PASS（模拟回执）/NOT_RUN（真人） | 生产发送函数收到未知回执不重发，下一条可继续；真人仍需逐条核实 |
| A19 | PASS（七模拟日） | 三条来源链有引用日记与实际 fixture 事件；未编造真人经历 |
| A20 | PASS（七模拟日） | 七个独立日/源才 STABLE；user 偏好不受改写；topic 显式指定 |
| A21 | PASS（隔离）/NOT_RUN（QQ） | Quiet/pause/额度/冷却/去重；合成 Outbox 未向平台发送 |
| A22 | BLOCKED | 0/20 真实自然日人工审核 |
| A23 | PASS（本地演练）/BLOCKED（生产异地） | 加密恢复+最新删除重放通过；异地主机/密钥/网络尚无独立授权验收 |
| A24 | PASS（保留约束） | 旧保护目录未删；本轮拒删目录只登记，不重试或换工具绕过 |

## 后续门禁

开发隔离验收不代表整个 P4 使用验收完成。G1 授权归属/失效闭包需先解决合同与既有记录
迁移问题；真实 QQ 需单独给出测试账号、对象、时段、费用与回退；来源 soak 需授权期限。
七模拟日不能代替二十真实日。长期状态见 [source-soak](p4-source-soak.json)，
真实 QQ 清单见 [p4-real-qq-acceptance.md](p4-real-qq-acceptance.md)，
配置与回退见 [p4-operations.md](p4-operations.md)。
