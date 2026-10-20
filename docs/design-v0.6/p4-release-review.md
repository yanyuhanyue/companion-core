# P4 固定基线独立审查

任务 `CC-NEXT-20261020-P4`，2026-10-20。输入候选为
`df28f595f714219b78c88858af6ff4c57c055c14`。实时 HEAD/base/reviews/CI 的完整记录在
[p4-baseline.json](p4-baseline.json)。审查后修复放在 `codex/p4-isolated-acceptance`，
已验证实现版本为 `fd321dcaaa67e6f21b12290bc69d0e02c065860d`，
[实现 CI](p4-ci.json) 五个 job 全过。其后的文档证据收尾没有改动产品、脚本或测试；
该最终候选的 HEAD/CI 另在本轮 Draft PR 精确核对。没有调整或合并 #11–#16。

## 结论

输入候选发现 **1 个 P1、2 个 P2**，均已复现并最小修复。修复后的已审查范围没有
遗留新增 P0/P1。**P4-3 的账号授权生命周期闭包仍有需求缺口**，单独阻断该能力及生产门禁。
本报告不等于把整个五层依赖的每一行重新审计了一遍，也不构成合并或上线许可。

| ID / 状态 | 触发、影响和修复 | 可重复证据 |
|---|---|---|
| R1 / P1 / FIXED | [bilibili.py:147](../../source_reader/bilibili.py#L147)：view 正确但简介为空，fallback 的另一视频 bvid/aid/cid 原先可覆盖目标并归为原 URL 的 L2。合并网页数据前核对请求目标及已有身份。 | `test_fallback_page_cannot_replace_requested_video_identity`；原代码三个错配均 SUCCESS，修复后在 player/subtitle I/O 前拒绝；正确 fallback 保持可用。 |
| R2 / P2 / FIXED | [accounts.py:17](../../source_reader/accounts.py#L17)、[打开检查:37](../../source_reader/accounts.py#L37)：路径检查与打开不同步，原子替换后可读到未检查的授权文件。绑定 lstat/fstat 文件身份、普通文件、权限、大小与修改时间，并在读后再核对；POSIX 加 NOFOLLOW/NONBLOCK。 | `test_grant_replaced_between_validation_and_open_is_rejected` 原先失败、修复后通过；读中撤销、软链接拒绝及两种来源正常授权另有覆盖。 |
| R3 / P2 / FIXED | [heybox_source.py:122](../../source_reader/heybox_source.py#L122)：本地 `READER_BYTE_LIMIT` 会锁死账号，正常后续请求也 SOURCE_DISABLED。仅对两个明确本地预算错误免于账号熔断；认证/验证码/平台拒绝保留熔断。 | `test_local_budget_limit_does_not_latch_native_account` 原先失败，修复后先有界失败、后续有正常独立预算的请求可成功。 |
| G1 / REQUIREMENT GAP | Core 未保存 Reader grant 身份/版本/到期信息，也没有 grant 撤销通知。因此 Reader 拒绝新 I/O 不会自动删除既有兴趣、日记和分享草稿。 | `ObservationEvent` / `ReadResult` 无 grant 字段；`account_grant` 与两个来源的 verify 仅在 Reader 本地生效。P4-3 标 FAIL，不以模拟源删除代替该能力。 |

G1 需要明确的非凭据授权生命周期合同、既有来源归属迁移和对旧备份的撤销日志。
不能从未记录的信息反推旧内容属于哪个 grant。本轮保留该实质阻断，不给既有记录
伪造授权归属或自动执行大范围删除。现有人工逐来源遗忘端点可以形成真实删除日志，
但它不等于自动 grant 失效闭包；见 [回退手册](p4-operations.md)。

## 审查范围及防线

| 范围 | 状态 | 独立核对内容 / 限制 |
|---|---|---|
| #15 对 #14 | PASS（修复后） | 精确 `058cb62..a199e25`：29 个文件；重点 accounts、固定签名 GET、network、bili_tools、worker、observation。没有把累计所有 PR 当成这 29 个文件。 |
| #12 七项旧修复 | PASS | 日期别名和历史 COMPLETE 删除重放、暂停/FIFO、管理命令、主人深读额度、压缩响应拒绝、日记祖先 TTL、私密包排除均保留；后续探索变动仍检查深读额度。旧 #11 本身不含这些修复。 |
| #13 / #14 | PASS（受影响路径） | 来源 URL/公网 DNS 固定连接、只读预算/回执、文章正文与图片引用边界；其余沿用固定基线并由全量回归覆盖，不扩大独立审查声明。 |
| #16 增量 | PASS（回归） | 保留 32 图引用、批处理/单图调用、UNKNOWN 与 migration 0015 保护；本轮不重跑付费视觉。历史实际识别仅为 14 张中前 3 张。 |
| NO_PUBLIC_WRITE | PASS | 静态四工具仅 search/subtitle/comments/video_info；force/写/未知工具在 I/O 前拒绝。原版 MCP 只由设置明确四项环境变量的隔离 probe 启动。 |
| 预算 / UNKNOWN | PASS | GET 原子预留、字节/次数上限、超时保留未知占用、回执不重放；本地额度错误与账号有效性分离。 |
| 内容来源 | PASS | `external_untrusted`、L1/L2 明确范围、aid/cid/CDN/time spans 与目标一致；不注册 WATCHED、ASR、公开发布。 |
| 身份 / 删除 | PASS（现有合同） | owner/private、不同 Reader/Bridge token、指纹幂等、event_dependencies、显式源删除与最新独立 Ledger 重放。grant 自动撤销不在此 PASS 内。 |
| MIT / GPL 分离 | PASS | MIT notices 保留；正式 pyproject/uv.lock/Reader Dockerfile 无 bilibili-api-python/MCP 运行依赖；GPL 仅 `upstream-native-p3.lock` 隔离 QA。新包检查独立执行。 |
| Windows 文件安全 | PASS（私密目录边界） | lstat 与 FD 检查一致，ctime 语义差异有正向回归；不宣称能隔离拥有目录写权限的恶意本地用户或管理员。需继续保持专用目录 ACL。 |
| Linux 文件安全 | PASS（新 CI） | POSIX NOFOLLOW/fstat/权限与 symlink 用例实际执行；Linux 626 passed / 4 browser opt-in skipped。 |
| Linux Chromium / 目标主机资源 | UNVERIFIED / BLOCKED | 旧 sandbox 阻断保留。此次未连接、修改或重启生产服务和既有容器；没有新的目标主机峰值、网络或密钥挂载实测。 |

## 验证和跳过

Reader 修复相关三文件：**96 passed、1 skipped**，Ruff 与 diff 检查通过。
该 skip 是 Windows 本地不能创建 symlink；原有相同权限受限的表达图片测试也可能跳过。
原 CI 普通全量的四个 skip 对应显式启用才运行的四项 Chromium 测试，独立 browser-reader
job 执行它们；不是认证、预算、隐私或迁移用例被悄悄跳过。

新本地全量、打包、精确命令和最终结果统一记在 [p4-validation.md](p4-validation.md)，
避免把初始输入候选 CI 当作修复分支 CI。不存在真实账号、模型、QQ 或来源 SLA 的新验收。
