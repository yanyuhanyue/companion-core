# P2 第一轮：发布链核对、小黑盒只读接入与环境门禁

任务书：`CC-NEXT-20261019-P2`；文件 SHA-256：
`6da3d99de0bde4d42fb38cfc1139867d29ae584ed582ad16a73aa01db357a696`。
核对日期：2026-10-20（上海）。本轮固定基线为
`c74cae69022fa09543a4e59209002c5282784140`，没有合并、生产接管、账号登录或付费调用。

## 发布依赖

| 对象 | 本轮查询的 HEAD | 状态 |
|---|---|---|
| main | `676f6143a40d08ffe44862a8897b4953dfa6c02e` | 仍为原基线 |
| PR #11 → main | `aee7b16fd4808bb78597f68e79836d146cc00e5b` | OPEN；6 项 CI 成功；旧代码门禁仍为 BLOCKED |
| PR #12 → release/v0.5 | `89c60f08ab4707f169d7fc268fc7d72a55faec78` | OPEN；6 项 CI 成功；修复候选 CODE_READY |
| PR #13 → fix/v05-release-gates | `c74cae69022fa09543a4e59209002c5282784140` | OPEN；8 项 CI 成功；公开 L1 有实测、L2 未取得真实成功 |

三个 PR 均无 review 或 inline review comment；Release 列表为空。
原 [v0.5 审计门禁](../release/v0.5-gate.json) 保留为历史证据，本轮不将旧 #11 标记为已修复。
重新核对了七项修复的差异和回归：日记删除／历史回放、暂停与 FIFO、管理指令路由、
主人深读次数、压缩拒绝、过期祖先证据及导出包排除私密文件。
没有从本轮核对中确认新的 P0/P1；生产门禁继续未通过。

本轮仍无合并授权，因此不改写、强推或移动这三条分支。
新开发分支暂以 PR #13 为 base，净增量仅是本工作包；不能将整个依赖链作为已合并 main。
将来先明确授权 #12 → release，再核验 #11 最终 SHA 并单独决定 main；其后再整理 #13
及本分支的 base，保留所有私人数据与可追溯提交。

## 本地与隔离检查

| 命令／范围 | 实际结果 |
|---|---|
| 开工 `./scripts/check.ps1 -TestFiles tests/test_release_privacy.py,tests/test_release_http.py,tests/test_release_migration.py,tests/test_source_reader.py` | 94 passed，38.91 秒 |
| Reader / Content / Exploration 联合回归 | 139 passed，52.25 秒；含新小黑盒 43 项测试 |
| 后补 `0014` 降级保护及 Reader 专项 | 48 passed，9.42 秒；多请求历史拒绝降级，单请求历史无损降级／再升级 |
| 最终本地 `./scripts/check.ps1`，真实 Chromium 开启 | 536 passed / 1 skipped，397.67 秒；跳过项为本机符号链接权限；1 条既有 Starlette 弃用警告 |
| Compose 两个 Reader profile | 使用显式占位环境和 `--no-env-resolution` 解析通过；未读取私密 env 文件 |
| 回放／进程联调 | 100 个确定性模拟事件、30 个预期事实，Recall@6=1.0；API 与六进程私聊／鉴权 smoke 通过 |
| 安装包 | `uv build --out-dir .local/v06-heybox/dist` 和 `verify_package.py --dist-dir ...` 通过；无 Git 导出重建排除私密 canary，提取 wheel 独立迁移和 Reader CLI 成功 |
| 目标 Linux 镜像构建 | 固定 Python digest、锁定依赖构建成功；无网络、非 root、只读运行尝试见下一节 |

