# Companion Core

面向单一主人 QQ 私聊的虚拟朋友核心。Python 模块化单体，AstrBot 只负责消息传输；记忆和表达风格来自有证据的互动，初始人格为空。

当前交付 **v0.3 M0/M1 实现**。已完成本地自动化测试、100 条格式化消息回放，以及独立 API/Worker 进程的 HTTP 冒烟测试。真实 QQ、真实模型质量/账单和目标 VPS 仍须联调；本仓库不将模拟验收等同于生产验收。

## 能力与边界

- 主人身份、私聊范围、机器人回声校验；入站事务、幂等冲突检测、按会话 FIFO。
- Worker 限次重试；发送使用 `LEASED → SENDING → SENT/DELIVERY_UNCERTAIN`。不确定消息不自动重发，后续聊天可继续。
- DeepSeek Flash/OpenAI-compatible 模型适配；按场景预留预算，保存输入、输出、缓存 Token 与整数微美元估算。价格未知时停止自主活动。
- 主动私聊有冷却、每日次数、免打扰、暂停指令；不伪造现实生活或浏览记录。
- M1：批量记忆候选、逐字证据核验、冲突标记、确认/纠正/忘记、中文 FTS5、跨会话检索、空白起步的自我观察。
- 加密热备份；恢复前强制回放独立遗忘日志；隐私变更期间的旧模型结果被丢弃。

M0/M1 的 **DSH、Android、Windows MCP、公开空间和评论执行均关闭**。原设计将它们安排在 M2–M4，参见 [实施边界](docs/architecture.md)。多媒体输入目前只作文字能力提示，不下载或理解附件。

## 本地启动

需要 Python 3.11+（已验证 3.12）、支持 FTS5 trigram 的 SQLite，以及 `uv`。

```powershell
# 在仓库根目录执行。把缓存和临时数据限制在工作区。
$env:UV_CACHE_DIR = "$PWD/.local/uv-cache"
uv sync --frozen
Copy-Item config.example.toml config.toml
$env:COMPANION_OWNER_QQ_ID = '你的QQ号'
$env:COMPANION_BOT_QQ_ID = '机器人QQ号'
$env:COMPANION_BRIDGE_TOKEN = '你生成的至少24位随机令牌'
$env:COMPANION_MODEL_KEY = '模型API密钥'
uv run python -m companion init
uv run python -m companion serve
# 第二个终端：设置同样的环境变量后
uv run python -m companion worker
```

演示可设 `COMPANION_MODE=demo`，无需模型密钥；回复明确标记为演示。`.env` 仅由 Docker Compose 读取，本地命令需通过环境变量注入。不要提交真实配置、密钥或数据库。

默认 `memory_enabled=false`，先验收 M0 链路，再在 `config.toml` 开启 M1。主模型、费率、预算、时区和频率均可配置，未知字段和非法值会使启动失败。生产配置使用 TOML，原设计 YAML 作为字段语义参考；环境变量覆盖文件值。

## QQ 指令

| 指令 | 作用 |
|---|---|
| 普通文字 | 聊天，唯一主回复模型由 Core 调用 |
| `记住这个：我喜欢茉莉绿茶` | 明确确认一条用户事实（M1） |
| `记住这个` | 记住上一条用户消息（M1） |
| `你记得什么` | 查看近期可检索记忆及 ID（M1） |
| `确认这一条 mem_…` | 将候选确认为事实 |
| `纠正这一条 mem_… 新内容` | 创建替代记录，保留修订关系 |
| `忘记这一条 mem_…` | 清除关联记忆、证据及衍生内容 |
| `暂停主动分享` / `恢复主动分享` | 控制自主私聊 |
| `发送状态` | 列出待人工核对的发送 ID |
| `核对发送 msg_… 已发送` / `核对发送 msg_… 取消` | 核对后解除不确定状态，不重发 |

“忘记”会清空关联原始事件，同时失效所有可能复述该信息的近期回复、摘要和自我特征投影。其他独立事实仍保留。这是首版的保守策略；已发到 QQ 的消息无法由本地遗忘操作撤回。

## 部署、验证与目录

- [VPS / AstrBot 部署](docs/deployment.md)
- [模块边界与设计取舍](docs/architecture.md)
- [验收结果与限制](docs/validation.md)、[回放数据](docs/replay-report.json)
- [原始设计合同](docs/design-v0.3/implementation-m0-m1.md)、[接口合同](contracts/openapi.v1.yaml)

```powershell
./scripts/check.ps1                     # 检查并清理测试临时文件
.venv/Scripts/python.exe scripts/replay.py
.venv/Scripts/python.exe scripts/smoke.py
.venv/Scripts/python.exe scripts/package_bridge.py
```

Linux 检查：`uv run ruff check companion migrations astrbot_plugin_companion_bridge tests scripts` 与 `uv run pytest -q --basetemp=.local/test-temp`。CI 同时运行 Windows/Linux 测试和 Linux 容器构建。

`companion/` 是核心包；`astrbot_plugin_companion_bridge/` 可单独打包安装；`migrations/` 维护版本化数据库；`tests/` 按 M0/M1 编号验收；`scripts/` 提供可复现回放和运行检查。运行数据、缓存与交付包不会被加入 Git。
