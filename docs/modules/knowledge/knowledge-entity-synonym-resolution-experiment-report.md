# KnowledgeEntity 异名同义候选实验报告

## 1. 结论

本次实验支持引入双视角 embedding，但只用于精确名称和 alias 匹配失败后的候选召回，不替代最终实体同一性判定。目录从 160 个实体扩展到 1160 个实体后，人工挑战集的 Embedding Recall@3 仍为 100%，完整链路总准确率为 100%，5 条硬负例没有误合并。

推荐链路：

1. 规范化名称或已知 alias 唯一命中时，直接复用规范实体。
2. 精确匹配失败时，按 Subject 和类型做硬过滤。
3. 分别计算“完整提及/实体语义”和“去除 Subject 后的局部名称”相似度，取两者最大值召回 Top 3 候选。
4. 把候选的规范名称、aliases、Subject、localName 和当前文档上下文交给 LLM，输出 `SAME`、`DIFFERENT` 或 `UNCERTAIN`。
5. 只有 `SAME` 才允许归一；`DIFFERENT` 和 `UNCERTAIN` 均不自动合并。

不建议在未知同义词回退路径中采用词法与 embedding 对等 RRF 融合。生产链路应先走确定性的名称/alias 精确匹配，再走双视角 embedding；报告中的“融合”仅用于近似评估这条完整链路。

## 2. 实验范围

### 2.1 数据

- 输入：`Document/` 下 16 篇 Markdown。
- 文档总规模：约 29 万字符。
- 真实实体发现：复用现有 `KnowledgeEntityDiscovery` 提示词、规范化和重试逻辑。
- 实体发现模型：当前环境的 `deepseek-v4-flash`，temperature 为 0。
- 原始发现结果：174 条实体输出，按规范名称合并后为 157 个目录实体。
- 人工 fixture：3 个，用于补充可控的跨语言目标实体。
- 基础候选目录：160 个实体。
- 压力目录：追加 1000 个合成干扰实体后共 1160 个实体。
- 压力词表：23 个 Subject、48 个中英文局部名称和 40 个全局相关概念；生成器会排除评测 mention、目标名称以及会破坏负例语义的实体名称。

原始 LLM 抽取结果与人工 fixture 分开保存。人工数据不会覆盖原始响应，并通过 `origins` 标记来源。

### 2.2 评测集

共 29 条：

- 人工挑战集 17 条：12 条同义正例、5 条易误合并负例。
- 从真实发现结果分层抽样的 aliases 12 条，均为正例。

挑战集覆盖：

- 中英文组件名称：`ByKC-基础问答引擎` 与 `ByKC-BaseQAEngine`。
- 中英文概念名称：`Palantir-对象时间线` 与 `object timeline`。
- 格式差异：`ByKC-AgentDSL` 与 `ByKC-Agent DSL`。
- 同 Subject 相近但不同：`ByKC-BaseEmbeddingEngine` 与已有 QA、Embedding 实体。
- 反义或相关但不同：`Obsidian-云端优先` 与 `Obsidian-Local-first`。
- 同义重复实体：`ByKC-操作类型注册表` 与已有注册表实体。

### 2.3 对比策略与迭代口径

| 策略 | 候选方式 | 最终判定 |
|---|---|---|
| 词法 + Subject | token、字符 n-gram、编辑距离、包含关系、Subject | 同一个真实 LLM |
| Embedding | `text-embedding-v4`，1024 维；初始基线为完整语义单视角，最终方案为完整语义与局部名称双向量取最大余弦相似度 | 同一个真实 LLM |
| 融合 | 词法与 embedding 候选做 RRF | 同一个真实 LLM |

三种策略共用同一份实体抽取缓存、同一批评测样本和同一组 LLM 候选判定结果，避免因重复抽取或模型随机性破坏对比公平性。

## 3. 实验结果

### 3.1 初始 160 实体基线指标

本节记录引入压力词表前的单视角基线，用于保留实验演进过程；最终双视角方案及同口径规模对比见 3.5 节。

| 策略 | Recall@1 | Recall@3 | MRR | 正例解析准确率 | 误合并率 | 总准确率 |
|---|---:|---:|---:|---:|---:|---:|
| 词法 + Subject | 70.83% | 70.83% | 70.83% | 70.83% | 0.00% | 75.86% |
| Embedding | 91.67% | 100.00% | 95.83% | 100.00% | 0.00% | 100.00% |
| 词法 + Embedding 融合 | 70.83% | 100.00% | 84.72% | 100.00% | 0.00% | 100.00% |

