# 耦合度与技术债优化计划

审查基线：`9ca37059d9b86afdb572eb9aba1f43133d5b77fd`，日期：2026-10-28。
本计划保留全仓审查的 A3–A11 编号，列出后续可独立验收的候选切片。
已获授权按优先级继续修复；结构性工作按可独立验证的切片推进，新增机制先按项目规则讨论。
执行、审阅和发布状态继续由项目既有审阅台账及唯一整合负责人维护。

## 当前修复与后续调查的接缝

A1 修复对象是 `/internal/v2/affect/reset`：沿用
[`ApiStore`](../../companion/api_store.py)，在执行线程内创建独立连接、调用现有
`AffectEngine.reset`，事务取得写锁后采样时间，完成原有独立日志与数据库更新。
针对性测试覆盖锁等待期间健康响应、并发同请求幂等、连接关闭、鉴权和输入校验，
以及真实 HTTP 与独立 Worker 的派发失效、UNKNOWN 和旧备份回放场景。

A2 同步 `/internal/v1/deliveries/lease` 的
[发布合同](../../contracts/openapi.v1.yaml)与当前 `LeaseRequest`，使用 owner authority
引用及账号投递条件。双向示例校验与字段集合检查随产品测试运行。
这些验证的范围是投递租约合同；其他 API 合同需要按实际调用路径分别核对。

继续修复的范围为以下原生操作。它们使用各自拥有的连接，返回已物化结果；控制台的
会话检查和操作回执继续使用同一份规则。

| 路由 | 隔离与复核 | 验证入口 |
| --- | --- | --- |
| `/v1/console/affect/reset` | 独立 Store 执行回执和重置；取得写锁后以及实际重置事务内复核会话、Origin、CSRF、凭据与启用状态 | [锁等待、幂等与排队撤权测试](../../tests/test_console_reset_isolation.py) |
| `/internal/v2/readers/{adapter_id}/{request_id}/result` | 原生结果处理使用独立 Store，取得事务后采样租约时间；保留来源授权、目标与计量处理 | [Reader 隔离测试](../../tests/test_reader_status_isolation.py)、[探索回归](../../tests/test_exploration.py) |
| `/internal/v3/readers/{adapter_id}/{request_id}/result` | 同一连接组装现有 Reader 依赖；复用来源和图片登记逻辑，避免 ContentService 启动清理；保留视频后续任务与目标状态处理 | [Reader 隔离测试](../../tests/test_reader_status_isolation.py)、[独立 Reader 集成](../../tests/test_reading_integration.py) |
| `/internal/v1/ops/status` | 完整视图在独立 Store 上执行原有状态检查；摘要由后台线程执行现有只读快照，缺失数据库仍不创建状态 | [状态隔离测试](../../tests/test_reader_status_isolation.py)、[诊断回归](../../tests/test_ops_status.py) |
| Reader v2/v3 的 `/lease` 与 `/{request_id}/validate` | 独立连接调用现有租约与来源授权规则；领取事务取得写锁后计算期限及计费周期；视频租约继续要求显式能力并绑定原媒体额度 | [Reader 准入隔离与撤权检查](../../tests/test_reader_admission_isolation.py)、[真实读取前授权检查](../../tests/test_source_grant_preflight.py) |
| `POST/DELETE /v1/console/session` | 独立连接执行登录/退出；取得事务后校验当前凭据、Origin、会话与 CSRF，计算会话期限；失败次数和会话上限保持事务内处理 | [会话隔离、排队失效与并发限额](../../tests/test_console_session_isolation.py) |
| `/internal/v2/source-grants` 注册及 `/{grant_id}/revoke`、`/{grant_id}/forget` | 独立连接执行原有授权操作；注册取得锁后校验适配器与有效期；撤销保留历史，遗忘保留独立日志及派生闭包回放 | [来源授权隔离与恢复检查](../../tests/test_source_grant_api_isolation.py)、[派生证据遗忘](../../tests/test_source_grant_erasure.py) |
| `POST /v1/console/source-grants/{grant_id}` | 回执和原生操作使用同一独立连接；原生撤销/遗忘事务取得锁后再查会话，回执登记后的注销不能继续产生效果 | [Console 来源授权失效与幂等](../../tests/test_source_grant_api_isolation.py) |
| `PUT /v1/console/configuration`、`PUT /v1/console/cloud-budget` | 独立连接执行原有版本化变更；业务事务内复查会话并采样时间，保留加密、幂等、版本冲突及原费用快照 | [配置隔离与 UNKNOWN 保留](../../tests/test_console_settings_isolation.py)、[预算账本回归](../../tests/test_cloud_budget.py) |

