# P11 发行许可核查

状态：`ADOPTED — AGPL-3.0-or-later`。主人于 2026-10-28 选定，root `LICENSE`、包元数据和 README 已同步。

## 自有代码许可

自有源码与自有文档采用 [GNU AGPL v3.0 或更高版本](https://www.gnu.org/licenses/agpl-3.0.html)。
root `LICENSE` 为 GNU 官方原文，SHA-256
`0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0`；
`pyproject.toml` 声明 `license = "AGPL-3.0-or-later"`。

选择理由：本项目是联网运行的私人伴侣服务，AGPL 第 13 条要求向通过网络使用修改版的用户提供源码；
它也与 GPL-3.0 兼容，允许在保留原版权声明的前提下复用 MaiBot（GPL-3.0）等项目的代码。
复用这类代码时，要在第三方 notices 中记录来源、修订与原许可。

第三方组件、模型、图片／表情、字体和外部服务仍各自适用原条款，下表的处理方式不变。

## 已有第三方与参考项目

| 范围 | 已核查事实 | 本项目处理 |
|---|---|---|
| Reader 请求签名与客户端约定 | `source_reader/THIRD_PARTY_NOTICES.txt` 保存固定上游修订、MIT 原文与署名 | 保留原 notices；不把这部分改称为自有非商业授权 |
| VCPToolBox | `2717df53e3c0691e9e493cba3fe34a6ef6f2974d` 的 [LICENSE](https://github.com/lioensky/VCPToolBox/blob/2717df53e3c0691e9e493cba3fe34a6ef6f2974d/LICENSE) 为 CC BY-NC-SA 4.0 | P11 以独立实现的接口/行为合同作参考，未引入 VCP 源码；今后复制需逐文件核查署名和相同方式共享要求 |
| SnowLuma | [固定 LICENSE](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/LICENSE) 区分源码可见非商业部分与专有 Native Addon | 通过 OneBot 互操作，不复制到 Core 源码或发行包；修改版公开发行和专有组件另有条款 |
| SnowLuma 官方二进制 | [固定 EULA 5.4](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/EULA.md) 对第三方安装包/镜像与自动化部署要求独立书面许可 | 保留现有私有运行批准的实际证据边界，不把 Core 的发行许可延伸到它 |
| BGE 模型 | [BAAI 模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5/blob/main/README.md) 与实际下载的 [Qdrant 固定修订模型卡](https://huggingface.co/Qdrant/bge-small-zh-v1.5/blob/46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59/README.md) 均声明 MIT；转换包该修订的 9 项文件中没有独立 LICENSE/NOTICE | 模型缓存不在源码发行包中；将来捆绑时仍需随产物补齐可核对的上游许可和署名，模型卡标签不能代替许可原文 |
| Android/Houdini、QQ 软件、第三方图片/表情/字体 | 本次没有取得可转授的发行许可 | 不将私有运行镜像、登录卷或媒体素材纳入对外包 |
| Console v0.3 | 未在已跟踪源码中找到可核对的原设计资产 | E1 提供本地只读 Inspector；完整 Console 需要实际源文件与设计授权 |

上述核查不新增任何第三方运行许可，也不修改已有实机授权记录。
Qdrant 固定修订 README 的原始字节 SHA-256 为
`d8d7a46d41a1a37fe4f0a5f637bf55c649310185329127d8a2204632e480be17`；
同修订 API 元数据确认 `sha=46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59` 与 `license=mit`。

## 依赖清单与打包范围

[依赖清单](p11-dependency-license-inventory.json) 绑定实际 `uv.lock` 哈希。
除项目自身外共有 63 个包/版本记录（包含条件版本）：61 个与 Windows 环境中实际安装版本相符，
读取其 METADATA、许可文件/NOTICE 哈希；另外两个条件版本只核对 PyPI 的精确版本元数据，未安装或执行。
元数据声明以 MIT/BSD/Apache/PSF 等为主；certifi 和 tqdm 包含 MPL，NumPy 与若干原生依赖包含多种第三方条款。
这些组件保留自身许可，项目的非商业条款不能替代它们。

这份清单是可核对的依赖盘点，`license_clearance=false`：尚不是所有平台二进制的分发审计。
对外软件包/镜像发布前，应从最终打包产物生成每个平台的完整组件清单，
包含操作系统包、Python 原生扩展、浏览器、模型与其嵌入的第三方 notices，并随包保留必要文本。
只看 Python 项目元数据不能覆盖 Linux 镜像、浏览器或专有组件。

本轮不需要改动或重新分发这些第三方产物来继续获准的私人 Beta。