Embedding 相对词法的 Top-3 召回提升 29.17 个百分点。

### 3.2 初始基线的人工挑战集

| 策略 | 样本数 | Recall@3 | 正例解析准确率 | 误合并率 | 总准确率 |
|---|---:|---:|---:|---:|---:|
| 词法 + Subject | 17 | 41.67% | 41.67% | 0.00% | 58.82% |
| Embedding | 17 | 100.00% | 100.00% | 0.00% | 100.00% |
| 融合 | 17 | 100.00% | 100.00% | 0.00% | 100.00% |

挑战集更能反映未知跨语言同义词。词法方案即使限制在相同 Subject 内，仍不能对候选进行有效的跨语言排序。

### 3.3 已知 aliases 的确定性路径

12 条已知 alias 通过规范化精确查询均可解析到 canonical 实体。这验证了已知 surface 应优先采用确定性匹配；纯 embedding 在最终压力实验中并未召回其中 1 条，也不影响正式链路，因为已知 alias 不应进入 embedding 或 LLM。

### 3.4 关键样本

`ByKC-基础问答引擎` 的结果：

| 策略 | 结果 |
|---|---|
| 词法 + Subject | `ByKC-BaseQAEngine` 未进入 Top 3 |
| Embedding | `ByKC-BaseQAEngine` 排名第 1 |
| 融合 | `ByKC-BaseQAEngine` 进入 Top 3 |
| LLM 判定 | `SAME` |

因此，该示例可以稳定归一到 `ByKC-BaseQAEngine`，并将 `ByKC-基础问答引擎` 记录为 alias。

其他只有 embedding 召回成功的挑战样本包括：

- `ByKC-流式事件` → `ByKC-StreamEvent`
- `ByKC-服务工具调度器` → `ByKC-ServiceToolDispatcher`
- `ByKC-知识实体发现器` → `ByKC-KnowledgeEntityDiscovery`
- `ByKC-嵌入查询服务` → `ByKC-EmbeddingQueryService`
- `Obsidian-本地优先` → `Obsidian-Local-first`
- `Palantir-对象时间线` → `object timeline`

### 3.5 1160 实体压力测试

使用相同的 29 条评测样本，将候选目录从 160 扩展到 1160。两组均使用双视角 embedding，保证对比口径一致。

| 指标 | 160 实体 | 1160 实体 | 变化 |
|---|---:|---:|---:|
| Embedding Recall@1 | 91.67% | 79.17% | -12.50 个百分点 |
| Embedding Recall@3 | 95.83% | 95.83% | 0 |
| Embedding MRR | 93.75% | 86.81% | -6.94 个百分点 |
| 人工挑战集 Embedding Recall@1 | 91.67% | 66.67% | -25.00 个百分点 |
| 人工挑战集 Embedding Recall@3 | 100.00% | 100.00% | 0 |
| 完整链路 Recall@3 | 100.00% | 100.00% | 0 |

加入大量同 Subject、同类型近邻后，Top-1 和 MRR 明显下降，但 Top-3 召回保持稳定。这支持“召回 Top 3，再裁决”的设计，不支持直接拿向量 Top-1 自动合并。

1160 实体下的真实 DeepSeek 裁决结果：

| 策略 | 正例解析准确率 | 误合并率 | 总准确率 |
|---|---:|---:|---:|
| 词法 + Subject | 70.83% | 0.00% | 75.86% |
| 纯 Embedding Top-3 | 95.83% | 0.00% | 96.55% |
| 精确匹配 + 双视角 Embedding 候选 | 100.00% | 0.00% | 100.00% |

纯 Embedding 的唯一漏项是已知 alias `组织操作系统` → `AI Argos`。该样本在正式链路的精确 alias 阶段排名第 1，不应进入 embedding 回退，因此不构成未知同义词召回失败。

## 4. 迭代记录

### 4.1 候选判定必须包含 aliases

第一版只把候选名称交给 LLM。`ByKC-操作类型注册表` 的候选实体已经拥有对应 alias，但 LLM 看不到，因而判为不同。

修正后，判定输入包含：

