# PR-A 验证记录

基线：`89c60f08ab4707f169d7fc268fc7d72a55faec78`（PR #12，未合并）。
本轮只实现 B站公开 L1/L2 Reader；未将 v0.6 全部能力或生产门禁标为完成。

## 代码和隔离验证

| 检查 | 结果 |
|---|---|
| Windows `./scripts/check.ps1`，启用真实浏览器测试 | 487 passed、1 skipped；403.97 秒。跳过项是系统符号链接权限；1 条既有 Starlette 弃用警告 |
| 后续字幕登录标记修正及真实 Core HTTP 路由回归 | `./scripts/check.ps1 -TestFiles tests/test_source_reader.py`：45 passed；8.34 秒 |
| 真实 Chromium | Windows Chromium 156 / Playwright 1.64.0；内联数据、外部连接阻断、无限脚本超时和取消清理三项通过，后续任务可继续执行 |
| 预算／兼容 | 多请求原子预留与结算、UNKNOWN 保留、旧 result／observation 指纹重放通过 |
| 迁移／隐私 | 有数据 0003/0004 → 0014、0013 → 0014、重复升级、加密恢复及最新遗忘日志重放通过 |
| 包隔离 | `uv build --out-dir .local/v06-reader/dist`；`verify_package.py --dist-dir ...` 通过；脱离 Git 重建不包含私密 canary，提取 wheel 独立迁移和 Reader CLI 成功 |
| GitHub CI | 提交后记录最终 HEAD 检查结果 |

全量检查在最后两个 Reader 回归添加前开始，故不能把其数量当成最后 HEAD 的全量测试计数。
最后 HEAD 由远端 Linux/Windows 全量检查与独立浏览器检查复核。
本机 Docker daemon 未运行；Linux 镜像构建及非 root 启动交给隔离 CI，未触碰现有生产容器。

## 真实公开访问

2026-10-19 UTC，以新的 PublicHTTP/Reader 实现低频读取 10 条公开视频链接，结果见
[source-reader-live.json](source-reader-live.json)。每条最多 4 次请求、512 KiB、20 秒；
无 Cookie、账号、付费模型或 QQ 发送。报告只保存标题、状态、字符数及来源摘要，不保存完整正文或字幕。

9 条读取成功，均为 **L1**：5 条字幕 `restricted`，4 条接口未提供字幕（`missing`）；
1 条上游 HTTP 错误且无结果内容。missing 描述公开接口本次未提供字幕，不断言视频不存在内嵌字幕。
API 真实响应在 `data.need_login_subtitle` 指示登录要求，已据此修正原先错误的 missing 分类，
并加入顶层／嵌套登录标记回归。初次诊断也曾遇到 HTTP 412；没有绕过验证码或登录墙。
真实公开 L2 字幕成功尚未验证，不能用模拟字幕替代实测结论。

## PR-A 验收矩阵

| 任务书要求 | 实际证据与边界 |
|---|---|
| 公开链接 10 例 | 真实 10 例：9 L1、1 可解释失败 |
| 字幕缺失 3 例 | 3 种缺失响应的自动回归通过；4 条真实样本的公开接口未提供字幕 |
| 限制访问 3 例 | HTTP 403/412/429 回归及字幕访问限制回归通过；5 条真实链接显示字幕受限 |
| 恶意跳转 | 不跟随外部重定向，DNS 混合私网／公网、元数据地址和非白名单目标回归通过 |
| 提示注入 | 不可信文本原样归入 Observation，既有信任标记／权限边界保持；没有自动外部动作 |
| 无限页面、超额响应 | 真实 Chromium 无限脚本被终止，下一会话正常；压缩／字节／请求超限回归通过 |
| 超时后清理 | 真实超时及取消调用关闭浏览器，active context 归零；验证后未观察到本工作区残留 headless-shell 进程 |
| 跨进程数据合同 | 实际 FastAPI ASGI HTTP 路由往返、适配器鉴权、正文及 L2 证据落库、重复 tick 不重读通过；外部响应为确定性测试输入 |

## 风险、未完成门禁与下一行动

公开 API 可变且不承诺可用性；测试时字幕要求登录或未提供，因此当前实际可用能力是公开 L1。
Browser 仅离线渲染受控 HTML，不是完整 SPA 浏览器。Linux sandbox、目标主机资源峰值、
真实 L2、真实 QQ 全链路及长期日记审核尚待独立验收。
L3 关键帧／语音转写、其余 v0.6 工作包未在本 PR 启用。

PR 依赖 PR #12 的修复候选。后续先审阅 PR #12/#11 和本 PR；获得明确授权后才执行合并
或生产启用。无需账号也能继续开展后续公开来源研发；要验证登录态字幕，需要主人明确配置
独立只读账号能力及安全存储合同。

运行与回滚说明见 [source-reader.md](source-reader.md)。
