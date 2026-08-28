# KnowledgeEntity 增量 Enrich 与成文质量设计

## 1. 背景

discover 已从单纯的 Entity 抽取升级为 Entity 与 Topic 联合发现。Topic 会绑定到一个稳定 Entity，用来表达该 Entity 下可持续检索和维护的研究方向。但原有 enrich 流程仍只使用实体名、别名、旧 Markdown、关系证据和一次混合语义检索，没有消费 Topic，也没有区分首次生成与重复更新。

本设计基于 2026-08-27 “大厂文章知识库”的实际生成结果和当前代码链路，目标是改善实体文档的信息完整度、增量更新可靠性和引用可读性。

## 2. 实际结果审计

本次审计覆盖该知识库的全部 14 个 canonical Entity 文件，不是抽样。数据库中另有 1 条 `object_kind='ENTITY'` 的 alias 记录，不是第 15 个待 enrich 文件。结果如下：

| 检查项 | 实际结果 | 判断 |
| --- | --- | --- |
| 文档数量 | 14 | 全量覆盖 |
| 正文结构 | 14 个文件均只有“实体定义与边界”一节 | 严重不足 |
| 正文长度 | 14 个文件均只有一个实质段落 | 退化为实体简介 |
| Topic 利用 | 成文未读取任何持久化 Topic | discover/enrich 链路断开 |
| 引用分布 | 14 个实质段落全部在段尾追加链接 | 引用机械化 |
| OpenClaw | 已持久化 13 个 Topic，成文仍只有一段定义 | Topic 信息完全浪费 |
| 小红书写作规范 | 已持久化 11 个 Topic，成文仍只有一段定义 | 规则域未展开 |
| GBrain | 已持久化 11 个 Topic，成文仍只有一段定义 | 架构和能力未展开 |
| 其他实体 | 多数持有 1 个 Topic，成文仍未覆盖相应方向 | 召回和写作均未利用 |

当前文档中的事实本身大多有来源支持，主要问题不是单句正确性，而是内容覆盖、更新语义和引用组织不符合可持续维护的实体文档要求。

## 3. 现有问题点

### 3.1 P0：实体文档退化为定义卡片

实际生成结果只有实体定义，没有对已有证据中的机制、边界、能力、局限、冲突或不确定性做结构化综合。默认软模板包含“核心事实”和“证据、冲突与不确定性”，但模型可以直接省略，现有提示也没有在证据充足时阻止“只有定义”的结果。

影响：实体文档无法承担知识汇总页作用，后续问答仍需回到原始文章，enrich 的价值接近于零。

### 3.2 P0：discover 的 Topic 没有进入 enrich

Topic 已写入 `knowledge_entity`，通过 `object_kind='TOPIC'` 和 `subject_entity_id` 绑定 owner Entity。但 enrich 只加载 Entity 的名称、别名和旧 Markdown，没有查询 owner 下的 Topic。

影响：discover 已经识别出的研究方向既不参与召回，也不参与文章覆盖规划。实体名较宽泛时，单一查询容易只召回定义性片段。

### 3.3 P0：旧引用没有确定性保留保证

重复 enrich 会把旧 Markdown 传给模型，但旧链接是否保留完全依赖模型遵循提示。若第二次召回没有包含第一次使用的来源，模型可能重写段落并删除旧引用。

影响：更新一次文档可能造成来源回退，且无法区分“旧事实被证伪”和“模型只是没有再次看到旧来源”。

### 3.4 P1：首次生成与重复更新使用同一套全量召回逻辑

原流程没有读取上一次成功 enrich 任务的时间。每次都在整个知识库范围重新做关系证据选择和语义检索。

影响：

- 重复处理相同证据，增加模型成本；
- 旧证据可能再次占满 50k 字符预算，新证据反而进不来；
- 无法解释本轮究竟因哪些新增信息而更新；
- 没有新内容时仍可能重写文档，引入无意义漂移。

### 3.5 P1：新旧内容融合只有提示词约束

模型虽然收到 Existing Markdown，但原提示没有明确把它定义为必须就地编辑的权威基线，也没有明确规定冲突、新增和未再次召回的旧事实分别如何处理。

影响：模型可能生成一篇与旧稿割裂的新摘要，章节、措辞、事实和引用均发生不必要变化。

### 3.6 P1：大量 Topic 存在查询截断和请求膨胀风险

discover 最多允许 24 个 Topic。若一次性把全部 Topic 拼进查询，查询会受 1000 字符上限影响，尾部 Topic 容易被截断；若一个 Topic 发一次请求，则最多产生 24 次检索，成本和延迟不可接受。

影响：Topic 越多，覆盖反而越不稳定，且无法控制调用上限。

### 3.7 P1：引用规则导致机械注脚

原提示要求每个事实在首次相关陈述处引用。模型倾向把它简化为“每段末尾加链接”。当前文档虽然只有一个段落，但已经呈现 100% 段落尾注模式；文档扩展后会演变为每段重复同一来源。

影响：正文被链接切碎，阅读体验像证据清单而不是文章；重复引用也不能提升可追溯性。

### 3.8 P2：无新增证据时缺少无操作语义

`KnowledgeEntityEnricher` 要求至少存在一个证据片段。原 worker 没有在调用模型前区分“首次 enrich 无证据”和“重复 enrich 没有新证据”。

影响：重复 enrich 要么失败，要么通过重复旧证据强行更新，无法安全表达“当前无需更新”。

### 3.9 P2：可观测性不足