最终远端 Windows/Linux、容器及独立 Windows 浏览器检查由
[本分支 GitHub Actions](https://github.com/yanyuhanyue/companion-core/actions?query=branch%3Afeat%2Fv06-heybox-reader)
提供。测试覆盖的真实／模拟边界如下：

- Core 实际 ASGI HTTP 鉴权、lease/result、图文引用／摘要落库、正文进入聊天、重复 tick、
  失败后普通聊天、删除分享根后恢复旧备份通过；上游文章与模型使用确定性测试输入。
- 公开外部页面是真实网络访问；不把自动回归文章记为真实帖文成功。
- 图片仅登记所属 CDN 引用与顺序；本轮无真实视觉 Provider 或 QQ 图片发送。
- Core 默认开关和生产配置未启用新能力；Bridge 与 Core 仍通过 HTTP 边界工作。

## 真实来源与 Linux sandbox

**小黑盒：** [10 条公开链接记录](heybox-public-live.json) 全部返回 3479 字节页面外壳，
正文成功数为 0，结果均为 `READ_BODY_UNAVAILABLE`，没有生成 FETCHED 观察。
公开分享入口另实测返回 302，位置为同一规范页面；数据接口直接 GET 返回缺少 hkey 参数。
这些结果只证明本轮静态 HTTP／直接请求路径的限制，不证明整个站点没有公开内容。
未逆向签名、提取 Cookie、登录或通过第三方镜像内容冒充原站证据。
用户已确认无现成只读 HTTP 服务，本轮未配置或部署这种服务。

**B站：** [新 3 条公开检查](bilibili-public-p2.json) 全部 L1 成功；1 条字幕 restricted，
2 条 missing。仍无真实 L2；视频标题含字幕字样不作为字幕 API 可用的证据。

**Linux：** [sandbox 实测记录](linux-browser-p2.json) 为 BLOCKED。
目标开发 VPS 的原有受限 Docker 配置下，Chromium 报 `No usable sandbox`，同配置
`unshare --user --map-root-user true` 返回 `Operation not permitted`。
宿主 unprivileged_userns_clone=1、max_user_namespaces=31581，同时启用 AppArmor、
Docker 内置 seccomp 和 cgroup namespace；这些信息不足以唯一归因某一安全模块。
没有关闭 sandbox、修改宿主安全设置或增加容器权限。

该启动尝试 3.073 秒，结束后 active context 为 0；限制为 1 CPU / 768 MiB / 96 PID，
container memory.peak 与 pids.peak 不可用，因此不报告已测得内存／PID 峰值。
前后 13 个原有容器的 ID、镜像、运行状态和启动时间一致。
Linux 浏览器继续默认关闭；API／静态页面路径不依赖它。
要启用，后续需独立审查兼容的 sandbox 运行配置或专用节点，而不是削弱现有隔离来获得通过。

## Android 核对与边界

已按用户指定向“比较两台主机的安卓模拟器支持”对话询证。
[只读摘要](android-readonly-p2.json) 对应 2026-10-20 07:28（上海）的实际查询；
没有重启、点击、滑动、重新抓取在线页面或改变现有设备。
Houdini 当前镜像 ID 为 `sha256:83ee52cd2f5619f54ac8d0587440e6618308eb331746916024f770d135bbe15a`；
基础镜像 digest 为 `sha256:a3e654caad74faed03cd5ead057a989e06f507778cda678e5d3b9772e0092f1c`。
这是校验材料，不是 Houdini 再分发许可或可下载修复包。

设备游客短测证据仍有效；10 次冷启动、独立副本恢复和 24/72 小时连续监测没有完成。
历史 XML 只有信息流摘要／图片控件，不能充当 10 篇真实全文或确认帖子 URL。
下一包的限制写在 [Android 只读备用合同](android-source-contract.md)，未部署该适配器。

## 验收结论和下一行动

| 门禁 | 本轮结论 |
|---|---|
| R0 / G0 | 固定 SHA 的核对与回归通过；合并和生产仍待主人决定 |
| R1 / G2 | 只读现状核对完成；长期稳定性／恢复未验收 |
| R2 / G3 | 适配器合同与自动测试通过；真实正文 0/10，保持 BLOCKED |
| R3 / G4 | B站 L1-only；目标 Linux sandbox BLOCKED |
| R4 / G5、G1、G6 | 无真实 QQ 接管、20 条实机 QQ 或 20 个自然日日记验收 |

最值得推进的是下一独立 Android 只读观察 PR：先实现包名／登录页／输入框／撤销守卫，
再由主人安排已打开游客页面的真实采样。当前既无可用 HTTP 来源，也没有完整可读 XML 正文，
不能把接入合同测试成功包装成内容闭环完成。
部署、风险和回滚见 [heybox-reader.md](heybox-reader.md)。
