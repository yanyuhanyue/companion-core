# Core 素材运行时合同（B1b）

当前是独立 B1b 本地接口里程碑，基于冻结 B1a `64ac829`；尚未完成
Settings / Core Worker 集成或独立进程端到端验收，不代表可生产切换。
仍需 B2 向量/发现、B3 历史内容副本擦除与原责任闭环、B4 独立镜像的相应门禁。

## 单主与兼容

临时 `meme_catalog_owner` 仅允许 `bridge` / `core`，默认 `bridge`。
默认值不进入旧 Settings / Bridge 快照，默认 paid、owner、meme 摘要及投递材料保持原语义。
实际非默认来源规则和 cooldown 必须序列化并进入 meme 策略摘要，即使 selector 是 bridge。
Core 模式摘要总是包含 selector、exact 来源规则和 cooldown；旧授权不能取得新模式权限。
该 selector 是迁移窗口，最终退休 Bridge 业务写入，不永久维持两套可写目录。

BridgeConfig.snapshot 基于 asdict 的新拷贝，仅在实际 selector 为 bridge 时删去新增字段。
window 历史 owner_authority 缺项分支不删除 core selector。回归使用从冻结 B1a 原代码
生成的默认及历史形状 golden hash；不得将真实含凭据快照打印或另行留存。

Core 模式禁止 Bridge 候选 sidecar、offer、caption 创建、embedding 和 discovery 执行；
旧 caption 状态及 discovery result 接口保留原结算职责。Bridge MemePump 不启动业务任务，
lazy factory 直接构造 CoreMemeTransport，只创建原 pin 和 delivery binding 表。
旧历史内容副本存在本身不是已完成擦除；真实切换仍受 B3 门禁约束。

## 集成接口

Context 集成人独占 `companion/config.py` 和 `companion/core.py`。精确合同：

- `meme_catalog_owner: Literal['bridge','core']` 默认 bridge，仅默认值 exclude。
- `meme_trusted_sources: list[companion.meme_policy.TrustedSource]`，最多 32 条，空列表 exclude；
  Settings.load 按 JSON 解析对应环境变量。规则字段与原 TrustedReviewer 一致，精确绑定
  repo/revision/license/license_path/license_sha256/asset_prefix/usage_basis。
- `meme_cooldown_seconds` 严格整数 0..86400，默认 1800，仅默认值 exclude。
- `await self.memes.prepare_offer(job)`：命令判定后，原 agent/context 构造前；
  恢复旧 agent conversation 时只验证原冻结值。非 Core、非 chat、已分类控制命令为 no-op。
- `self.memes.check_offer(job)`：原 `_check_context_snapshots` 中复核；原 agent/budget 的
  source_check 每次实际模型请求前、await 后及 Outbox 提交前均经过它。错误必须传播。
- Core 模式 waiting_offer=False，原 Bridge 模式的两秒等待合同保持不变。

领域生成过程不访问网络、模型、向量服务或 Bridge 数据库；只使用已成功且费用已结算的
原生 caption，移植原 caption_eligible、context_allows、分词及词法评分，最多八个候选。
正负/GIF 固定语料仅证明词法规则等价，不声称完整向量质量等价。局部排序超时或没有
合适候选冻结为空；STOP、来源撤回、epoch、授权和费用责任错误不能被泛捕获成空候选。
STAGED 表示内容保管；当前 owner/private、目的、原始来源证据和租约分别复核。

## 冻结绑定 v1

保留 MemeOffer / ImageIntent DTO 和原图 SHA candidate_id。每个事件对相同字节
确定唯一的 source/custody 记录，排序相同按 asset_id/caption_job_id 确定；保管目录仍保留
独立来源。选定来源撤回后，本冻结值不能改用同字节其他来源。

原 `meme_offer:<event_id>` 严格写入现存 job_id/event_id/reply_revision/query_sha256/offer
及 catalog_owner=core、binding_version=1、meme_policy_sha256、privacy_epoch、
最多八项 catalog_bindings。每项 AssetBinding 字段为：