- 规范名称
- aliases
- Subject
- localName
- 当前提及上下文

修正后该样本判定为 `SAME`。

### 4.2 清理歧义金标

`LLM Wiki-摄入` 是泛称，而目录实体是更具体的“两步链式思维摄入”。原金标把不同粒度对象强行标为同义，LLM 判 `DIFFERENT` 是合理结果。

评测提及改为无歧义的“两阶段思维链摄入”，避免把金标问题错误归因于算法。

### 4.3 精确 alias 必须先于 Subject 根过滤

为避免 `ByKC-X` 总是召回根实体 `ByKC`，实验加入了 Subject 根过滤。但这曾错误过滤 `AI Argos 组织操作系统`，因为它是根实体 `AI Argos` 的已知 alias。

最终顺序固定为：

1. 精确名称或 alias 匹配。
2. 再执行 Subject 根过滤。
3. 再进行近似候选召回。

### 4.4 完整语义与局部名称必须分开向量化

第一轮 1000 干扰实体测试只使用完整提及向量。`Palantir-对象时间线` 的向量被 `Palantir` 和上下文信号主导，目标 `object timeline` 未进入 Top 3。

修正后分别向量化：

```text
完整视角：实体提及 + Subject + 上下文  ↔  规范名 + Subject + aliases
局部视角：去除 Subject 后的 mention    ↔  localName
最终分数：max(完整视角余弦相似度, 局部视角余弦相似度)
```

Subject 只用于冲突过滤，不再固定加分。修正后 `object timeline` 在 1160 个实体中排名第 1，分数为 0.9408。

### 4.5 压力词表不得污染负例

首轮压力词表生成了 `ByKC-BaseRetrievalEngine`，它与负例 `ByKC-基础检索引擎` 实际同义。DeepSeek 判为 `SAME` 是合理结果，不能计作误合并。

压力词表增加 `excludedEntityNames` 后移除该污染项。最终 5 条硬负例全部判为新实体，误合并率为 0%。

## 5. 性能结果

### 5.1 实体发现

16 篇文档使用真实 LLM：

- 单篇最小约 42.01 秒。
- 单篇中位数约 107.79 秒。
- 单篇最大约 179.89 秒。
- 单篇耗时合计约 1,786.05 秒。
- 并发数为 2 时，实际墙钟时间约 15 分钟。

这再次证明实体发现结果必须按文档缓存，并且 worker 必须限制并发。

### 5.2 Embedding

基础单视角实验中，160 个目录实体加 29 条提及，共 189 条文本：

- 冷缓存：19 次批请求，约 9.34 秒。
- 热缓存：0 次外部请求，约 86 毫秒。

因此不能在每个 worker 内重新向量化全库实体。向量应以模型身份和 surface 内容哈希缓存，只计算新增或变化的实体。

双视角压力实验中：

- 1160 个实体和 29 条评测提及产生 2378 次逻辑向量读取。
- 由于跨 Subject 的 localName 大量重复，内容哈希去重后实际只有 1461 个唯一向量。
- 独立冷启动在阿里云连接两次瞬时失败后，通过逐批原子缓存从断点恢复，无需重算已经完成的 1406 个向量。
- 热缓存复跑时 2378 次读取全部命中，embedding 外部请求为 0，embedding 阶段约 0.36 秒。
- 包含候选排序和 29 条已缓存裁决的完整热复跑约 3.1～3.2 秒。

冷启动期间连接不稳定，因此本轮不把跨三次断点运行的墙钟时间作为服务延迟基准；它足以证明全量建索引是分钟级操作，不能放在每个 worker 的任务路径中。评测客户端已增加针对网络错误、408、429 和 5xx 的 4 次指数退避，并保持逐批落盘。

### 5.3 LLM 同义判定

29 条评测、并发数 4：

- 冷缓存：29 次请求，约 41.40 秒。
- 热缓存：29 条全部命中，约 0.58 秒。

生产链路应按“提及 + 候选集合 + prompt 版本 + 模型身份”缓存判定结果。精确匹配成功时完全跳过此步骤。

## 6. 对生产方案的调整

生产方案采用独立实体资产表，实体生命周期与文件解耦；同时参考 `014_embedding_table.sql.tpl`，为每个 embedding 模型动态创建实体向量表：