原任务结果只有证据片段数量、模板覆盖率和警告等信息，没有记录是否为增量模式、使用了多少 Topic、增量水位是什么，也没有无新证据的明确动作。

影响：无法评估 Topic 是否真正提升覆盖，也无法统计增量更新的有效率和跳过率。

### 3.10 已知上游约束：Topic 只持久化名称

当前 schema 不保存 Topic 的 `evidenceSummary` 或独立 Topic 证据表。discover 的原始结果仍存在任务结果中，但 enrich 的稳定资产读取只能获得 Topic 名称。

这不阻塞本次改造：Topic 名称可作为语义检索锚点，再由召回证据决定是否写入正文。它也意味着 Topic 不能被直接当作事实输入，避免把 discover 的概括误当成可引用证据。

## 4. 改造目标与非目标

### 4.1 目标

- 让 Topic 真正参与证据召回和文章覆盖规划；
- 首次 enrich 形成完整实体文档，证据充分时不得只输出定义；
- 重复 enrich 只检索上次成功任务之后新增或更新的信息；
- 将旧 Markdown 与新证据融合为同一篇持续维护的文章；
- 即使旧来源未被本轮召回，也确定性保留旧引用；
- 引用自然融入论述，不按段落机械添加；
- 对大量 Topic 设置稳定的检索调用和上下文预算；
- 无新证据时不改文件，避免文档漂移。

### 4.2 非目标

- 本次不新增 Topic evidence 表，也不修改 discovery 协议；
- 不把每个 Topic 强制转换成 Markdown 标题；
- 不尝试用程序判断旧事实是否已被新证据证伪；
- 不改变现有证据授权、相关性阈值、单文档片段上限和 50k 总字符预算；
- 不在 enrich 中重新裁决 Entity 或 Topic 身份。

### 4.3 问题与方案对应关系

| 问题 | 核心改造 | 主要落点 |
| --- | --- | --- |
| 文档只有定义 | 增加 Topic 覆盖协议，证据充分时拒绝定义式退化 | Enricher prompt |
| Topic 未使用 | 持久化读取，并同时用于召回和成文规划 | Asset Service、Worker、Enricher |
| 重复更新全量召回 | 读取上次成功 enrich 水位，过滤旧关系和旧文件 | Entity Repository、Worker |
| 新旧内容割裂 | 把旧 Markdown 设为权威编辑基线，按主题就地融合 | Enricher prompt |
| 旧引用可能丢失 | 新旧链接目标比较，遗漏链接恢复到参考资料 | Enricher post-process |
| Topic 过多 | 每 6 个一批，最多 4 次检索，统一证据预算 | Worker |
| 每段机械引用 | 改为连贯主张组引用，禁止固定段尾裸链接 | Enricher prompt |
| 无新证据仍重写 | 返回 `SKIPPED_NO_NEW_EVIDENCE`，不写文件 | Worker |
| 缺少运行信息 | 返回 Topic 数、水位、增量标记和跳过动作 | Task result |

## 5. 总体方案

```text
加载 Entity、旧 Markdown、持久化 Topic
                 │
                 ├─ 未 enrich / 无可靠历史任务 ── 全量召回
                 │
                 └─ 已 enrich / 有历史水位 ───── 增量召回
                                      │
               关系证据 + Topic 分批语义检索
                                      │
                  去重、过滤、限额、证据编排
                                      │
          以旧 Markdown 为基线进行结构化编辑融合
                                      │
             程序化检查并恢复遗漏的旧来源链接
                                      │
                   CAS 更新文件并重新构建索引
```

实现仍保持原有职责边界：

- `KnowledgeEntityAssetRepository/Service` 负责读取 Entity 的 Topic；
- `KnowledgeEntityRepository` 负责读取上次成功 enrich 水位；
- `KnowledgeEntityTaskWorker` 负责模式判断、召回和无变化跳过；
- `KnowledgeEntityEnricher` 负责编辑协议、Topic 覆盖提示和旧引用保留；
- `DocumentUpdateService` 继续负责 CAS 更新和 enrich 关系断言替换。

## 6. 详细改造方案

### 6.1 读取 Entity 的 Topic

通过 Entity 文件锚点查找 canonical Entity，再读取其直接绑定的 canonical Topic，按 Topic `kid` 保持稳定顺序。结果去空、去重后形成 `tuple[str, ...]`，传入本轮检索和成文阶段。

- 首次 enrich 读取该 Entity 的全部 Topic；
- 增量 enrich 只读取 `topic.updated_at > previousEnrichAt` 的 Topic；
- 没有新 Topic 时仍执行 Entity 名称与旧文档事实查询，以召回时间窗口内更新的原文；
- 旧 Topic 已经融入完整旧 Markdown，增量轮次不再把全部历史 Topic 重复塞入上下文。

Topic 是检索与覆盖提示，不是证据：

- Topic 名称可以出现在语义查询中；
- 模型只能在召回片段支持时写入 Topic 相关内容；
- 没有证据支持的 Topic 必须忽略；
- 不允许仅凭 Topic 名称生成事实、关系或结论。

### 6.2 判断首次模式和增量模式

模式判断同时依赖文件元数据和任务历史：

| `entityEnriched` | 上次成功任务时间 | 模式 | 原因 |
| --- | --- | --- | --- |
| 非 `true` | 任意 | 全量 | 文件从未可靠 enrich |
| `true` | 不存在 | 全量 | 缺少可信水位，不能冒险漏召回 |
| `true` | 存在 | 增量 | 可安全从上次成功点继续 |

