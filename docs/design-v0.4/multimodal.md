# 语音与视觉的后续接入合同

本轮定义可校验的 Observation Event 草案：`contracts/observation.draft.schema.json`。当前 Core 入站接口仍只接受 QQ v1 消息事件，未启用录音、摄像头、STT、TTS 或实时通话；草案通过结构验证不代表获得采集或存储权限。

## 边界

| 来源 | 后续接入方式 | 事实与授权 |
|---|---|---|
| QQ 语音消息 | AstrBot 传输，独立 STT 产生转写事件 | 原消息 ID、转写模型/时间、可靠性；原音频默认不留存 |
| QQ 语音回复 | 独立 TTS + 协议端媒体发送 | 先验证格式，再沿用发送租约和回执；成功送达后才记录行为 |
| 双向实时语音 | WebRTC 会话 + 独立实时 Agent | 主人开启的短期会话授权；中断、静音、结束立即生效 |
| 手机/Windows 摄像头 | 设备端显式开启并受控推流 | 授权设备、采集时间、会话 ID；不默认连续录制 |
| QQ 原生通话 | 隔离的实验适配器 | 固定版本验证，失败不影响文字 Core |

实时音视频可优先评估 [LiveKit Agents](https://docs.livekit.io/agents/)，它提供 WebRTC 与语音/视频 Agent 的基础设施。QQ 原生通话可参考 [maibot-qq-voice-call](https://github.com/ClaudiaGardner/maibot-qq-voice-call)，但本仓库尚未验证或接入该项目。

## Observation Event 草案

事件携带随机 ID、发生时间、观察类型、真实来源事件 ID、会话与设备身份、观察文字、置信度、出处摘要哈希，以及服务器可核验的 consent_id。不得以客户端提交的 `owner=true` 或一句“已经同意”代替授权。

执行端只把实际获取的观察或转写送入验证器。验证器须核对授权仍有效、设备/范围/时限匹配、输入已去敏、来源真实、隐私版本未变，然后才允许进入 Core Event Store。底层来源依赖必须完整传给 Memory 和 Diary，并加入遗忘日志及旧备份恢复测试。

默认 `raw_media_retention=discard`。如将来确需保留音视频，需增加独立的留存授权、到期删除及缓存/索引清理，不能仅靠事件字段改成“长期保存”。来自 Windows 摄像头的推流仍使用该设备的上行网络。

## 分阶段交付

先完成目标环境的 Core QQ/VPS 联调和 M1.5 私人日记验收，再分别接入自主浏览、授权工具、Android 与实时语音视觉。各阶段独立预算、能力开关和验收；协议端换成 SnowLuma 不会自动开启这些能力。浏览记录要区分发现链接、实际读取、实际观看和任务失败，不能把尝试写成成功。