```text
knowledge_entity
├── knowledge_base_id
├── fs_entry_id nullable unique
├── name_role = canonical / alias
├── canonical_entity_id
├── entity_name / normalized_entity_name
├── local_name / subject_entity_id / entity_type
└── timestamps

knowledge_entity_embedding_<model>
├── entity_id
├── representation = full / local_name
├── source_content_hash
└── embedding vector(N)
```

实体资产归属知识库，并通过可空唯一的 `fs_entry_id` 与 KnowledgeEntity Markdown 一一锚定。由于现有知识库和文件采用逻辑删除，文件删除事务必须显式把 `fs_entry_id` 置空，实体和向量保留；删除 canonical 实体时由应用层先走现有文件删除服务，再级联删除 aliases 和 embeddings；知识库删除事务显式硬删该库实体行，MVP 暂不跨知识库保留资产。

1160 实体压力测试显示，候选扩大后人工挑战集 Embedding Recall@1 从 91.67% 降到 66.67%。余弦分数本身不会因词库变大而机械衰减，但近邻竞争会降低正确实体排名。因此生产中的精确名称、alias 和向量查询全部限定当前 `knowledge_base_id`，不让其他知识库实体参与候选竞争。

推荐处理顺序：

```text
抽取实体
  → 当前知识库精确名称/alias 唯一匹配
  → 未命中时按当前知识库、Subject/类型硬过滤
  → 当前知识库 embedding Top 3
  → LLM SAME/DIFFERENT/UNCERTAIN
  → SAME 复用本库规范名称并补 alias
  → 本库无兼容结果时，在当前知识库独立创建实体
  → 其余 DIFFERENT/UNCERTAIN 新建实体，不自动合并
```

性能约束：

- 精确名称/alias 从实体资产表查询，向量 Top-K 从当前模型的动态表查询。
- 文档实体发现按文档内容哈希增量缓存。
- 实体 embedding 按 `entity_id + representation + source_content_hash` 增量更新。
- worker 不加载或向量化全库实体，只执行有界的精确查询和 Top-K 查询。
- 当前知识库的 canonical 实体可通过 `fs_entry_id` 唯一锚定本库文件；文件删除只清空锚点，不影响该库实体资产。
- 保持有界 worker 并发，默认 4 仍需结合模型限流压测。

## 7. 边界与后续验证

本实验足以证明双视角 embedding 在 1160 候选规模下对当前未知跨语言同义词有显著价值，但不能把完整链路 100% 当作生产准确率保证：

- 人工挑战集只有 17 条，硬负例只有 5 条。
- 3 个目标实体来自人工 fixture。
- 同一大模型参与实体发现和最终判定，可能产生相关性偏差。
- aliases 集来自同一个实体发现结果，难度低于真实线上未知提及。
- 压力词表扩大了近邻目录范围，但独立金标仍只有 29 条，未等同于增加人工标注样本量。
- 本实验未覆盖同名异义、版本实体、组织重名和跨 Subject 错配。

正式上线前建议扩展到至少 100 条人工复核样本，并单独统计：

- 跨语言同义词 Recall@3。
- 缩写和全称 Recall@3。
- Subject 冲突拦截率。
- 同类型近邻误合并率。
- `UNCERTAIN` 比例。
- 冷启动与增量更新延迟。

## 8. 复现方式

运行完整实验：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
.venv/bin/python scripts/knowledge_base/evaluate_entity_synonym_resolution.py
```

关键文件：

- Demo：`scripts/knowledge_base/evaluate_entity_synonym_resolution.py`
- 人工挑战集：`scripts/knowledge_base/entity_synonym_resolution_benchmark.json`
- 人工实体：`scripts/knowledge_base/entity_synonym_resolution_fixtures.json`
- 压力词表：`scripts/knowledge_base/entity_synonym_resolution_stress_vocabulary.json`
- 单测：`tests/knowledge_base/unit/test_entity_synonym_resolution_demo.py`
- 本地详细结果：`eval/reports/entity-synonym-resolution/report.json`
- 本地可读报告：`eval/reports/entity-synonym-resolution/report.md`

运行 1000 干扰实体压力测试：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
.venv/bin/python scripts/knowledge_base/evaluate_entity_synonym_resolution.py \
  --output-dir eval/reports/entity-synonym-resolution-stress-1000 \
  --synthetic-catalog-size 1000
```