剩余 13 个 Console 写路由已迁移到现有 `ApiStore`，每个请求只构造所需原生服务：
记忆决策、日记修订和遗忘、运行模式、目标决策和继续、自主范围和调整决策、反思决策、
话题关闭、工具审批和撤销、投递对账。回执登记及业务效果分别复查会话；日记和反思的
前置证据清理也在取得事务后复查。工具审批期限在取得写锁后计算。Goal、话题关闭和
对账沿用支持外层事务的原生实现；其他服务在原事务中接收与现有设置/授权操作相同的
`owner_check`，不合并跨事务的遗忘日志和回执流程。

[Console 写入隔离测试](../../tests/test_console_write_isolation.py) 覆盖 18 种动作，
包含全部 13 个路由的真实 HTTP、独立 SQLite 写进程、重复请求，以及排队或回执登记后
由另一连接注销会话的检查。[原生失败回放](../../tests/test_console_operation_failures.py)
继续检查确定拒绝、未知错误和进程死亡后的回执语义。

## 主 API 同步候选清点

范围为 `api.py` 内不含 `await` 的 POST/PUT/PATCH/DELETE 路由，共 64 个。
这是按入口及调用代码形成的静态清点，不是 64 个已复现缺陷，也未涵盖 GET 或其他
路由模块内的同步工作。2 个固定拒绝入口不访问 I/O，其余 62 个需结合真实调用验证。

| 分组 | 路由数 | 主要接缝 |
| --- | ---: | --- |
| 记忆与收件 | 3 | 离线 memory-control、Core.ingest、记忆决策；涉及语义与恢复初始化 |
| 原生业务 | 21 | 工具、目标、Life、整理、反思、观察、同意、日记及账号素材 |
| 媒体 | 14 | 视频媒体准入/回执/结果，表情语义/素材/发现任务 |
| 评估 | 2 | 评估预览与提交 |
| 授权、责任与投递 | 22 | owner/live-text 凭据、recall、停止、结转、对账、账号动作 |
| 固定拒绝 | 2 | M0/M1 tasks 与 approvals，保留直接拒绝 |

后续按数据库写锁、日志读写/fsync、模型预热与 CPU 工作分类验证。授权、费用与 Bridge
发送链的业务改动由原协调对话负责；本切片只处理 API/Console 调用隔离。

A3 已按用户选择实施显式按需组装，见 [架构说明](../architecture.md)。Core 的原有业务
方法集中在借用连接的 `CoreSession`；进程入口继续使用 Runtime 启动与恢复。原 Reader
独立工厂已移除，Reader、Console 与 Core 共用 `Services`。语义预热延后到实际语义使用，
Content 构造清理改为进程启动显式调用。

在原 64 个同步候选中，本切片接入了 25 个原生业务入口（包括记忆决策、收件与评估），
保留原业务事务；剩余 39 个入口含 2 个固定拒绝和 37 个尚待迁移/验证的同步 I/O 候选。
[服务组装检查](../../tests/test_service_composition.py) 覆盖按需构造、独立连接、语义失败、
身份失败关闭、遗忘回放及真实 HTTP 锁等待。上下文内容与模型编排不在本切片调整。

## 候选切片