水位取同一知识库、同一实体文件、任务类型为 `DOCUMENT_ENRICH`、状态为 `succeeded`、任务 ID 小于当前任务的最大 `finished_at`。

不使用当前实体文件的 `updated_at` 代替任务水位，因为文件更新时间还可能由元数据更新、重新索引或其他写操作改变，不能准确表达“上次 enrich 已消费到哪里”。

### 6.3 增量关系证据

首次模式沿用现有最近关系选择逻辑。增量模式只接受：

```text
relation.created_at > previous_enrich.finished_at
```

仍保留以下限制：

- 同一来源文件只取一条最近关系；
- 最多选择 3 个关系来源文档；
- 优先从来源原文中提取包含实体名或别名的章节；
- 找不到字面实体名时，在已确认关系的原文内按本轮 Topic 选取章节；
- 仍无命中时，使用 discovery 的确定性文档上下文作为兜底，不得只返回空证据；
- 长章节围绕 Entity/Topic 首次出现位置选取，再拆成不超过单片段限制的多个片段，避免实体段落在章节后部时被截掉；
- 未授权、空内容和目标实体自身内容不得作为证据。

### 6.4 Topic 分批语义检索

查询首先加入 Entity 规范名和别名，然后将每个当前批次 Topic 显式组合为 `Entity + Topic`，最后加入旧 Markdown 中的非标题事实行。Topic 不会以脱离 owner Entity 的独立查询词出现。

Topic 每 6 个组成一个批次：

| Topic 数量 | 检索次数上限 |
| --- | --- |
| 0 | 1 |
| 1—6 | 1 |
| 7—12 | 2 |
| 13—18 | 3 |
| 19—24 | 4 |

这样可同时满足：

- 每个 Topic 都有机会进入查询前部，不会统一被 1000 字符限制截断；
- 不产生逐 Topic 请求风暴；
- Entity 名始终出现在每个查询中，避免 Topic 脱离 owner 后发生语义漂移；
- 多批命中最终进入同一证据池统一去重和排序。

召回后还要校验 Entity 归属：语义片段本身未出现 Entity 名或别名时，只有来源文档已通过 discovery 关系明确指向该 Entity，或文档路径明确包含该 Entity 身份，才可保留。这防止“上下文工程”等多 Entity 共享 Topic 将 Hermes 证据误并入 OpenClaw。

增量模式为每次语义检索附加：

```json
{"gt": {"fieldName": "updatedAt", "value": "<previousEnrichAt>"}}
```

搜索返回后还会读取文件元数据并再次检查 `row.updated_at > previousEnrichAt`，防止搜索过滤器或索引状态造成越界命中。

### 6.5 大量 Topic 下的证据预算

增加查询批次不扩大最终模型上下文预算。所有批次命中合并后继续使用统一的 `organize_evidence`：

- 全局去重相同文件、位置和内容；
- 直接提及优先于普通语义相似；
- 显式关系优先于普通语义相似；
- 每个文档最多 25 个片段；
- 每个片段有字符上限；
- 总证据不超过 50k 字符。

Topic 多只提高召回覆盖，不线性放大传给模型的内容量。

### 6.6 新旧内容融合协议

旧 Markdown 被定义为权威编辑基线，而不是可丢弃的参考材料。文档读取后以全文原样进入 user message，不受 50k 证据预算、1000 字符检索词上限或单证据片段上限影响。这些限额只作用于新召回证据，不得截断待 enrich 文档。

Enrich system prompt 与 user message 结构指令统一使用中文，保留 JSON 字段名、关系枚举和 Entity/Topic 等协议标识。模型需要按以下规则编辑：

1. 保留仍受支持的旧事实、有效结构和表达；
2. 新证据补充已有主题时，合入对应段落或章节；
3. 新证据引入新主题时，才增加自然章节；
4. 新旧证据冲突时保留双方来源，并显式说明冲突或时间差异；
5. 不得仅因本轮没有再次召回某个旧来源，就删除旧事实或链接；
6. Topic 重叠时聚合到上位章节，不生成 Topic 清单式文章；
7. 证据支持多个重要 Topic 时，只有定义的一段式输出视为不合格。

该协议保证语义融合，但不做程序化段落拼接。盲目拼接旧段落会保留已经过时或被纠正的文字，也会制造重复章节。

### 6.7 旧引用的确定性保留

模型输出规范化后，对新旧 Markdown 中的 Markdown 链接按目标地址比较：

1. 新文档已经包含的链接保持原位置，不重复处理；
2. 旧文档存在、新文档遗漏的链接视为待恢复引用；
3. 若新文档没有“参考资料”章节，则创建该章节；
4. 将遗漏链接去重后追加为列表；
5. 任务警告记录恢复数量。

该兜底保证“旧引用不丢失”，但不声称旧引用仍支持某一条具体新句子。模型能自然保留时应继续放在正文；只有无法定位或被遗漏的链接才进入参考资料。

### 6.8 引用写作规则

引用单位从“段落”改为“连贯主张组”：

- 可写成“根据 [来源]，该机制……”；
- 也可在一组相邻且由同一来源支持的论述末尾引用一次；
- 同一来源支持连续陈述时不重复引用；
- 不得将裸链接作为每段固定后缀；
- 数值、版本、评价、机制和不确定性仍必须可追溯；
- 多个来源共同支持或相互冲突时，应在对应论述附近分别引用。

