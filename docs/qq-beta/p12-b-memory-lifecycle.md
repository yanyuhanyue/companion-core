# P12-B：独立记忆控制与生命周期验收

基线为 `c6493d481676e1e6684fb49564c2557224950d20`。本包只进行了本地公开合成数据开发和验证；没有读取或复制私密库、连接 VPS、发送 QQ、调用付费模型或取得新的真人批准。当前部署镜像、实际业务库身份和本轮真人效果均为 `null`。原已结束窗口保持关闭。

| 工作 | 本包证据 | 真实准入 |
| --- | --- | --- |
| B1 原生确认、纠正、遗忘、最新 Ledger 恢复 | `OFFLINE_PASS`：原 Memory / FTS5 / BGE / Ledger / backup 链 | `NOT_RUN`，尚缺准确事实、真实来源、停止业务与逐项操作批准、手机确认 |
| B2 主人显式管理入口 | `OFFLINE_PASS`：真实本地认证 WS → 原 Core HTTP v1 → 原 Memory，默认拒绝 | `DISABLED`，必须先有业务 B1 签收和新的准确管理 scope |
| B3 检索评测与派生视图方案 | `OFFLINE_PASS`：已量化漏忆和候选误报，保留失败 | 多自然日与真实模型 Token 开销 `NOT_RUN`；不改检索路径，不启用图引擎 |

## B1：可审查的业务执行准备

[R5 私密申请模板](p12-r5-request.template.json)是未填写的准备材料，不能作为执行许可。实际资料只保存在获准的私密目录，公开记录只保留脱敏结果和摘要引用。每一步都绑定准确源码与运行镜像、当前数据库身份、原主人来源、最新 privacy epoch、Ledger 及全部既有 Budget / Window Journal 身份和头；任何缺项都保留 `null`。

1. 明确一条非敏感真实事实和对应本人原始来源，记录拟创建/确认内容、subject、fact_key、来源有效期与准确停止范围。业务必须已 STOP；使用原生 `Memory.add` / `Memory.decide`，不启用提取 worker。当前只读 R5 hash 不代表这次写入批准。
2. 保留创建/确认回执，分别检查 FTS / BGE 的直接和同义问题。另行批准准确旧 ID、拟纠正新值和修订来源后执行纠正，保留旧状态、替代链与冲突检查。
3. 另行批准遗忘目标及其**完整共享证据/修订闭包**。原 Ledger 必须先 fsync，再删除原始来源、修订、证据、向量及派生视图；写入或 commit 失败不能回答成功。外部 Ledger 已追加而 DB 未提交时先停，恢复必须先回放最新 Ledger。
4. 在技术网络隔离的新路径恢复一份遗忘前、已获准的加密备份；使用最新独立 Ledger，重建 FTS / BGE 再查旧新值。核对另一独立事实仍保留。不得覆盖原卷、回滚预算/窗口日志、恢复旧额度，不能把合成恢复算作业务恢复。
5. 另行准备有界 QQ 窗口与模型批准，验证可召回的已批准事实及纠正/遗忘后的不泄漏，由主人手机确认。管理操作本身没有模型调用或 Outbox 发信；验证聊天的预算另外计算，窗口最多五次模型尝试。

合成闭环的实现位于 `tests/test_p12_memory_lifecycle.py`。它实际运行原生确认、BGE 同义检索、纠正、原始 QQ 纠正来源删除、Ledger、旧加密备份恢复、FTS/BGE 重建和近期时间线检查；被遗忘记忆/向量均为 0，另一独立事实保留。另一个用例先完成并 STOP 原合成付费聊天窗口，再执行管理生命周期，比较原 Budget Journal 与 Window Journal 字节、`model_usage`、`model_admissions`、`model_budget_windows` 和 STOP binding 全部不变。

## B2：实现与权限边界

入口复用 `create_app`，路径是 `POST /internal/v1/memory-control`，使用已有独立 `control_token`。普通启动没有 scope，管理请求返回 `MEMORY_CONTROL_SCOPE_REQUIRED`。已有 live_text 的 slash 拒绝规则保持有效；引用、转发、卡片和图片都不进入此控制路径。

通过 `companion serve --memory-control-grant <私密 JSON> --memory-control-sha256 <批准摘要>` 显式加载一条命令的 scope 时，该 API 实例仅接受控制接口和存活检查，并在整个生命周期持有原五类 worker 的 OS 锁。它不启动模型或发送器，不改 `memory_enabled` 全局开关。所有其他写者功能必须关闭，原工作队列和不确定状态必须清零，旧真实窗口必须已 STOP。该启动命令本身不代表已经获得真实权限。

桥接侧 `memory-control-once --approval <同一私密 JSON> --memory-control-sha256 <摘要>` 使用 `COMPANION_MEMORY_CONTROL_TOKEN` 连接原 HTTP v1。接收器持有原 Spool 锁，复查原 STOP、历史 Window Journal 及 ACK 状态，使用既有网络 DNS/Host/认证边界和固定 SnowLuma 2.0.3 身份/状态/版本探测，最多接收 16 帧、30 秒内的一条命令。它不新增聊天窗口，不重置原 Spool，不调用 `send_private_msg`，失败不自动重试。

完整字段由 `companion.memory_control.MemoryControlGrant` 定义：

私密 scope 文件必须保存该模型 `model_dump(mode="json")` 的完整结果（包括默认值和 `null`），批准摘要是这份对象的 canonical JSON SHA256。加载器拒绝缺省字段、不同日期序列化及重复 JSON key，确保 Core 与独立桥接核对的是同一份准确 scope。

