# P8-C.2 认证 OneBot 接收合同

本阶段提供离线可验证的接收实现。真实 QQ、部署、手机可见回执、收费模型、恢复和
production_ready 仍未签收；真实平台 I/O 为 0。测试使用合成身份、临时密钥、真实
localhost WS/Core HTTP/OneBot HTTP 和 Mock 模型，不能作为平台或付费实测。

## 接收与批准

所有命令共用同一 Bridge 配置、原 Spool 的 OS 排他锁和原 Window Journal。
常驻 daemon 检测到窗口登记便拒绝启动；捕获持锁时，第二个 daemon 或窗口也不能启动。
同一 Spool 对应部署中唯一接收器；另一个目录/主机上的副本不能由本机 OS 锁排除，部署
批准必须固定唯一实例和 data_dir。

| 命令 | 行为 |
| --- | --- |
| `window-open` | 固定原计划、身份、epoch、配置/版本、镜像与独立批准，检查初始待处理工作为零 |
| `window-capture-once` | 三项 HTTP 身份/健康/版本探测后，在 Bearer 认证 WS 接收边界捕获一个准确输入 |
| `window-approve-input --approval PRIVATE_JSON` | 仅从本 Spool 的 CAPTURED 记录读取正文，核对原 frame、签名收据、Journal 和人的逐条批准，再上传 Core |
| `window-send-once --approval PRIVATE_JSON` | 复用原 `deliver_tick`、Core lease/begin-send、Spool 结果与 Outbox；最多一次外部尝试 |
| `window-status` / `window-stop` | 查看持久状态 / 请求本地停止并取得独立 Core stop 确认 |

前四项命令继续要求 `--window PRIVATE_PLAN --inventory PRIVATE_INVENTORY`；approved_live
还要求准确匹配的 `--authorization PRIVATE_AUTHORIZATION`。所有批准/配置文件必须受限，
POSIX 要求 0600 文件及 0700 目录。文件、目录、账本或输入冲突均失败关闭。

`window-capture-once` 返回匿名计数和 `input_approval` 草稿：
`window_sha256,event_id,input_sha256,capture_sha256,evidence_sha256`。最后一项初始 null，
操作者检查受限 `data_dir/captures/` 中的原始证据后填写其逐条批准证据摘要，保存私密
批准文件，再调用 `window-approve-input`。捕获不会自动批准或调用模型。
来源正文不能通过 `--event` 或批准 JSON 上传；staged `window-ingest` 仅允许
local_synthetic，approved_live 在读取输入准入前拒绝。

只接受固定 owner→bot 的 friend/private/text：群聊、第三人、自回声、附件、引用段、
CQ 控制段和 ASCII `/` 开头的保留命令都会停止。文字段中的字面字符不会转换成 CQ 动作。
source message id 必须非零，原始消息/时间指纹用于同源去重与冲突检测。
消息发生时间和接收时间都必须在窗口内；来源不得晚于接收，且最多早 30 秒。
接收器将 wall clock 与 monotonic elapsed 比较，超过 2 秒漂移便停止。