“减少引用数量”不是目标；目标是在不损失可追溯性的前提下消除机械重复。

### 6.9 无新增证据时跳过

增量模式下，如果新关系证据和新语义证据均为空：

- 不调用 LLM；
- 不更新实体文件；
- 不重新构建 Markdown 索引；
- 任务正常成功，动作返回 `SKIPPED_NO_NEW_EVIDENCE`。

首次模式没有证据时仍按错误处理，因为尚未生成可用实体文档，不能把空结果误报为成功更新。

### 6.10 版本和任务指纹

方法版本由 `enrich/1.0` 升级为 `enrich/2.0`。任务指纹因此可以识别新实现，避免旧版本成功结果阻止新版流程重新执行。

## 7. 数据一致性与失败处理

### 7.1 时间边界

时间比较统一使用带时区时间。语义过滤使用严格大于 `>`，因为上次任务完成时间之前创建或更新的内容已属于上一轮可见范围。

如果历史时间不存在、不是 `datetime` 或没有时区：

- 不存在时降级为全量模式；
- 非法时间视为数据错误并停止任务，避免错误水位造成静默漏召回。

### 7.2 并发更新

文档写入继续使用任务创建时的 `input_checksum` 作为 `refer_signature`。如果 enrich 期间文件已被其他操作修改，CAS 更新失败，不用旧基线覆盖并发新内容。

### 7.3 引用与关系的区别

正文引用是 Markdown 来源链接，本设计提供确定性保留。Entity 间语义关系仍按 enrich producer run 进行替换；不能把“保留旧来源链接”理解为永久保留所有历史 Entity 关系。

## 8. 可观测性

任务结果新增或明确以下字段：

| 字段 | 含义 |
| --- | --- |
| `topicCount` | 本轮加载的持久化 Topic 数量 |
| `incremental` | 是否采用增量模式 |
| `previousEnrichAt` | 本轮使用的上次成功任务水位 |
| `evidenceFragmentCount` | 最终交给 enrich 的证据片段数 |
| `SKIPPED_NO_NEW_EVIDENCE` | 已确认没有新增证据，因此未修改文件 |

Enricher 警告增加 `existing source references restored: N`，用于监测模型遗漏旧链接的频率。

建议后续增加以下聚合指标：

- Topic 查询批次数和 Topic 覆盖命中数；
- 首次/增量 enrich 任务比例；
- 无新证据跳过率；
- 每次增量新增来源数量；
- 旧引用自动恢复次数；
- 生成文档章节数、正文长度和来源去重率。

## 9. 验收方案

### 9.1 自动化测试

完整验收测试集应覆盖：

- Topic 正确绑定到 owner Entity 并按稳定顺序读取；
- 0、1、6、7、13、24 个 Topic 时的查询分批行为；
- 首次 enrich 不增加时间过滤；
- 已 enrich 且存在历史任务时增加 `updatedAt > previousEnrichAt`；
- 旧关系和旧文件命中被增量水位排除；
- 新关系和新文件命中可以进入证据池；
- 第二次生成遗漏旧链接时，旧链接进入“参考资料”；
- 已存在链接不会重复；
- 无新证据时不调用模型、不写文件、不重建索引；
- `enrich/2.0` 进入任务指纹。

### 9.2 “大厂文章知识库”回归验收

重新运行 enrich 后重点检查 OpenClaw、小红书写作规范和 GBrain：

- 文档不再只有一个定义段落；
- 重要且有证据的 Topic 被聚合到自然章节；
- 不要求 13 个或 11 个 Topic 生成同等数量标题；
- 正文中不出现每段固定追加同一链接的模式；
- 所有事实性扩展仍可追溯到原始文章；
- 再次运行且不新增资料时，任务返回无新证据跳过，文件 checksum 不变；
- 新增一篇相关资料后再次运行，只消费水位之后的资料，旧引用仍保留。

### 9.3 质量判定原则

不使用单一“引用数量”或“章节数量”作为成功标准。最终质量同时满足：

- 完整性：覆盖证据充分的主要 Topic；
- 紧凑性：重叠 Topic 被合并，没有目录式堆砌；
- 稳定性：无新增证据不改稿；
- 保真性：旧事实不会因本轮未召回而无故消失；
- 可追溯性：事实、数字、机制、评价和冲突均能定位来源；
- 可读性：引用服务于论述，不成为每段固定注脚。

