# QQ 空间逐稿公开审批

2026-10-31：Core 已实现“Life 草稿 → 待审公开稿 → 主人逐稿确认来源分享 → 原生账号投递”。
配置默认关闭，M0/M1 继续不执行 QQ 空间。验证使用模拟平台传输，真实账号兼容性尚未验收。

## 启用与授权

- 原 [QZone 来源与 Life 草稿许可](qzone-reading.md)继续生效。只能消费已结算且仍有效的
  `QZONE_DRAFT` 原始结果，不重新调用模型；生成时与当前 OwnerAuthority 必须一致。
- `social_autonomy.enabled=true`、`scope=qq_social_v1`、有效的 `authorization_sha256`，
  以及 `qzone_enabled=true`、`qzone_public_enabled=true`。
- 当前原生 OwnerAuthority 须含 `social`、`account_actions`、`qzone_publication`，
  `capability_policies.account_actions` 绑定原账号动作授权摘要，
  `capability_policies.qzone_publication` 绑定完整 SocialAutonomyConfig 摘要。
  用原 `owner_authority.capability_hashes(settings)["qzone_publication"]` 计算当前能力摘要，
  不能用开关、普通私聊授权或来源读取许可替代公开发布授权。
- Core 与独立 OneBot Bridge 同时更新。公开计划使用冻结合同 `schema_version=2`，
  只允许 `qzone.publish`、`ugc_right=1`、空目标列表，且必须有原生授权。
  原主人可见计划仍使用 schema 1、`ugc_right=16`、仅主人目标。

本切片只支持纯文本说说。昵称、签名、头像、图片附件、点赞和评论不由此流程附带执行。

## 控制台操作

在“QQ 资料行为 → QQ 空间公开发布”选择已有空间草稿，编辑完整正文并“保存待审稿”。
随后“审阅公开稿”显示发布账号、所有人可见的受众、有效期、完整正文及每份来源正文快照、
链接与指纹。主人逐稿勾选“我确认这份稿件可以公开分享上述来源”，再批准公开发布。
勾选默认关闭；修改正文会清除勾选，并要求保存后重新审阅。

保存、修改、审阅都不创建投递。批准在同一事务内记录精确摘要和分享确认，再沿用原生
Social reservation 与 Outbox 预留次数。已批准稿不可修改；撤销后需要新 Life 草稿。
一份 Life 草稿最多形成一个公开计划。有效期最多为原草稿有效期与策略 TTL 的较早者，
编辑只可缩短它。到期、授权/配置变化、STOP 或来源变化后，旧稿不能重新绑定新授权发送。

## HTTP 合同

主人 control Bearer 接口前缀为 `/internal/v2/qzone/publications`；
Console 同等接口前缀为 `/v1/console/qzone/publications`，要求当前主人会话、同源 Origin 与 CSRF。
写入均要求 8–128 字符的可打印 ASCII `Idempotency-Key`，事务内再次验证主人凭据。
相同键与正文返回该计划的当前结果，不重新执行；相同键改变请求返回 409。

| 操作 | 请求与结果 |
|---|---|
| `POST <prefix>` | `job_id`、原 `expected_content_sha256`，可选完整 `text`、有时区 `expires_at`；返回 201 与待审计划 |
| `GET <prefix>` / `GET <prefix>/{plan_id}` | 最近三十条或指定计划的审阅/投递状态，`Cache-Control: no-store`；失效或已擦除来源不再显示正文 |
| `PUT <prefix>/{plan_id}` | `expected_payload_sha256`、完整 `text`、可选缩短的 `expires_at`；只允许尚未预留投递的待审稿 |
| `POST <prefix>/{plan_id}/decision` | `expected_payload_sha256`、`decision=APPROVE/REJECT/REVOKE`；APPROVE 必须显式传严格布尔值 `source_sharing_confirmed=true` |

冻结摘要覆盖账号、完整正文、全部原模型来源上下文的快照、受众、策略与有效期。
请求不能指定平台方法、账号、受众、目标列表或附件。更改任意冻结字段都不能沿用旧确认。

## 来源分享与隔离

按主人确认的范围，精确 QZone 来源可以在这一份公开稿中获得分享许可；这不修改
SourceGrant、Life 草稿许可或全局来源能力。原 `public_source` 仍拒绝账号授权来源，
普通 Reader、自动分享与其他发布路径不会因此接受 QZone。
私聊、上传、私人生活记忆、日记、FICTION 不能通过本流程获得分享豁免。
检查覆盖模型实际看过的全部来源，不能只保留公开引用来隐藏私人上下文。

复用 `social_autonomy_plans`、来源依赖、reservation、Outbox 和原账号回执；没有另建发布队列。
审批和幂等操作收据存原 `audit_log`，仅保存请求/载荷摘要、决定和确认标记，不复制正文。

## 发送、撤销与结果

独立 Bridge 沿用原账号动作链：领取、begin-send、实际网络边界的 Core 复核、冻结参数检查、
原生结果回传。批准不等于已发布；只有原生成功码和真实说说 `tid` 的类型化回执可显示平台已接受。
请求超时保留 `DELIVERY_UNCERTAIN` 与次数责任，不自动重试，不改正文或受众降级重发。

拒绝稿件不创建投递。撤销批准会取消尚未开始的投递；已开始的动作保留不确定性与责任，
仍需按原账号对账流程核对。撤销不删除平台上已经发布的说说。
来源 `forget`/撤回通过原 ErasureLedger 擦除派生正文并拦截后续发送；旧备份恢复重放擦除，
恢复后的旧批准不能重新启动投递。已执行的用量、原生回执与未知责任按原合同保留。

## 依据与验证

公开受众字段依据固定 SnowLuma 提交
[`send_qzone_msg` 参数与请求实现](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/qzone.ts#L184-L237)：
`ugc_right=1` 表示公开，公开请求不附 `target_uins`。审批、取消与外部执行继续复用
[已记录的 DSH 对照及本项目机制](../design-future/external-capabilities.md#实现与来源依据)，不引入插件运行时。

`tests/test_qzone_publication.py` 检查逐稿确认、编辑失效、幂等、私人来源、会话/CSRF、
STOP/到期/开关和撤销责任。`tests/test_qzone_publication_integration.py` 从签名主人输入与真实
HTTP 入口出发，依次运行独立 Reader、Life Worker 和生产 Bridge 投递循环；外部模型和 QQ
provider 使用模拟传输，覆盖公开参数、真实类型回执、UNKNOWN 和最后发送边界撤销/遗忘。
`tests/test_qzone_publication_browser.py` 使用原生 HTTPS/Nginx 控制台验证保存、修改、重审、
逐稿勾选、批准与撤销，并检查桌面和窄屏审阅。