固定 SnowLuma v2.0.3 源码会先发送 connect、enable、heartbeat 三帧，然后发送业务事件；
参见 [固定 adapter 源码](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/onebot/src/network/adapter.ts)。
接收器只跳过字段、bot 身份、新鲜时间正确的 connect/enable，以及 online/good 都为
严格 true 的健康 heartbeat。disable、未知/恶意元事件与错误身份仍停止。单次接收共用
最多 30 秒的总 deadline、16 帧及 `2 * max_frame_bytes` 累计字节上限；元事件不重置期限。
[OneBot 元事件规范](https://github.com/botuniverse/onebot-11/blob/master/event/meta.md) 提供标准字段；
enable 的 WS bootstrap 行为按上述固定 SnowLuma 版本单独覆盖。

## 独立接收器证明

普通 Core HTTP Bearer token 不足以生成 approved_live 输入证明。Bridge 私密配置增加
`capture_signing_key`：独立随机 Ed25519 32-byte seed 的 64 位小写 hex；Core 私密
`live_text_window.capture_public_key` 固定对应公钥。批准范围、配置摘要和运行绑定都包含
这项关联；无独立密钥或公钥不符则阻断 approved_live。local_synthetic 可以无密钥。
Bridge 独立镜像在自身带 hash 锁文件中固定 cryptography 47.0.0，不依赖安装 Core 包。

接收器仅在真实认证 socket 收到合格帧后，以域
`companion-core/onebot-capture/v1\0` 签署规范化完整 receipt。签名覆盖：kind、窗口、
event id、完整 source event id、protocol epoch、身份/配置摘要、frame SHA256、准确
输入摘要、发生/接收时间和私密证据 SHA256。JSON 使用排序键、紧凑分隔符、UTF-8；
时间使用带时区的 Python ISO 格式。Core 使用批准公钥独立验签，并再核对身份、source
与 event id 的对应、准确文本、发生时间和窗口。缺签、坏签、跨窗口/epoch/key 或任意
字段修改不会批准输入，也不会调用模型。

这是固定源码和独立凭据接收器对其观察的证明，**不是** QQ/OneBot 的平台签名；不声称
抵抗接收器私钥或批准环境本身被控制。Bridge 私钥不能放进 Core 配置、普通 HTTP token、
CLI 参数或公共日志，Core 仅需要公钥。Bridge 仍无模型 key、Core DB 或 ErasureLedger
管理权。只持普通 bridge token 的客户端不能用手写 CaptureReceipt 代替这项证明。

纯计算固定证据快照及其摘要，再计算 signed receipt，避免循环签名；快照包含原始 frame、
接收关联和私网连接资料。先 fsync 原 Window Journal 的 CAPTURED 记录绑定该 receipt
摘要，再写原始证据和原 Spool。原 Spool
保存同一 event id、source 指纹及正文。批准时逐一复核这些绑定，才变为原输入的
APPROVED 阶段。没有第二收件队列或发送状态机。

原始 frame 与 Spool 正文采用同样生命周期：收到 Core 202 acceptance 或精确 410
EVENT_ERASED 后删除本地 frame 文件，保留签名与哈希收据。Spool 启动会完成 ACK 后
中断的删除，不让捕获器形成另一份长期正文档案。未确认的捕获保留受限证据，必须纳入
人工恢复/停止处置；Bridge 不自行读取或重放 Core 遗忘账本。

## 额度、网络和停止

接收前先在原 Window Journal 和 test_window.state/fence 持久登记 CAPTURE_STARTED。
仅本次局部 capture_fence 可继续 CAPTURING，CLI/HTTP 不接受该参数；进程死亡后 status
报 CAPTURE_UNCONFIRMED，正常 STOP 仍可撤销，任何新接收均被阻断。合格 receipt/证据
快照与摘要纯计算后，先 fsync CAPTURED，再写原文/Spool，最后提交 DB head；中途死亡
导致 Journal/DB 不一致而阻断，不把未知接收当作零并继续。

CAPTURED 已消费一个新输入额度，即使随后批准/网络失败也不退款。仍最多五个新输入、
五次外部发送尝试、一并发、每输入至多一个回复；window_attempts 的 event id 唯一。
断线、异常、UNKNOWN、Core stop 未确认、旧 Spool/Journal 不一致、冲突重放均持久阻断。
不重连自动接收、不重试 send、不改变 epoch 恢复额度。

Demo 与 live_text 的所有 Core HTTP、三个 OneBot probes 和 send 都在 DNS pin 与全部
request hooks 之后校验原 authority、Host、SNI、实际固定 IP 和 Bearer。local_synthetic
对 HTTP 与 WS 实际目标都要求 loopback。一次 send 的 item/approval/client/fence 仅在
局部 SendOperation 中存在；不修改或清理调用方的 hooks 列表。

Core HTTP v1 Delivery item 直接携带真实 lease_until，Demo 和 live_text 均保存和检查该
租约。live_text 在最终发送前 await 原 Core send-check；响应包含已批准消息的 lease_until。
之后完成本地 Journal/DB 读取，再做纯 approval/config/fence/deadline/lease 检查；最后
采样时间之后无文件/DB I/O 或 await。外部 I/O 前仍先持久记账，拦截也保留已花尝试额度。
最终实际正文摘要和完整 item 摘要还须匹配原持久 reservation；正文、message id、
source event 和 lease 的可变副本不能相互比较后代替原逐条批准。

`window-stop` 首先 fsync 原 Spool 目录中的 `window.stop` 停止请求，即使接收器持锁也能
打断等待。持锁时 CLI 报 `STOP_REQUESTED / WINDOW_STOP_PENDING`，不声称 Core 已停。
接收器观察请求后持久停止并请求 Core stop；不可达时保持 CORE_STOP_UNCONFIRMED。
重启、恢复和新窗口不得删除停止请求来重获授权；一致恢复集合须包含该请求、原 Window
Journal、Spool、尚未确认的捕获证据、稳定 epoch，以及 Core 与既有预算/遗忘账本。

## 离线验证

`tests/test_onebot_capture.py` 覆盖真实 localhost WS bootstrap→准确输入→逐条批准→Core
HTTP→Mock 模型→原 Outbox→真实 localhost OneBot HTTP 的五轮成功链及重启去重，另覆盖
身份、元事件、来源时间、漂移、断线、Spool 满、双监听争用、CLI STOP、Core stop 未确认、
旧 Spool、证据篡改、缺签/坏签/跨范围签名、DNS pin 和最后 Core await 后的批准变更。
原 Window/live_text/network/delivery/erasure 测试继续承担原额度、不确定投递和恢复合同。
实际检查结果与固定候选 SHA 由执行记录关联；此文档不自证真实 QQ、费用或生产准入。