## 10. 实施状态

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| Topic 持久化读取 | 已实现 | 首次读全量，增量只读水位之后更新的 Topic |
| Topic 分批语义召回 | 已实现 | 每批最多 6 个 |
| Entity + Topic 归属约束 | 已实现 | Topic 不单独检索，无 Entity 提及片段需要来源归属证据 |
| Topic 成文覆盖提示 | 已实现 | 作为覆盖指南而非事实 |
| 首次/增量模式判断 | 已实现 | 元数据与任务水位双条件 |
| 关系和语义证据时间过滤 | 已实现 | 查询过滤后再做服务端校验 |
| 无新证据跳过 | 已实现 | 不调用模型、不更新文件 |
| 旧 Markdown 基线协议 | 已实现 | 完整旧文档不截断进入上下文，由模型负责语义融合 |
| 原文直接上下文 | 已实现 | Entity 章节 → Topic 章节 → discovery 文档上下文三级选取 |
| 中文 Enrich Prompt | 已实现 | System Prompt 和 user message 结构指令已中文化 |
| 旧引用程序化恢复 | 已实现 | 按链接目标去重和恢复 |
| 自然引用提示 | 已实现 | 以连贯主张组为引用单位 |
| 证据按来源分组 | 已实现 | 同一来源只提供一次标准引用，下挂多个片段 |
| 跨标题引用归属 | 已实现 | 每个有证据主张的实质章节至少自然引用一次 |
| 引用标签可读性 | 已实现 | `article.md`/`index.md` 使用父目录文章标题 |
| 生成引用白名单 | 已实现 | 修正单来源内部路径，多来源歧义时移除虚构链接并告警 |
| Enrich 低温、低推理生成 | 已实现 | 温度固定为 `0.0`，`reasoning_effort=low`，不继承对话模型默认值 |
| 增量模式显式编辑协议 | 已实现 | 明确 delta 不是完整替代语料，逐节保留旧事实、限定词与不确定性 |
| 方法版本与结果字段 | 已实现 | `enrich/2.0` |
| 核心单元测试 | 已实现 | 覆盖增量过滤、Topic 分批、引用恢复和无变化跳过 |
| Topic 全边界参数化测试 | 待补充 | 补齐 0、1、6、7、13、24 边界 |
| 不落库真实运行器 | 已实现 | 保存输入、产物、确定性检查和 LLM 评审，异常也保存指纹 |
| 真实 LLM 全量回归 | 已通过 | 14/14 canonical Entity 首次 enrich 全部通过 |
| 无变化重跑 | 已通过 | 14/14 均未召回水位之前的证据 |
| 重复更新回放 | 已通过 | OpenClaw 的 retention=5、integration>=4，旧事实和旧引用保留 |

## 11. 不落库的真实运行评测流程

### 11.1 运行边界

`scripts/knowledge_base/evaluate_entity_enrich_quality.py` 直接复用生产运行时中的数据库读取、MinIO 读取、混合检索、Embedding 和 enrich LLM，但不会调用以下写路径：

- `DocumentUpdateService.update_file`；
- `KnowledgeItemIngestionService.file_to_markdown_index`；
- processing batch/task 创建或状态更新；
- Entity、Topic、关系断言或元数据写入。

每次运行只在 `eval/reports/entity-enrich-quality/<run-id>/` 写入本地评测材料，包括实际 LLM messages、证据统计、生成文档、增量回放结果和质量评分。

运行器会在评测前后读取以下可变状态并计算 SHA-256 指纹：

- 文件 checksum 和 `updated_at`；
- 文件元数据值；
- semantic processing task；
- semantic processing batch；
- 文件关系断言。
- Entity/Topic 资产行和检索投影。

前后指纹不一致时，即使文档质量通过，运行也会以“违反只读不变量”失败。

### 11.2 本地基线审计

只连接本机 OpenGauss 和 MinIO，不调用搜索、Embedding 或 LLM：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
.venv/bin/python scripts/knowledge_base/evaluate_entity_enrich_quality.py \
  --kb-code 1 --baseline-only
```

当前回滚基线结果：

- 14 个 canonical Entity 文件；
- 所有文件 `entityEnriched=false`；
- 共加载 47 个 Topic；
- 每个文件只有 76—126 个 Markdown 字符；
- 每个文件只有 1 个引用；
- 运行前后持久化指纹一致。

数据库中的第 15 条 `object_kind='ENTITY'` 记录是 alias，不是缺失文件锚点的 canonical Entity，因此实际待 enrich 文件数为 14。

### 11.3 首次 enrich 评测

真实调用检索、Embedding 和 LLM，但不持久化生成结果：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
.venv/bin/python scripts/knowledge_base/evaluate_entity_enrich_quality.py \
  --kb-code 1
```

运行器默认以 4 个 Entity 并发执行，可通过 `--concurrency` 覆盖：

```bash
.venv/bin/python scripts/knowledge_base/evaluate_entity_enrich_quality.py \
  --kb-code 1 --concurrency 4
```

并发是 Entity 级有界并发；同一 Entity 内的检索、首次生成、评审、无变化回放和增量回放仍按顺序执行。单个 Entity 失败不会取消同批其他 Entity，所有已完成报告与失败列表都会保留。

每个 `report.json` 的 `timingSeconds` 记录：

- `retrievalAndContext`；
- `fullGeneration`；
- `fullJudge`；
- `noChangeReplay`；
- `replayBaselineGeneration`；
- `incrementalGeneration`；
- `incrementalJudge`；
- `total`。

`summary.json` 另记录 `concurrency`、批次开始/结束时间、`durationSeconds`、`evaluationWallClockSeconds`、`cumulativeEntitySeconds`、`averageEntitySeconds`、`minEntitySeconds` 和 `maxEntitySeconds`。

迭代初期可以用 `--entity` 选择高 Topic、低 Topic 和不同文档类型的代表实体。最终验收必须去掉过滤，覆盖全部 14 个实体文件，不能只用少数样本宣称达标。

### 11.4 输入上下文审计

每个实体保存完整 `full-input.json`，并检查：

- Topic 数量及查询批次数；
- 证据片段数、字符数和来源文档数；
- 单来源片段分布；
- 直接提及和显式关系证据比例；
- LLM prompt 总字符数；
- 是否超过 50k 证据预算；
- 是否混入未授权片段。

输入合理性不能只看总字符数。还要人工检查 Topic 是否真的改善召回覆盖、某一来源是否垄断上下文、旧 Markdown 是否挤压新证据，以及低相关片段是否因 Topic 批次增加而大量进入。

### 11.5 无变化重复运行

