# 验证记录

日期：2026-10-19。运行环境：Windows、Python 3.12.10、依赖版本见 `uv.lock`。

## 已执行

- Ruff 静态检查与格式整理。
- 68 项自动化检查：M0/M1 合同用例与真实命令流程回归，覆盖跨会话同义问法、新事实干扰、冲突双方保留、候选确认、核对结果校验、预算降级和在途遗忘。
- SQLite Alembic `base → M0 → M1 → memory_index` 及降级/升级往返；已有 M1 事实在索引迁移和回退后保持不变，向量可重建。
- 真实回环 HTTP API 与独立 Worker 进程，使用演示模型，完成入站、任务执行、领取、发送前状态更新和回执。
- 100 条合成的真实接口格式消息回放：30 条目标事实入库，Recall@6 为 30/30，公开动作 0。
- 加密热备份恢复；忘记后恢复旧备份，使用最新独立日志回放，内容不会重新进入检索。
- GitHub CI 在 Windows、Ubuntu 执行同组测试、回放、独立进程冒烟和打包；Linux 容器初始化启用 M1，检查原生 ONNX 依赖与模型加载。[对应提交的运行结果](https://github.com/yanyuhanyue/companion-core/actions/workflows/ci.yml)。
- Python wheel 在独立安装目录导入，所包含的 Alembic 迁移可执行。

详细回放数值见 [replay-report.json](replay-report.json)。输入 Token 数是保守估计；此回放使用确定性规则模型。

## 真实 DeepSeek 场景验证

使用真实 `deepseek-flash` 与固定版本的本地中文嵌入模型，输入均为合成数据，数据库位于工作区临时目录。最终一轮 10 项检查全部通过，27 次模型请求，按配置费率估算 2208 微美元（0.002208 USD）。记录包含实际回复与 usage，见 [live-memory-report.json](live-memory-report.json)。

- 自动抽取候选后，通过“记住这个”确认同义内容，保留原 ID 和证据。
- 在后续加入多条无关事实后，用新会话询问饮料偏好、休息日运动和宠物名字，均召回正确事实。
- 互相矛盾的“记住”命令保留双方并提示冲突；兼容的两项偏好可并存；冲突问题会请求澄清。
- 不相关的候选经过回复前筛选；遗忘后不再回答已删除的颜色偏好，也不附带无关的饮料记忆。

这些结果验证指定的真实模型使用场景，不等同于长期真实聊天的完整质量评估。测试将出站结果留在本地 Outbox；没有向 QQ 发送合成测试内容。

## 复现

```powershell
$env:UV_CACHE_DIR = "$PWD/.local/uv-cache"
uv sync --frozen
./scripts/check.ps1
.venv/Scripts/python.exe scripts/replay.py
.venv/Scripts/python.exe scripts/smoke.py
.venv/Scripts/python.exe scripts/package_bridge.py
.venv/Scripts/python.exe scripts/verify_memory_live.py
```

检查脚本把测试临时目录限定在工作区并在结束时清理。回放和冒烟脚本自行清理数据。插件 ZIP 是保留的交付物。

## 尚未完成的生产门禁

- 真实 AstrBot/NapCat/QQ 账号的发送、优先级冲突与重连测试。插件方法已对照 4.29.1 源码，自动化使用模拟宿主。
- 长期真实模型输出质量和供应商账单核对；本次场景报告已保存实际 Token 与缓存用量。
- VPS Docker 运行、资源压力、异地备份和独立遗忘日志复制。
- Linux 容器已由 GitHub CI 检查；目标 VPS 的容器运行与在线 QQ 链路仍待验证。

这些项目完成前，M0/M1 为**代码与本地合同验收通过，生产验收待完成**。DSH、Android 和公开发布仍关闭。
