# P12 原生检索留出评测

证据等级：`OFFLINE_SYNTHETIC_NATIVE_RETRIEVAL`。数据为公开合成事实，不是真人聊天、自然日成长或真实业务 R5。没有修改生产检索算法，未使用图引擎；QQ、模型 API 与供应商 token/cost 都没有样本。

[cases.json](cases.json)包含 10 条公开事实和 12 条查询，脚本另加 40 条无关档案。3 条历史措辞只作为查询探针，9 条新查询作为留出。所有查询都直接调用当前 `Memory.search(query)`，没有把 gold、候选 ID、主题关键词路由或预选 fixture 传入检索器；结果返回后才计算必要记忆和误报。时间只是合成来源日期，不计入真实自然日。

[results.json](results.json)保存输入和实现 hash、每例返回项及实际指标：

| 组 | 查询 | 必要记忆 | 找回 | 漏忆 | 多余候选 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 历史措辞探针 | 3 | 3 | 3 | 0 | 1 |
| 独立留出 | 9 | 7 | 6 | 1 | 0 |

留出漏例为“休息日早上我一般去哪锻炼？”，未找到“周六上午去河边骑自行车”。历史措辞探针的项目代号问题额外返回一条无关档案。两个日期问题各包含当天、前/后一天和项目来源，正确日期结果排在第一；这只能证明候选排序，不能证明下游模型已经正确理解时间。

另实际运行原生纠正、source event 遗忘、记忆遗忘、遗忘前加密备份、最新 Ledger 回放、FTS 与 BGE 索引重建：新值可召回，旧值候选泄漏 0；当前库和隔离恢复库被删记忆命中/向量行均为 0，独立封面事实保留。

原 [P8-B 7/8 与 7/8](../../qq-beta/p8b-context-benchmark-results.md)是完整上下文保留读数，此次未重跑、未改写。这里的检索候选不能和原读数相加，也不能证明真实 recall-review、生成准确性、Token 节省或质量收益。保留漏例，继续原检索路径；真实 Token、金额、phone samples 和自然日均为 `null`。

运行已有锁定缓存，无下载：

```powershell
$env:HF_HUB_OFFLINE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH=(Get-Location).Path
.venv/Scripts/python.exe scripts/evaluate_p12_memory.py --embedding-cache data/embedding-cache --output docs/experiments/p12-memory/results.json
```

缓存缺失时脚本在开始数据库工作前失败，不下载权重。数据库、加密备份和恢复目录只在本工作树 `.local/test-temp` 内生成并自动删除，结果文件是唯一保留产物。Pinned encoder：`BAAI/bge-small-zh-v1.5@46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59`。