运行器以评测开始后的 UTC 时间作为模拟增量水位，再调用真实 `_collect_evidence(updated_after=cutoff)`。在评测期间没有新增资料时，期望所有实体 `newEvidenceCount=0`。

这验证的是生产 worker 会走 `SKIPPED_NO_NEW_EVIDENCE`，不会再次调用 LLM 或改写文档。它不通过真的写入一条成功任务来制造水位，因此不会污染数据库。

### 11.6 新增证据更新回放

为验证重复更新质量，运行器按来源文件 ID 稳定地把真实召回证据划分为 baseline 和 delta：

1. baseline 证据生成第一版文档；
2. delta 证据作为下一轮新增材料；
3. 第一版 Markdown 作为权威旧稿；
4. 第二次调用真实 enrich LLM；
5. 检查旧引用保留、新证据融合、结构重复和事实回退。

优先按完整来源文件划分，避免同一文件的片段同时出现在 baseline 和 delta；来源不足时才按片段划分。该回放只模拟时间顺序，不修改源文件时间或数据库任务记录。

### 11.7 通用质量门

确定性检查包括：

- H1 与权威 Entity 名完全一致且只有一个 H1；
- 旧引用全部保留；
- 新引用只能来自本轮授权证据或旧稿；
- 证据明显丰富时不能退化成少于 400 个正文字符、少于 3 个标题的定义卡片；
- 段尾引用覆盖率过高时判定为机械尾注，自然嵌入主体的引用不受此惩罚；
- 无变化回放不能召回水位之前的证据。

LLM 评审使用与实体名称无关的固定量表，对 groundedness、coverage、synthesis、citation quality、maintainability 各打 1—5 分；增量文档额外评估 retention 和 integration。所有适用项不低于 4 且无 critical 缺陷才算通过。

长度和标题数只用于识别明显的定义式退化，不用于奖励长文或多标题。最终仍以证据覆盖、事实可靠、融合稳定和引用自然为准，禁止加入针对 OpenClaw、小红书、GBrain 或当前 14 个实体的名称、章节和预期事实特例。

### 11.8 2026-08-28 真实回归结果

数据保持在 discover 完成、enrich 尚未执行的回滚状态。本轮调用真实混合检索、Embedding 和 enrich LLM，但没有调用文档更新、索引重建或任务写入路径。

| 验收项 | 结果 |
| --- | --- |
| canonical Entity 首次 enrich | 14/14 通过确定性门禁和完整证据 LLM 评审 |
| 无变化重跑 | 14/14 `newEvidenceCount=0` |
| 高 Topic 多来源 | OpenClaw 通过首次与增量回放 |
| 高 Topic 单来源 | GBrain 通过 |
| 规范类文档 | 小红书写作规范通过 |
| 低 Topic 窄证据 | OWL 通过，未出现强行长文化 |
| 增量保留 | OpenClaw `retention=5`，`integration>=4`，旧引用集合完整保留 |
| 引用可读性 | 来源名称替代 `article.md`，主张组/章节级引用，不再以每段尾注为默认 |
| 只读不变量 | 所有真实运行前后指纹一致：`6065c14bd46fcbb2f2fe207c305e8cd48c977cd841daf6bd9869a224acc90400` |

迭代中发现并修复的通用问题包括：Topic 分批检索之间不应共享一个分数阈值；证据不应按片段重复注入同一来源链接；增量 delta 不应被视为完整替代语料；持久知识编辑不应继承对话温度；生成引用必须经过授权目标白名单。这些规则均不包含样本实体名、样本事实或固定章节。

### 11.9 Low reasoning 耗时回归

2026-08-28 将 enrich 与 discover 统一为 `temperature=0.0`、`thinking.type=enabled`、`reasoning_effort=low`。使用同一 GBrain 输入做非持久化对比：

| 项目 | 调整前 | Low reasoning |
| --- | ---: | ---: |
| Enrich LLM 墙钟耗时 | 184.2 秒 | 29.0 秒 |
| Reasoning tokens | 25,538 | 2,960 |
| 确定性质量门 | 通过 | 通过 |
| 完整证据 LLM 评审 | 通过 | 通过 |
| 无变化重跑 | 通过 | 通过 |

Low reasoning 将该样本的 enrich 耗时降低约 84.3%，reasoning tokens 降低约 88.4%，未使质量门禁退化。报告保存在 `eval/reports/entity-enrich-quality/20260828T021112974491Z/35-GBrain/`。

### 11.10 OpenClaw 两时点真实回放

评测脚本支持指定两篇真实原文，串行模拟两个导入时点：

```bash
.venv/bin/python scripts/knowledge_base/evaluate_entity_enrich_quality.py \
  --kb-code 1 \
  --two-stage-entity OpenClaw \
  --stage-source '/大厂文章/从架构到代码：深入理解_OpenClaw_的双源记忆系统/article.md' \
  --stage-source '/大厂文章/深度解析_OpenClaw_在_Prompt_Context_Harness_三个维度中的设计哲学与实践/article.md'
```

回放协议不依赖 OpenClaw 特例规则：

