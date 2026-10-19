# AstrBot / NapCat 运行版本

2026-10-19 核对官方发布并启动验证的版本：

| 组件 | 版本 | 镜像 |
|---|---|---|
| AstrBot | 4.29.1 | `soulter/astrbot:v4.29.1` |
| NapCat | 4.18.38 | 本目录构建的 `companion-napcat:4.18.38` |

发布链接、下载地址和校验值见 [runtime-versions.json](runtime-versions.json)。这是可复现的版本记录；后续升级应重新核对官方发布、备份并验收。

## 构建 NapCat 4.18.38

核对时官方 Docker 镜像最新为 4.18.37，尚未发布 4.18.38 镜像。这里使用按摘要固定的官方 4.18.37 镜像，仅替换经 SHA-256 核验的官方 4.18.38 Shell 压缩包。QQ 运行环境和入口脚本沿用官方镜像。

在仓库根目录、Linux Docker 主机执行：

```bash
mkdir -p .local/napcat-build
curl --fail --location --retry 3 \
  https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.38/NapCat.Shell.zip \
  --output .local/napcat-build/NapCat.Shell.zip
echo '735f2e633247df3ffd6e8ee8b84d7039c315b2655f5807e5f0029f93d2c30016  .local/napcat-build/NapCat.Shell.zip' | sha256sum -c -
docker pull mlikiowa/napcat-docker@sha256:f611790ecffa23c3c182017d5cf68179dc24612403608cd835ad13d79642f1bd
docker build --pull=false --network=none \
  -f deploy/napcat/Dockerfile -t companion-napcat:4.18.38 .local/napcat-build
```

Dockerfile 再次核验压缩包，并确认基础镜像没有预装入口脚本可能跳过更新的 `napcat.mjs`。保留已有 `/app/napcat/config` 与 `/app/.config/QQ` 挂载；不要把整个 `/app/napcat` 挂载为持久卷，否则已有程序文件会覆盖镜像内升级内容。

## 升级验收

升级前记录旧镜像 ID，添加回退标签，备份 Compose、应用配置、数据库及 QQ 登录状态。停止相关容器后备份 SQLite 状态；保留原挂载、网络、端口和插件配置，仅修改镜像引用并重建相关容器。

启动后核对实际程序版本、容器重启次数、管理页响应和插件加载结果。新 AstrBot 容器可能需要安装已有插件的依赖，管理页会在初始化完成后开放。QQ 处于未登录状态时，需要人工扫码；只有登录成功并建立 OneBot WebSocket 后，才能继续验收 QQ 消息链路。

这些运行检查不等于 Companion Core 的真实 QQ 联调。Core 部署与桥接验收见 [部署文档](../docs/deployment.md)。
