# QQ 空间草稿与发布链离线验证

日期：2026-10-24。基线：`85c98ec85c1b86929fdb5e8525234f64bb6d43bb`。
本阶段只增加回归测试，不更改运行配置、生产代码、数据库或发送权限。
补充材料中的两张截图未实际附加，本文没有据此认定任何真实经历。

## 事实与数据流

当前路径为认证 Reader 公开观察 → `social_autonomy_runtime._candidates` →
`SocialAutonomy.plan` → 原模型预算准入/结算 → `FrozenPlan/READY` →
`reserve` → 原 Outbox 的 `qq_account` lane → 认证 HTTP lease/begin-send →
`account-actions/send-check` → Bridge `send_qzone_msg` → 原生结果/UNKNOWN →
原核对 journal。模型适配器不负责权限、存储或发送。

`public_source` 只接纳实际取得、未删除、未过期、具有公开获取证据的观察；
拒绝带 source grant 的私有资料、私人文档及不可核实正文。私人触发输入可以成为
删除依赖，但不进入模型的公开来源正文。私聊和日记不能因存在而成为空间取材。
删除沿原 ledger 清除派生正文；模型费用、发送尝试和未知结果责任保留。

`READY` 是经现有独立账号授权产生的可投递计划，后台会自动 reserve，
**不是待人工审核草稿**。当前 Console 展示计划、投递、原生回执及来源状态；
仅人工标记 SENT 或缺少有效平台回执时显示待核实，不伪装平台成功。

模型只能提出内容与来源引用。Core 冻结 `ugc_right=16` 和唯一主人 QQ；
Bridge 再独立核验并构建参数。这里是指定主人可见，区别于平台 `64` 的仅账号自身可见。
M0/M1 的默认配置不启用本链；测试内显式合成授权不构成生产授权。

