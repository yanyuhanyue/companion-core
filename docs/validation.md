# 验证记录

日期：2026-10-19。运行环境：Windows、Python 3.12.10、依赖版本见 `uv.lock`。

## 已执行

- Ruff 静态检查与格式整理。
- 48 项自动化检查通过：M0 15 个合同编号、M1 15 个合同编号及参数化/故障补充用例。
- SQLite Alembic `base → M0 → M1 → M0 → M1 → base → M1` 空库迁移往返；同版本重复迁移不丢数据。
- 真实回环 HTTP API 与独立 Worker 进程，使用演示模型，完成入站、任务执行、领取、发送前状态更新和回执。
- 100 条合成的真实接口格式消息回放：30 条目标事实入库，Recall@6 为 30/30，公开动作 0。
- 加密热备份恢复；忘记后恢复旧备份，使用最新独立日志回放，内容不会重新进入检索。

详细回放数值见 [replay-report.json](replay-report.json)。输入 Token 数是保守估计；样例模型使用确定性规则，因此不代表 DeepSeek 的实际提取准确率或幻觉率。

## 复现

```powershell
$env:UV_CACHE_DIR = "$PWD/.local/uv-cache"
uv sync --frozen
./scripts/check.ps1
.venv/Scripts/python.exe scripts/replay.py
.venv/Scripts/python.exe scripts/smoke.py
.venv/Scripts/python.exe scripts/package_bridge.py
```

检查脚本把测试临时目录限定在工作区并在结束时清理。回放和冒烟脚本自行清理数据。插件 ZIP 是保留的交付物。

## 尚未完成的生产门禁

- 真实 AstrBot/NapCat/QQ 账号的发送、优先级冲突与重连测试。插件方法已对照 4.29.1 源码，自动化使用模拟宿主。
- 真实模型输出质量、真实缓存用量和供应商账单核对。
- VPS Docker 运行、资源压力、异地备份和独立遗忘日志复制。
- Linux 容器运行由 GitHub CI 单独检查；本机 Docker daemon 当前未运行。

这些项目完成前，M0/M1 为**代码与本地合同验收通过，生产验收待完成**。DSH、Android 和公开发布仍关闭。