1. T1 只读第一篇原文，执行真实 discover，通过权威实体名或 alias 确认候选实体，只保留该 `entityRef` 拥有的 Topic；
2. 使用生产渲染逻辑构造 `entityEnriched=false` 的实体骨架，执行首次 enrich；
3. T2 只读第二篇原文并重新执行真实 discover，再次确认命中同一权威实体；
4. Topic 时间窗口只向 T2 enrich 传递 T2 新发现且 T1 不存在的 Topic，不全量重放旧 Topic；
5. T2 的新证据只能来自第二篇原文，但完整 T1 文档作为不截断的权威旧稿进入上下文；
6. 检查 T1 引用是否全部保留、T2 来源是否新增、新内容是否融入原结构；
7. 运行前后比较持久化指纹，不写入业务文档、任务、索引或关系。

2026-08-28 的真实回放保存在 `eval/reports/entity-enrich-quality/20260828T023353392381Z/`。两篇原文都成功发现并匹配 OpenClaw。T1 发现 3 个 Topic；T2 虽发现 5 个 Entity，但没有产出归属 OpenClaw 的 Topic，因此 T2 Topic 增量窗口为空，证据仍由第二篇原文的 Entity 相关段落提供。

| 验证项 | T1 | T2 |
| --- | ---: | ---: |
| Discover | 29.491 秒 | 39.782 秒 |
| Enrich | 36.307 秒 | 30.065 秒 |
| 质量评审 | 14.986 秒 | 21.454 秒 |
| 确定性门禁 | 通过 | 通过 |
| 旧引用保留 | 不适用 | 通过，1/1 保留 |
| 当前来源引用 | 通过 | 通过 |
| LLM 质量门 | 不通过 | 不通过 |

T1 的主要缺陷是未充分展开已发现的“记忆信息的检索与调用” Topic，coverage=3。T2 的 retention=5，证明完整旧稿与旧引用保留约束生效；但新内容以新章节直接追加，没有与原有记忆架构融合，synthesis=3、integration=2。因此该场景真实暴露了下一步需要优化的通用问题：首次生成的 Topic 覆盖约束，以及增量生成的原结构就地编辑约束。

## 12. Topic × Source 证据配额与跨召回合并

不再将全部关系原文从文章开头顺序拼接到统一字符上限。对每个 Topic 分别执行语义查询，关系原文也分别选取实体总览和 Topic 匹配章节。每个 Evidence fragment 携带 `matched_topics`，最终预算组为：

```text
(normalized Topic, sourceFileId, mention | semantic)
```

选择分两轮：

1. 按预算组稳定顺序选取，每组保留最低字符配额；
2. 所有已出现组获得保底后，再按 mention、显式关系、语义分数和稳定来源顺序填充剩余总预算。

默认每组目标最低配额为 1,200 字符。当组数过多时，实际配额自动降为 `totalBudget / groupCount`，确保不突破 50k 总预算。mention 和 semantic 即使来自同一文档，也是独立预算组，避免大段 mention 证据吃完该来源的全部配额。

在计算配额前，先按来源合并 mention 与 semantic 交集：

- 文本相同、一方包含另一方，或共享实质段落时，语义片段合并进 mention fragment；
- 合并结果保留 `direct_mention=true`，同时保留最高 `semantic_score` 和所有 Topic 归属；
- 该 fragment 可同时满足 mention 与 semantic 两个保底组，但在总字符预算中只计一次；
- 同一语义 chunk 同时命中多个 Topic 时，只保留一份内容，`matched_topics` 取并集。

评测输入存档增加 `semanticSearchQueryCount`、`topicSourceQuotaGroups`、mention/semantic fragment 数和交集合并数，便于检查每个 Topic 和来源是否真正获得证据。

Topic 原文定位同时兼容标准 Markdown 标题和微信转 Markdown 后丢失 `#` 的普通文本标题：

- 有 Markdown 标题命中时，优先专题标题章节，不使用前言中偶然出现 Topic 的章节；
- 长章节中优先以 Topic 开头的短独立行作为专题锚点；
- Topic 模式只用 Topic 定位，不让高频 Entity 名称抢占窗口锚点；
- 锚点必须位于拆分后的首个 evidence fragment，避免保底配额只拿到专题前的背景文本。

## 13. P0/P1 成文质量闭环

### 13.1 P0：EvidenceClaimGroup

证据完成限额和去重后，在调用 LLM 前确定性生成 `EvidenceClaimGroup`。证据
选择仍按 `Topic × Source × RecallKind` 分配最低配额，但成文覆盖义务按 `Topic`
聚合：一个 Topic 组可以授权多个 Source，避免同一主题因来源多而被拆成大量段落和引用。
未携带 Topic 的实体级证据归入“实体总览”。每组记录：

- 稳定的 `claimGroupId`；
- `sourceIds`、来源文件和关联 `evidenceIds`；
- 证据实际支持的定义、架构、机制、场景、指标、限制与比较等子主题；
- 推荐落入的章节；
- 是否属于必须覆盖的主张组。

Topic 只决定证据的研究方向，`supportedAspects` 才决定文章需要展开的内容。因此即使只有
一个 Topic，只要证据同时支持机制、场景和限制，也不能退化成一段定义。Topic 很多时只为
最终入选证据建立主张组，不会把没有召回证据的 Topic 转换为写作义务，也不会突破 50k
证据预算。

模型需要在 `claimCoverage` 中返回主张组、目标章节、正文中真实存在的短锚点和使用的来源。
程序化校验同时验证：主张组存在、锚点确实位于目标章节、来源属于该组，且该来源链接实际
出现在同一章节。仅在 JSON 中声称“已引用”不能通过校验。

### 13.2 P0：增量编辑计划

增量模式先解析完整旧 Markdown 的 H2/H3 章节，再把每个新主张组映射为编辑提示：

