# 主人控制台第二版

第二版控制台替换了 `companion/console_static/` 的界面，接口仍是原有的 `/v1/console`，
会话、Origin、CSRF、幂等键和“结果待核对”语义不变。[prototype.html](prototype.html)
是确认过的设计稿，用演示数据展示全部页面，包括还没有后端支持的部分；直接用浏览器打开即可。

## 页面与接口

| 页面 | 读取 | 写入 |
|---|---|---|
| 今天 | `overview`、`activity`、`diagnostics`、`configuration` | `mode` |
| 活动时间线 | `activity`（分页） | 无 |
| 记忆 | `memories`（`q`、`review`） | `memories/{id}/decision` |
| 日记与成长 | `life`、`diary/{day}` | `diary/{day}/correction`、`diary/{day}/forget`、`mode` |
| 阅读与文件、表情素材、语音与节点、QQ 资料行为 | `content`、`memes`、`voice`、`account-actions` | 无 |
| 能力与开关 | `overview` 的能力快照、`configuration` | `configuration`、`mode` |
| 运行诊断 | `diagnostics` | `deliveries/{id}/reconcile` |
| 配置 | `configuration` | `configuration`、`cloud-budget` |

“今天”的时间带由当天的审计记录按动作前缀分行；超过一次读取上限时提示去时间线查看。时间线按日期筛选时会继续向前翻页，最多读取 20 页。
记忆页的关系图只用现有字段：主体、记忆类型和来源消息编号，引用同一条消息的记忆互相连接。
属于某项能力的开关和参数只在能力页出现，配置页只放模型、费用总额、密钥和运行等系统级设置；两页共用一份配置草稿，保存为一个新的配置版本。云端预算单独保存并立即生效。
对象型配置按子项显示为开关、数字和名单，也可以切换为 JSON 编辑。模型账本以美元记账，界面可按云端预算里的汇率换算成人民币显示和填写，保存时换算回微美元。
能力与配置字段的对应关系在 `app.js` 的 `CAP_FIELDS` 中维护，只显示服务端实际返回的字段，
部署保管的项目（例如 `observation_adapters`、`executive`）仍不进入浏览器。

页面只用 DOM 构建内容，样式通过 CSSOM 设置，与现有 `script-src 'self'`、`style-src 'self'`
策略兼容；`tests/test_console_static.py` 检查这些约束。

## 设计稿中尚未实现的部分

下列功能需要新的后端机制，按仓库规则先单独讨论范围和取舍，再实现：

| 设计稿功能 | 缺少的机制 |
|---|---|
| 交给她决定（她提议或在范围内自行调整能力参数） | 运行时覆盖的存储、授权范围、审计与撤销，以及备份恢复和遗忘时的处理 |
| 记忆按人物、地点等实体连成关系图 | 记忆提取时增加实体与关系，并保证每条关系都有原话证据、随遗忘一起清除。现在的关系图只按主体、类型和共同来源消息连接 |
| 网站名单在界面填写、黑名单模式 | 允许主机名单需要从 `observation_adapters` 中拆出；读取程序目前只支持精确白名单 |
| 语音回放与她的语音回复 | 语音任务结束即清除音频，回放需要独立的保留期与读取接口 |
| 表情素材编辑、上传、审核与隔离 | 控制台目前只读取理解任务，没有素材写入接口 |
| QQ 资料计划的允许与拒绝、头像预览 | 计划决定接口与审核过的素材读取；外部图片受 CSP 限制不直接加载 |
| 目标与反思的接受和拒绝 | 现有决定接口在内部控制面，尚未进入主人会话 |
| 记忆原话展示 | 记忆列表只返回来源编号，需要有界的证据摘录接口 |

关系图与召回调试参考了 AstrBot 插件
[LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)
（[界面说明](https://lxfight-s-astrbot-plugins.github.io/astrbot_plugin_livingmemory/webui)：
图谱与记忆双向定位、选中节点列出关联记忆）、
[Memora Connect](https://github.com/qa296/astrbot_plugin_memora_connect)
和 [AI Memory KG](https://github.com/Catfish872/astrbot_plugin_ai_memory_KG)。