| 编号 | 影响与源码依据 | 建议实施范围 | 完成条件 |
| --- | --- | --- | --- |
| A3 | [Core](../../companion/core.py) 集中组装服务和上下文；[API 启动](../../companion/api.py) 构造完整 Core | 先梳理 API、聊天、Life 与其他 Worker 的实际依赖；沿用 Runtime 和现有服务构造方式，分离组装与业务编排；上下文组装另作切片 | 非语义入口无需预热语义索引；需要语义能力的入口仍正确拒绝不可用依赖；身份和遗忘日志初始化、失败清理、真实进程启动通过 |
| A4 | [预算日志](../../companion/paid_budget.py) 和 [Bridge 校验](../../companion_onebot_bridge/owner_runtime.py) 的完整读取、折叠成本随历史增长 | 首先测量同一同步事务中的读取次数、折叠次数、字节数和锁持有时间；复用 802 已采用的事务内批量校验思路，先消除重复工作 | 多种合成日志大小下结果、账本哈希与原行为一致；STOP、同大小篡改、截断、DB 变化均失效；无跨 await 的权限复用；报告规模与耗时而非仅小样本均值 |
| A5 | [预算准入](../../companion/budget.py)、[owner 授权](../../companion/owner_authority.py) 与业务模块相互依赖 | 选择一种现有能力，梳理准入、最终派发、结算分别需要的业务知识；业务来源检查留在所属模块，公共层集中费用责任 | 该能力的新增分支集中于明确位置；有调用前拒绝、等待期间撤权、已派发 UNKNOWN、迟到结果等回归；模型适配器继续不负责存储和权限决策 |
| A6 | `jobs`、`outbox`、`model_usage` 的写操作分布较广；见 [聊天等待](../../companion/agent_chat.py)、[恢复](../../companion/backup.py)、[投递](../../companion/transport.py) | 先选一个高频状态动作收拢规则，如等待外部结果或取消旧回复；清点全部调用者，由状态所属模块提供操作 | 重复调用、租约过期、旧令牌、崩溃恢复和遗忘并发结果一致；多表变更保持原事务；源码检查确认旧规则已由所有调用者统一使用 |
| A7 | [Settings](../../companion/config.py)、[授权能力哈希](../../companion/owner_authority.py)、[配置展示](../../companion/operator_config.py) 和前端映射多点维护 | 先对现有能力建立字段/场景映射一致性校验；再按领域收拢重复配置定义 | 环境变量、TOML、控制台变更及已冻结授权合同得到一致解释；兼容性与迁移要求明确，配置修改不会静默改变已批准权限 |
| A8 | 多个测试从 `tests.test_*` 导入共享辅助代码；见 [历史事务测试](../../tests/test_owner_history_transaction.py) | 优先迁移一个被大量引用的辅助函数组到已有 `tests/support`，原子更新调用者；按依赖分组逐次交付 | 用例身份与参数集合可核对；无共享数据库/账本污染；独立子进程仍导入当前候选；测试结果、临时目录清理和代表性运行成本有证据 |
| A9 | [模型传输](../../companion/model.py) 与 [Bridge 网络](../../companion_onebot_bridge/network.py) 使用 HTTPX 私有成员 | 清点传输借用、连接关闭及最终检查位置，优先在既有版本内收拢访问点；升级时核对所用版本的上游实现 | mock transport、连接复用、取消、请求钩子改写、最终字节封装、派发拒绝与 UNKNOWN 责任全部通过；借用的连接池不会被提前关闭 |
| A10 | [控制台脚本](../../companion/console_static/app.js) 集中保存页面、会话和草稿状态 | 先分离请求与会话逻辑，再按页面拆分；补足实际返回值类型；同步维护静态资源打包与路由清单 | 类型检查、实际浏览器和代理静态资源验证通过；退出/过期后响应不回填，切页取消、草稿保留、写入幂等与错误呈现不回退 |
| A11 | Reader 和语音使用部分 Core 命名空间内的纯协议；[Reader 构建](../../deploy/source-reader.Dockerfile) 手工复制指定文件 | 在下一次相关协议变更时评估归入已有独立协议包，统一调用者与打包清单 | 独立最小安装可导入并校验原协议；不加载 Core 业务或存储；线格式、签名域及容器构建结果一致 |

## 依赖与推进顺序

1. 先调查其余 API 阻塞路径，同时评估 A6 的一个状态转换切片。二者各自验收，避免
   在线程迁移中顺带改变业务状态规则。
2. A4 先完成规模基线与重复工作定位；A5 先完成一种能力的规则归属。若需要新的增量日志
   协议、检查点或准入机制，按 [AGENTS.md](../../AGENTS.md) 先讨论缺口、替代方案和范围。
3. A3 使用前两步明确的模块接口渐进拆分；A7 随相应领域调整同步完善配置映射。
4. A8 按被当前改动实际使用的辅助代码逐步迁移；A9、A10、A11 分别交付，避免与产品修复
   或验证基础设施变更混入同一候选。

下一轮以主 API 的原生业务入口为优先范围，沿用 Console 已明确的事务与服务依赖。
费用准入、结转与模型传输的结构调整须与当前文件所有者协调，避免跨切片双写。

## 验证与交付

沿用 [开发与验证](../development.md) 的独立工作树、定向检查和最终 CI 回执流程。
每个切片说明可观察的前后行为、受影响调用者、保留的不变量、已执行检查和未覆盖范围。
跨 API/配置/Worker 的变更使用真实入口及独立 Worker，外部供应商传输使用替身。

授权、幂等、费用预留、UNKNOWN、来源证据和遗忘恢复属于验收合同。重复验证的删除必须
证明仍处于同一有效的一致性范围；网络等待后的最终复核保持独立。
成功回执按同一候选和环境复用。全量 CI 在候选冻结后执行，发布继续由原协调者负责。