- `NEW`：合并到语义相近的旧章节，必要时才新增章节；
- `ALREADY_COVERED`：旧稿已包含相同证据，不重复写；
- `CORRECTION`：只纠正受影响的事实；
- `CONFLICT`：保留新旧说法和来源，明确差异。

模型必须返回 `editPlan`。校验器检查 required 主张组都有合法状态、动作和最终存在的目标章节。
旧 Markdown 仍全文、不截断地放入输入；旧引用继续由链接集合差分做确定性恢复。此外，
程序从旧文档每个实质 H2/H3 中提取最多 3 个 `ExistingClaimAnchor`，优先保护数字、
代码标识符、指标和限定条件。模型需在 `oldClaimRetention` 中为每个锚点返回
`KEEP`、`CORRECTION` 或 `CONFLICT`。`KEEP` 需在最终章节中保留事实与关键词；
后两者必须绑定本轮授权来源。因此“链接还在、旧事实消失”也会被判定为更新失败。

### 13.2.1 P0：实体证据隔离

关系文档的 Topic 章节和语义检索 chunk 可能同时包含多个产品。证据进入预算前，
根据实体名、别名、Markdown 标题和独立产品描述块进行二次限界。只因 Topic 相同而与
目标实体相邻的其他产品块不得进入证据。已确认的显式关系证据在没有字面实体名时仍
保留原有保底；普通语义命中不使用该保底。

### 13.3 P0：主张组引用和定向修复

模型返回 `citationPlan`，每项绑定一个 ClaimGroup 和其授权 Source。文档生成后检查：

- required 主张组是否都有有效引用计划；
- 有事实内容的 H2/H3 是否至少包含一个正文来源链接；
- 连续段落是否重复引用同一来源；
- 段尾机械引用比例是否高于阈值；
- 是否存在重复实质段落；
- 新章节开头是否使用跨标题后含混的代词。

正常路径仍只有一次 low-reasoning enrich 调用。只有模型采用新版
`qualityPlanVersion=1` 且确定性校验失败时，才追加一次定向修复调用。修复输入明确列出缺失
ClaimGroup、无引用章节、重复引用和含混主语，只允许局部融合，不允许重新自由改写无关章节。

### 13.4 P1：相邻证据与信息密度

在 Topic × Source 配额前，来自同一来源、召回类型一致、行区间相邻或内容实质重叠的片段会
合并。合并不能超过单片段字符上限；否则保留为两个片段，避免为了去重而截掉后半段。合并结果
保留全部 Topic、最高语义分数和 mention/reference 属性。

同一优先级和语义分数下，不再只按来源顺序填充剩余预算，而是优先选择信息密度更高的证据。
信息密度由可区分事实块数量、支持的子主题种类以及数字/指标数量共同计算。它只作为同相关度
证据的排序信号，不能让普通语义命中越过直接 mention 或显式关系证据。

成文时不按 EvidenceFragment 顺序改写，而是先在 Topic 内合并同义事实，再把每个事实放入
唯一最合适的章节。确定性审计除了完全重复，也识别包含式和高相似度的近似重复段落，
并在一次定向修复中要求合并。

### 13.5 评测归档

每个 Entity 的 `report.json` 增加 `protocol`：

- `claimGroups` 和 `editHints`；
- 是否触发定向修复及总调用尝试数；
- required/covered/uncovered ClaimGroup；
- 无引用章节、无效编辑/引用计划、重复引用比例、重复段落和含混主语等质量信号。

这些字段同时写入首次 enrich、普通增量回放和 OpenClaw 两时点真实回放，最终全量报告可直接
解释每个 Entity 为什么通过或失败，而不只给出一个总分。

## 14. 2026-08-28 全量验证结果

实现完成后运行 knowledge base 全量单元测试，结果为 `843 passed, 6 skipped`；变更文件通过
pre-commit 的 isort、ruff、ruff-format、pylint、pyink 和通用文本检查。

最终全量评测主批次归档在
`eval/reports/entity-enrich-quality/20260828T053603016916Z`：4 并发运行 14 个实体，墙钟时间
746.318 秒；13 个正常完成实体平均 161.695 秒。Hermes Agent 首次运行因 LLM 修复响应顶层
返回数组中断，单独补跑归档在 `eval/reports/entity-enrich-quality/20260828T054954321802Z`，耗时
165.586 秒。合并 14 个最终有效结果后，平均约 161.973 秒/实体。评测前后持久化指纹一致，
没有把候选文档写回数据库。

最终结果：

- 14/14 通过引用授权、实体边界、结构和旧内容安全等确定性门禁；
- 首次成文 10/14 通过 LLM 内容质量评审；
- 具备增量重放的 8/8 全部通过，旧内容保留和新旧融合没有失败；
- OpenClaw 两时点真实导入场景的两个阶段均通过，第二阶段 retention 和 integration 均通过；
- 首次未通过内容评审的实体为 GBrain、小红书写作规范、OpenAI Frontier 和 Hermes Agent。

剩余问题已收敛为两类。GBrain、小红书写作规范和 Hermes Agent 属于“单来源、多 Topic、证据
高度重合”，现有 Topic 级 ClaimGroup 仍可能驱动模型分节复述同一事实；OpenAI Frontier 属于
证据极少时仍套用完整结构，正文相对原材料过度膨胀。下一步应在 ClaimGroup 前增加跨 Topic 的
FactCluster，并增加低证据密度短文路由。逐实体原因和优化方向见该批次内的
`QUALITY-ANALYSIS.md`。