`version=1, asset_id, asset_sha256, custody_sha256, source_sha256, record_sha256,
caption_job_id, caption_sha256, caption_identity_sha256, meme_policy_sha256, privacy_epoch`。

摘要使用现存 canonical SHA256 digest。每个入 prompt 候选的完整 source/reuse 祖先先写入
原 chat job_sources，后续每次请求重新核验。owner_delivery 原材料只增加
meme_binding_version=1 与 meme_binding_sha256；默认 Bridge 材料不增加字段。
Core 模式缺少新字段或具有旧 Bridge offer 时拒绝，不自动补签/提升。
擦除沿现存 Ledger handler 清除资产内容与派生 offer；残余摘要/ID 不授予读或发送权限。

原 Outbox 是按物理 byte SHA 的占用及 cooldown 权威，不按许可别名绕过；PENDING、
RETRY_WAIT、LEASED、SENDING、DELIVERY_UNCERTAIN 保持占用，SENT 使用原 sent_at。
在原 enqueue 事务中原子检查；并发验证必须是独立连接/进程争同一原权威 SQLite。

## 租约取图与最终发送

`POST /internal/v1/memes/deliveries/{message_id}/asset` 使用原 Bearer 与 OwnerAuthority。
请求严格为 authority/event_id/reply_revision/lease_token/content_sha256，不接受自由资产
选择、URL、路径或新费用。服务验证原 Outbox、owner_delivery、private 目标、当前
LEASED/SENDING 租约、revision/source/epoch/策略及冻结来源后读取相同原图；无新工作或
累计发送 admission。响应字段严格为：

`message_id, delivery_key, event_id, reply_revision, authority, content_sha256, lease_until,
asset_id, asset_sha256, source_sha256, custody_sha256, record_sha256, caption_sha256,
binding_sha256, mime, byte_length, data_base64`。

运输硬上限为原图 4MiB、JSON 6MB；实际还取当前 paid meme_input 字节/像素上限的较小值，
并保持原 PNG/JPEG/GIF、2048 尺寸、120 帧、总像素限制。4MiB 不会扩大当前 4,000,000
字节授权。Bridge 在独立 meme_asset_http 中复用受检 HTTP/private destination/最终 seal、
bounded_post，保留普通 images.py 的 64KiB 边界。对 echoed identities、租约、asset 组合、
canonical base64、实际 SHA/MIME/长度逐项验证，await 后重读 STOP/recall/新输入。
来源和 caption 的最终执行权限由 Core 的原发送检查复核，摘要不代替授权。

Bridge 原图仅在准备好的单次发送 RAM 中，成功、失败或取消时清理；不新建原图、
缩略图、caption、向量或 query 持久副本。PinCustody 复用原 PREPARED/SENDING/UNKNOWN/
SENT/CANCELLED 转移、原 NOT_SENT 证明及晚到 ACK；重复 ACK 不改原 sent_at。
原窗口 journal、IO_STARTED、租约、fence 和实际传输 seal 不变。

真实切换先用原 STOP/锁/handoff 闭环原模型、discovery、ACK/UNKNOWN 和原 SENT cooldown，
并通过 B3。Core 失败不自动启用旧目录。已有 Core 发送后，旧快照缺少后来来源/cooldown
事实，立即回退仅限对账后的兼容 text-only 状态；旧候选重启须另审逆向对账或原 cooldown
完全到期并完成擦除。不得覆盖旧快照、放行 UNKNOWN、退款或制造新成功。

## 本地验证边界

已执行领域来源冻结/撤权/费用/epoch、原 catalog pin 回归、Core transport 故障注入和
实际 native poke 签名/Worker/route 检查；测试 fixture 的修复及原始失败保留在唯一回执。
Settings / Worker hooks 接入后仍须完成签名 API + 独立 Core Worker + Bridge 进程 +
OneBot Mock 的合成验收、同字节双来源、并发同库、擦除/旧备份、崩溃及回退检查。
该文档不声明真人 QQ、生产切换、付费模型、镜像或完整 CI 已通过。