[PR85](https://github.com/yanyuhanyue/companion-core/pull/85) 与
[PR89](https://github.com/yanyuhanyue/companion-core/pull/89) 在核实日仍 OPEN；
[PR91](https://github.com/yanyuhanyue/companion-core/pull/91) 已 MERGED，正文明确包含两者，
合并提交为 `69ea775279c0e20ef524db8cbd1caba4819fc77e`。本轮不重复合并。
本地部署 receipt 指向 PR100/main 本文基线，仅证明所记录的部署；其中表行存在
不能证明 social 已开启。没有连接生产、再次探测平台或推断真实空间验收通过。

## 一手实现核实与复用

下列版本均为实际读取源码时固定的版本；参考行为不是兼容性验收。

| 来源及固定版本 | 实际实现 | 本项目复用与限制 |
| --- | --- | --- |
| DSH `22ddee3792402c3e70ec5f86a63c3e3e895a2f60`，Schedule/Jobs `0.2.1-rc.3`：[Schedule runtime](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/schedule/schedule/src/runtime.ts)、[Jobs plugin](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/jobs/tool-jobs/src/index.ts) | Schedule 恢复持久任务，先追加原 Session inbox、flush 后保存 delivery；Jobs 使用共同 registry，完成可唤醒或安静等待 | 复用持久意图、结果和独立执行的分层原则；继续用 Core Wake/worker/Outbox，不安装第二调度器，不执行 DSH。旧搜索结果的 session-local 描述不代表此版本 |
| QQ Agent Plus `aa781ab0eb3c8506dc0185f8b5454fdd77ad7d75`，`0.9.5`：[LINUX.md](https://github.com/sakurawwwxh/qq-agent-plus/blob/aa781ab0eb3c8506dc0185f8b5454fdd77ad7d75/docs/LINUX.md)、[daily-moments.js](https://github.com/sakurawwwxh/qq-agent-plus/blob/aa781ab0eb3c8506dc0185f8b5454fdd77ad7d75/src/features/daily-moments.js) | 独立 preview；发布前检查权限/运行条件并保存 publishing；缺 tid/超时保留 publish-unknown；启动将 publishing 转 unknown；草稿发布不再次生成；支持核对 | 借鉴预览与发布分离、先记意图和 UNKNOWN 阻断；使用 Core 原有存储/预算/回执。群聊摘要取材、force 重复风险选项和按最新配置决定 visibility 不迁入。文档默认测试基线 v1.14.22，明确未默认跨 major 升至 2.x |
| AstrBot Qzone `f474c50c9b517655d8475df34ad0c129b70193d4`，`v3.1.3`：[campus_wall.py](https://github.com/Zhalslar/astrbot_plugin_qzone/blob/f474c50c9b517655d8475df34ad0c129b70193d4/core/campus_wall.py)、[scheduler.py](https://github.com/Zhalslar/astrbot_plugin_qzone/blob/f474c50c9b517655d8475df34ad0c129b70193d4/core/scheduler.py)、[service.py](https://github.com/Zhalslar/astrbot_plugin_qzone/blob/f474c50c9b517655d8475df34ad0c129b70193d4/core/service.py) | 投稿先保存 pending，管理员通过调用发布；撤稿检查投稿人；Cron/DateTrigger 提供自动发布；发布成功后填 tid/approved | 可参考待审内容与平台 tid 分离。抽查发布路径先外部发布后保存，不能作为 Core 的崩溃安全 UNKNOWN 合同；不迁移 Cookie 客户端或 APScheduler |
| QzoneActive `c0d1f293f1bfa32a830473f18e6d50e2d5e070d6`，`v1.0.5`：[main.py](https://github.com/Catfish872/astrbot_plugin_qzoneActive/blob/c0d1f293f1bfa32a830473f18e6d50e2d5e070d6/main.py) | 读取 InitiativeDialogue 的 ai_schedules.json，产生每日随机时点并生成发布；可选点赞评论 | 只参考触发机会与内容选择分开；日程文件不是真实共同经历证据，不复制随机生活、互动或第二任务循环 |
| SnowLuma `v2.0.3` / `db142db9e6398c779283eab9afc3552da1e2c361`：[qzone.ts](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/actions/qzone.ts)、[接口文档](https://snowluma.github.io/api/qzone/send_qzone_msg.html) | send_qzone_msg 接受 content/images；可见值 1/4/16/64/128，16/128 必需非空 target_uins；返回说说 tid | 继续使用原 Bridge 的纯文字、16、单主人参数；无真实平台调用，未据源码宣称 2.0.3 发布兼容已验收 |

## 合成验证与失败恢复

增强 `tests/test_account_actions_entrypoint.py` 的真实回环 HTTP 服务与独立 Core Store worker：

- 成功、明确拒绝、UNKNOWN、发出前取消均保留原 Outbox；重复回执幂等，冲突回执不改账。
- begin-send 之后删除来源、STOP、关闭 social 或计划过期，最终 send-check 被拒；
  `dispatched_at` 仍空，可用原无 I/O 证明取消，费用不退款。
- 删除在途来源及 UNKNOWN 触发 `LIVE_TEXT_RECONCILIATION_REQUIRED`；
  关闭配置触发 `LIVE_TEXT_RUNTIME_CHANGED`，均为更早的全局门禁。
- 关闭并重新打开独立 worker 后，已发/失败/未知/取消都不产生第二次模型调用或新投递；
  UNKNOWN 的 lease 返回需核对，已终止记录返回空队列。

`test_draft_intent_is_not_a_publish_proposal_or_a_delivery_permit` 防止上下游误接：
把 QZONE_DRAFT 错送进现有发布 planner 会被拒绝，零 reservation/Outbox，已发生的
合成模型费用仍结算且同候选不重付。它不是持久草稿功能或来源授权实现。

复用原测试覆盖严格可见范围、零授权、预算不足、来源有效性、来源删除、
新输入抢占、两个连接争抢配额、模型 UNKNOWN、Bridge UNKNOWN 不重复 I/O、
STOP 发送边界、恢复门禁及删除后晚到回执。另检查 Console 不虚报成功、
原人工核对保留未知回执和原配额。验证 receipt 由状态文件引用；最终 CI 为该候选唯一 CI 收据。
所有外部模型/OneBot 均为合成替身，没有付费、QQ 发信或平台读取。

## 与 Life 的接口及后续范围

已批准：默认关闭的预览/SHADOW，分别核验草稿用途与来源。Life 拥有来源候选、
topic、上游 intent、activity 结果及既有授权对象扩展；root 整合共享 Core/private_life/
cleanup/schema。本阶段没有新建 LifeIntentPlanner、授权库或跨应用事实库。

下游所需语义：每项来源绑定原授权对象身份与 revision、用途 `qzone_draft`、
来源指纹、有效期和不可扩大的受众上限；具体字段名以 Life 交付 DTO 为准。
源撤回、删除、到期或授权修订必须使依赖草稿失效并按原 ledger 清除派生正文。
私聊/日记默认不提供；公开取材也不能让模型扩大受众。
草稿只留在 Life 的 SHADOW activity，不能转换成 READY、调用 reserve 或创建 Outbox。
当前尚无该 DTO 的已集成消费者，因此本分支不宣称完整草稿闭环已实现。

后续真实发布还缺逐次内容批准与原发布计划的绑定；最小方案是批准准确草稿内容摘要、
逐项来源与授权 revision、到期时间及主人受众，再复用已有 FrozenPlan/Outbox/send-check。
编辑或撤销后旧批准无效；已发送/UNKNOWN 只走原核对，不重发；删除不能撤销已完成的外部发布。
无需新任务或发送框架，但这一发布批准接口须另行讨论并取得实际发送授权后实施。

有限 Beta 需要明确：候选提交/CI、实际账号及主人、准确内容和来源授权、纯文字单次上限、
起止窗口及模型费用上限、16+主人名单、STOP 执行人与 UNKNOWN 核对人。
如需部署、平台只读核对、真实模型或 QQ 写入，各自列入实际授权；本阶段均未执行。