| 类别 | 精确绑定 |
| --- | --- |
| 独立批准 | grant ID、`execution`、主人批准引用、停止服务证据引用；真实模式还需 B1 签收、原 STOP window hash 和桥接 journal head |
| 动作/次数 | `remember`/`confirm` → `memory.write`；`correct` → `memory.correct`；`forget` → `memory.forget`；每个 grant 最多一条命令 |
| 数据 | 主人当前正文 hash；已有操作的 memory ID 与完整目标/共享证据/修订族 hash；新事实 subject/fact_key；不允许用新建 scope 顺带确认或冲突修改已有事实 |
| 来源 | 当前 owner/bot identity、protocol epoch、桥接配置/代码 hash、Ed25519 捕获公钥；事件 ID、source ID、正文、时间和 capture 签名逐项核验 |
| 运行态 | 当前 Core 配置/代码 hash、privacy epoch、最新 Ledger hash、原停止窗口/预算 custody hash、恢复 generation；有效期最多 15 分钟 |
| 敏感数据 | 额外 `sensitive_confirmed` 与独立确认摘要；凭据即使被确认也拒绝保存 |

只有当前认证 owner 的**纯文本正文第一个字符为 ASCII `/`**，且准确匹配 `/记住这个`、`/确认这一条`、`/纠正这一条` 或 `/忘记这一条` 才可执行。桥接只对实际认证 WS 接收的帧签名；控制签名使用独立域，旧 live_text capture 不能重用。它是本地接收证明，不是 QQ 平台公证签名。原帧不额外落一份不可遗忘的缓存，控制文本仅进入原 Core `events`，参与原删除闭包。

事件、Memory 变更、原 `runtime_meta` 中单次回执和 `audit_log` 在同一 SQLite 写事务中提交。成功回执只含 ID、状态、hash、privacy epoch；同一已签名输入重放返回同一回执，不再写记忆或计费。后续隐私变更会拒绝过时成功回执；新来源消耗同一 grant 会被拒绝。断线或无法确认的响应不得当作成功，先私下核对原回执和 Ledger。

修复了一个明确的恢复授权缺口：**控制前备份中既没有消费回执，也可能具有相同 privacy epoch**。恢复后仅依赖 DB 内幂等记录会允许旧 grant 再写。因此原 `restore` 额外记录新的随机控制 generation；旧 grant 必须重新批准，新的批准可以绑定新 generation。该标记不改变、重建或返还任何原预算/窗口日志。Ledger 已落盘而事务失败的回归也证明后续请求拒绝成功，先要求 replay，之后旧 epoch 被拒绝。

本地验证覆盖认证 WS/HTTP、错误 token、当前 owner/来源/正文/签名漂移、跨签名域、引用/卡片/转发/图片/CQ 注入、ASCII slash、期限、privacy epoch、Ledger 变动、其他 worker、敏感额外确认、目标变化、重连重放、次数耗尽、旧备份和 Ledger-first commit 失败。原只读聊天仍拒绝控制命令。

## B3：实际读数与下一步

可复跑入口是 `scripts/evaluate_p12_memory.py`，结果和数据见 [P12 检索实验](../experiments/p12-memory/README.md)。算法没有修改，也没有 model API 或真实 QQ 输入。原 P8-B baseline / experiment 都为 **7/8** 的完整上下文读数保持冻结；本包只测原生检索候选，不能把两种指标拼成改进率。

本次 50 条公开合成事实中，9 条独立留出查询有 7 项必要记忆，找回 6 项，漏掉休息日锻炼的间接问题；候选误报 0。3 条历史措辞探针找回 3 项必要记忆，但产生 1 条多余候选。纠正后的新值可召回，旧值泄漏 0；来源遗忘后及旧备份最新 Ledger 回放后，重建索引泄漏均为 0，独立事实保留。真实 Token、金额、手机效果和自然日样本全部为 `null`。

近期 1–7 天视图、owner/self 区分和原始证据继续使用现有实现。标签规范化、一跳关联和按自然月分组仅保留派生只读提案：必须由明确共享来源支持、最多一跳、最多两个邻接项、总候选不超过现有六项、重新核验所有原始来源/epoch/Ledger；不凭同标签建立事实关系，不生成第二个事实库。先积累真实漏例，再用新留出集与来源遗忘/重建对照证明收益；当前证据不足以准入图增强。

## 验证记录

Windows / Python 3.12，复用已有锁依赖和本地 pinned embedding 缓存：

- `./scripts/check.ps1 -TestFiles tests/test_memory_control.py,tests/test_p12_memory_lifecycle.py,tests/test_memory_commands.py,tests/test_memory_workflows.py,tests/test_m1.py,tests/test_erasure_coordination.py,tests/test_source_grant_erasure.py,tests/test_live_text_commands.py,tests/test_live_memory_recall.py,tests/test_live_history_memory.py`：全仓 lint 通过，189 项相关回归通过。
- 随后补上重连捕获幂等、完整 one-shot 桥接调用和私密批准文件 canonical 校验；对最终两个新增测试文件再跑 `./scripts/check.ps1`，全仓 lint 与 46 项通过。两轮有重叠，不相加。
- `scripts/evaluate_p12_memory.py` 完整执行一次最终公开合成数据评测和隔离恢复，结果见链接；缓存只读，无下载。

完整 CI 发现旧 schema 0003 备份测试仍要求 `runtime_meta` 全部行完全不变，
与恢复时新增的控制 generation 冲突。测试现明确断言唯一新增 generation 的格式，
然后继续逐列验证全部原有元数据及业务表；没有忽略整个表或放松删除、预算和投递断言。

本分支未重复全量本地基线。最终组合的完整 `./scripts/check.ps1` 及各 Draft PR 精确 HEAD 的 CI 由 P12 集成验收分别记录；本页不预填结果。业务 R5、真实 QQ 控制、手机确认、实际镜像及跨自然日效果仍为 `NOT_RUN` / `null`。
