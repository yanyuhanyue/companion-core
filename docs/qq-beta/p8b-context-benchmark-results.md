# P8-B B4 冻结基准读数

证据：`OFFLINE_SYNTHETIC`，`usage_kind=synthetic_estimate`，结论
`DESIGN_ONLY_NO_PRODUCTION_BENEFIT_PROVEN`。基线
`a33d3cb77f348828b17e4bb48c621c53cfcdc017`；数据集 canonical SHA256
`12a73ddac13a1188113c2c8a4840a6a19959a75425961e6d7a3eefd62f374d4c`。
原始文件 [p8b-context-benchmark.json](p8b-context-benchmark.json) 含每样本记录与源文件 SHA256。
方法、限制、后续设计见 [RFC](p8b-token-memory-rfc.md)。

## 场景分解

以下所有 input/output 都是合成代理单位，不是实际 token。
金额列是无币种的演示算术，费率 1/0.25/2，不是真实供应商价格或账单。
缓存命中代理统一为 0，真实 cache usage、token usage、费用均 `null`。

| 场景 | baseline calls | experiment calls | baseline input/output | experiment input/output | 演示费用单位 baseline/experiment | UNKNOWN baseline/experiment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| chat | 12 | 12 | 4927 / 228 | 4775 / 228 | 5383 / 5231 | 1/12 / 1/12 |
| memory_review | 9 | 2 | 2056 / 187 | 490 / 44 | 2430 / 578 | 0/9 / 0/2 |
| memory_extract | 1 | 1 | 257 / 19 | 257 / 19 | 295 / 295 | 0/1 / 0/1 |
| diary | 1 | 1 | 575 / 32 | 575 / 32 | 639 / 639 | 0/1 / 0/1 |
| reflection | 1 | 1 | 224 / 20 | 224 / 20 | 264 / 264 | 0/1 / 0/1 |
| activity | 1 | 1 | 263 / 19 | 263 / 19 | 301 / 301 | 0/1 / 0/1 |

memory_review 读数只覆盖 recall relevance；提取后的 relation review 没有单独执行。
后台四项仅相同 payload 模拟，没有运行后台 worker，没有证明其实际批次成本。
UNKNOWN 是主动注入，不是测得服务商故障率；两边各保留一条非零预留，没有自动退款。

## 延迟、CPU 与内存

供应商模型 latency 及手机客户端 E2E：所有场景 n=0，P50/P95=`null`。
下表只计本地 stub，不能作为模型速度或手机延迟：

| 场景 | baseline n, P50/P95 ms | experiment n, P50/P95 ms |
| --- | --- | --- |
| chat | 12, 0.0662 / 0.1222 | 12, 0.07595 / 0.1185 |
| memory_review | 9, 0.1681 / 0.1986 | 2, 0.15515 / 0.1924 |
| memory_extract | 1, 0.0372 / 0.0372 | 1, 0.0203 / 0.0203 |
| diary | 1, 0.0198 / 0.0198 | 1, 0.0107 / 0.0107 |
| reflection | 1, 0.0112 / 0.0112 | 1, 0.0058 / 0.0058 |
| activity | 1, 0.0093 / 0.0093 | 1, 0.0049 / 0.0049 |

context/recall 本地 wall（含 SQLite review reserve/settle，n=12）：baseline
P50/P95=7.691/9.8841 ms，experiment=1.0226/9.2051 ms。
进程 CPU P50/P95 分别 0/15.625 ms、0/0 ms；Windows 时钟粒度不足，不能推断零 CPU。
Python tracemalloc peak 分别 5,065,213/3,617,061 bytes，RSS 均 `null`。
先 baseline 后 experiment、有初始化/缓存顺序效应且 n 很小，不声称显著加速或内存下降。

## 保持、负例与不实施结论

两边各 12 样本、11 组，有要求的参考共 8 项：baseline 保留 7/8，experiment 保留 7/8。
但失败位置不同：

- baseline `pressure`：近期长对话使正确的 `tea` 参考先被 soft 裁剪。
- experiment `indirect`：“饮料还是老样子吗？”被简陋规则当 L0，丢掉必要 `tea`。

两边禁止参考泄漏、`DELIVERY_UNCERTAIN` assistant 回显、当前输入丢失和 system 规则丢失均 0。
这些是 fixture 边界/上下文保持检查，不是生成语义、真实权限系统或旧备份遗忘通过。
review stub 使用 fixture oracle，实验绕过若干 oracle review 不等于真实检索仍然正确。

因此减少的合成调用数不能升级为真实节省比例；间接回忆退化已经触发逐组不退化的退出条件。
不修改生产 context、不建新图表、不启用 PAD，不提出在当前结果上部署。

## 实际验证记录

使用已有锁定项目 Python 3.12.10 环境；未安装模型或 tokenizer。执行入口：

```text
python scripts/benchmark_p8b_context.py --output docs/qq-beta/p8b-context-benchmark.json --work-dir .local/p8b-rfc/benchmark-run-final
python -m pytest tests/test_p8b_context_benchmark.py tests/test_m0.py tests/test_affect.py -q --basetemp=.local/test-temp/p8b-rfc-final -o cache_dir=.local/pytest-cache/p8b-rfc
python -m ruff check scripts/benchmark_p8b_context.py tests/test_p8b_context_benchmark.py --cache-dir .local/ruff-cache/p8b-rfc
```

脚本退出 0、provider HTTP=0、QQ attempts=0；最终定向套件 **37 passed / 1 existing warning**，
89.56 秒，退出 0；其中新增 B4 测试 11 条，其余为 M0 与 Affect 相关回归。
警告是既有 Starlette TestClient/httpx 弃用提示；Ruff 与 `git diff --check` 通过。
网络发送/encoder warmup 在新基准测试里被替换为抛错，以检测意外模型/网络调用。
首次测试因新工作树缺少 `.local/test-temp` 父目录出现 4 passed/6 setup errors，
创建指定父目录后旧数据集为 10 passed；新增间接回忆原始负例后执行最终套件，未隐藏失败重跑历史。
全量组合验证与同 SHA CI 由独立整合阶段记录，不引用 P8-A 的 771 条作为本轮成绩。
