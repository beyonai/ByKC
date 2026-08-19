# Markdown 文件引用关系去重设计

日期：2026-08-19

状态：已实施，待验收

## 1. 背景

当前 Markdown import、zip import 和 update 流程会扫描 `![]()`、`[]()` 文件引用，
为每一次引用出现创建一条 `knowledge_file_reference` 记录，再将 Markdown target
替换为 `byqa-ref://<kid>`。read、download、search 在输出时根据 `kid` 查询目标文件
当前路径，并把内部 token 还原为用户可见引用。

现有模型把“一次引用出现”和“一条文件关系”合并成同一个概念：同一源文件内即使
多次引用同一个目标文件，也会产生多条记录。引入
`source_heading_path`、`start_line/end_line`、`start_offset/end_offset` 后，每次出现的
位置被纳入 `evidence_fingerprint`，进一步保证了每个位置都是独立物理断言。

这种设计适合保存精确证据位置，但当前业务不再需要 Markdown 引用所在章节、行号和
字符偏移。继续按出现次数保存会造成：

- `knowledge_file_reference` 行数随引用出现次数增长，而不是随有效文件关系增长；
- import/update 对同一目标重复执行文件查询和关系写入；
- move、delete、restore 需要批量更新重复关系行；
- references 查询返回重复 source-target 关系；
- 同一来源的大量重复 Markdown 断言会挤占实体 Enrich 最近证据的查询窗口。

## 2. 目标与非目标

### 2.1 本期目标

1. 对 Markdown Parser 产生的引用按“源文件—目标文件”去重。
2. 同一源文件内指向同一目标文件的所有引用共用一条数据库记录和同一个关系 `kid`。
3. 不再为 Markdown 引用保存章节、行号和字符偏移。
4. query 和 fragment 仍按每次出现分别保留。
5. read、download、search 继续输出目标当前路径，并且不泄漏内部 token。
6. 目标 unresolved、move、delete、restore 后，query 和 fragment 保持不变。
7. 历史内部 token 继续兼容；部署时不要求批量重写对象存储和 chunk。
8. import、zip import、update 使用同一套关系去重规则。

### 2.2 明确不做

1. 不修改 `knowledge_file_reference` 表结构。
2. 不删除 `source_heading_path`、行号、偏移等现有列。
3. 不修改 Discovery/Enrich 关系的证据粒度。
4. 不新增引用出现表、token alias 表或独立关系表。
5. 不在数据库中保存每个 Markdown 引用的原始相对路径、query、fragment 或出现次数。
6. 不通过纯 SQL 立即合并历史重复记录。
7. 不让不同源文件共享同一条关系记录。

## 3. 核心语义

### 3.1 去重边界

一条 Markdown 文件关系的逻辑身份是：

```text
knowledge_base_id
+ source_fs_entry_id
+ relation_code = MENTIONS
+ discovered_by = MARKDOWN_PARSER
+ target identity
```

目标身份按当前解析状态表示：

- 已解析目标：以 `target_fs_entry_id` 表示当前绑定；
- 未解析目标：以规范化后的 `KB_PATH + target_locator_value` 表示；
- 写入分组统一使用规范化 KB path，使同一次解析中 resolved 和 unresolved 采用相同规则。

这里的“同一个文件”只在同一个源文件内去重。例如：

```text
/docs/a.md -> /assets/logo.png   一条记录
/docs/b.md -> /assets/logo.png   另一条记录
```

不能把整个知识库中指向 `/assets/logo.png` 的引用合并成一条记录，否则会丢失来源文件，
无法支持 inbound/outbound 查询和源文件更新时的范围删除。

### 3.2 关系数据与出现数据分离

数据库行只保存关系级状态：

- source、target 当前绑定；
- 目标恢复 locator；
- resolved/unresolved/broken 状态；
- relation、producer 和本次 parser run；
- 关系级 evidence fingerprint。

每次出现自己的 query 和 fragment 直接保留在 Markdown 内部 token 后面，不写入数据库。
原始相对路径、URL 编码形式和 target 前后的空白不再保留。

因此，多次出现可以使用相同关系 `kid`，同时仍能保留各自不同的 query 和 fragment。

### 3.3 不变与变化

不变：

- `target_fs_entry_id` 仍是目标文件移动后的稳定绑定；
- `target_locator_value` 仍用于目标删除、恢复和 pending 补偿；
- read/download/search 仍在输出边界动态解析 token；
- query 和 fragment 不会重复拼接。

变化：

- `knowledge_file_reference` 的 Markdown 行从“一个出现位置”变为“一条逻辑文件关系”；
- references 查询对同一 source-target 只返回一项；
- Markdown 行的位置列固定为 NULL；
- Markdown 行的 `original_target` 和 `target_suffix` 不再承担逐次出现的还原职责；
- unresolved/broken 时回退为关系行保存的规范 KB path，不保证恢复用户原始相对写法；
- semanticRelations 中由 Markdown 贡献的 assertion 数量不再等于正文出现次数。

## 4. 内部 token 协议

### 4.1 格式

内部 target 使用现有 token，不引入 token 版本或 payload：

```text
byqa-ref://<relation_id><suffix>
```

其中：

- `relation_id` 是 `knowledge_file_reference.kid` 的十进制表示；
- `suffix` 是当前 occurrence 原 target 中从第一个 `?` 或 `#` 开始的剩余部分；
- 没有 query/fragment 时，suffix 为空；
- suffix 保存在 Markdown 中，不写入 `knowledge_file_reference.target_suffix`。

`byqa-ref://<relation_id>` 是需要动态解析的部分，后面的 suffix 保持普通文本。现有
`REFERENCE_TOKEN_RE` 已经只匹配数字 ID 部分，因此 resolver 替换 token 后，suffix 会自然
保留，无需引入新的 token codec。

### 4.2 共用关系示例

输入：

```markdown
[概览](b.md#overview)
[接口](./b.md?download=1#api)
![示意图](b.md#diagram)
```

假设三个 target 归一化后都指向 `/docs/b.md`，数据库只写一行，`kid=42`。
内部 Markdown 为：

```markdown
[概览](byqa-ref://42#overview)
[接口](byqa-ref://42?download=1#api)
![示意图](byqa-ref://42#diagram)
```

三个 token 的 `relation_id` 相同，query/fragment 各自保留。`b.md` 和 `./b.md` 的原始
路径差异不再保存；resolved 时三者都输出目标当前路径，unresolved/broken 时三者都使用
关系级规范 KB path。

### 4.3 兼容规则

历史内容同样使用 `byqa-ref://<relation_id>`，但 suffix 存在数据库行的 `target_suffix` 中。
新旧内容通过关系行字段自然兼容：

- 历史行：`row.target_suffix` 可能非空，Markdown token 后没有 suffix；
- 新关系行：`row.target_suffix` 为空，suffix 直接位于 Markdown token 后；
- resolver 继续只替换 `byqa-ref://<relation_id>`，两种格式都能得到正确结果；
- materializer 使用相同规则，update 前可以同时物化历史内容和新内容；
- 关系行缺失时没有 occurrence fallback，读路径使用安全空串并记录 warning，update 使用
  fail-fast。

新设计没有引入新的 token 版本，旧服务实例也能读取新内容，因此不需要读写双版本开关。

## 5. 数据库存储约定

本设计复用现有所有字段，不执行 DDL。

### 5.1 新 Markdown 关系行的字段值

| 字段 | 新语义 |
| --- | --- |
| `knowledge_base_id` | 当前知识库 |
| `source_fs_entry_id` | 当前 Markdown 文件 |
| `target_fs_entry_id` | 已解析目标 ID；未解析时为 NULL |
| `original_target` | 规范化 KB path，不含 query/fragment；仅作关系级兼容值 |
| `target_path` | unresolved/broken 时的规范化 KB path |
| `target_suffix` | 空串；openGauss A 兼容模式下可表现为 NULL |
| `target_kind` | `FILE` |
| `status` | `resolved`、`unresolved` 或 `broken` |
| `relation_code` | `MENTIONS` |
| `discovered_by` | `MARKDOWN_PARSER` |
| `producer_run_id` | 当前 import/update parser run ID |
| `evidence_fingerprint` | 关系级稳定指纹 |
| `source_heading_path` | NULL |
| `start_line/end_line` | NULL/NULL |
| `start_offset/end_offset` | NULL/NULL |
| `target_locator_type` | `KB_PATH` |
| `target_locator_value` | 规范化 KB path |
| `source_task_id` | NULL |

### 5.2 关系级 fingerprint

Markdown 关系 fingerprint 不再包含 span、标题、行号或原始文本，建议按以下规范生成：

```text
sha256(
  "markdown-relation"
  + ":" + knowledge_base_id
  + ":" + source_fs_entry_id
  + ":MENTIONS:KB_PATH:"
  + normalized_target_path
)
```

`producer_run_id` 在同一次 rewrite 内保持不变。现有
`uq_kfr_exact_assertion` 已包含 knowledge base、source、relation、locator、producer、
producer run 和 fingerprint；同一 parser run 内即使发生重复 upsert，也会返回同一行。

本设计不依赖新增唯一索引。跨 run 的唯一性由 import/update 的源文件生命周期控制：

- import 创建全新的 source 文件，不存在旧出边；
- update 已持有 source 文件行锁，并在重写前删除该 source 拥有的旧出边；
- zip 内每个 source 文件仍只允许一个上传事务成功创建。

Repository 应提供语义明确的 `upsert_markdown_relation` 适配器，避免其他调用方绕过关系级
fingerprint 规则；通用 `upsert_relation_assertion` 和 Discovery/Enrich 行为保持不变。

## 6. Markdown 重写流程

当前实现按 span 逐个查询目标、逐个 upsert。本设计改为“先分析分组，再写关系，再替换”。

### 6.1 第一阶段：解析 occurrence

对 `detect_reference_spans(text)` 返回的每个引用：

1. 对 target 做 trim，用于资格判断和路径解析。
2. 用 `split_target` 分离 path 与 suffix，suffix 仅保留在当前 occurrence 内存结构中。
3. 跳过页内 `#anchor`、协议 URL、`//` URL、逃出知识库根路径和目录引用。
4. URL decode path，并通过 `normalize_kb_path` 得到规范 KB path。
5. 记录完整 target 在原 Markdown 中的替换区间。

Occurrence 只存在于当前方法内，不创建数据库对象。

### 6.2 第二阶段：按目标分组

分组键为：

```text
(target_locator_type="KB_PATH", target_locator_value=normalized_target_path)
```

同一组只执行一次目标文件查询。若目标不存在，再执行一次目录查询以排除目录链接。

目录查询发现目标是目录时，整组引用保持原样且不登记关系。

### 6.3 第三阶段：写关系

每个有效分组调用一次 `upsert_markdown_relation`：

- resolved：填写 `target_fs_entry_id`，`target_path=NULL`；
- unresolved：`target_fs_entry_id=NULL`，`target_path=normalized_target_path`；
- occurrence 位置字段全部传 NULL；
- `original_target=normalized_target_path`；
- `target_suffix=""`；
- 使用关系级 fingerprint。

Repository 返回关系 `kid`，分组内所有 occurrence 共用该值。

### 6.4 第四阶段：替换 target

每个 occurrence 的完整 target 被替换为：

```text
byqa-ref://<group_relation_id><occurrence_suffix>
```

例如 `./b.md?download=1#api` 替换为
`byqa-ref://42?download=1#api`。原始 path 和外围空白不写入新内容。

替换仍按原字符 offset 从左向右执行。日志区分：

- `parsed_count`：扫描到的 Markdown 引用次数；
- `occurrence_count`：成功改写的出现次数；
- `relation_count`：实际写入或命中的唯一关系数；
- `resolved_relation_count`；
- `pending_relation_count`；
- `skipped_occurrence_count`。

引用数量上限继续按 occurrence 数量计算，避免超大 Markdown 导致无界内存使用。

## 7. 输出解析流程

### 7.1 批量查询

read、download、search 先扫描所有 `byqa-ref://<reference_id>`，按 ID 去重后调用一次
`list_by_reference_ids`。

由于同一目标的多次 occurrence 共用 ID，新内容的查询行数将从 occurrence 数下降为唯一
关系数。

### 7.2 替换规则

resolver 只替换 token 的数字 ID 部分，不消费 token 后面的 query/fragment：

1. 若关系 `status=resolved`、目标存在且未删除，token 替换为
   `target.virtual_path + row.target_suffix`。
2. 若 unresolved、broken 或目标已删除，token 替换为 `row.original_target`。
3. Markdown 中紧随 token 的 occurrence suffix 保持原位。
4. 若关系行缺失，使用安全空串替换 token 并记录 warning，不能向用户输出内部 token。

历史行的 suffix 来自 `row.target_suffix`，其 Markdown token 后没有 suffix；新行的
`row.target_suffix` 为空，suffix 位于 Markdown token 后。因此相同 resolver 逻辑同时兼容
历史内容和新内容，并避免 query/fragment 重复拼接。

### 7.3 update 前物化

update 接收的正文可能来自 readFile，也可能包含客户端回传的内部 token。现有流程会在删除
旧出边前执行 `materialize_existing_tokens`，本设计保持顺序不变：

1. 扫描 token；
2. 批量读取关系行；
3. 按第 7.2 节规则恢复为普通 Markdown target；
4. 任一 token 缺少关系行时终止 update；
5. 删除 source 旧出边；
6. 用新设计重新分析、分组、写关系和生成共享 token。

这个流程会在文件第一次 update 时自然把旧的逐 occurrence 记录压缩成关系级记录。

## 8. import、zip import 与 update 集成

### 8.1 单文件 import

保持当前事务边界：创建 source `knowledge_fs_entry` 后，在写对象存储前执行 Markdown
关系重写。关系写入和 source 创建随同一数据库事务提交；对象写入失败时沿用现有补偿。

### 8.2 zip import

保持“非 Markdown 优先、Markdown 后上传”和 pending 补偿机制。单个 Markdown 文件内部
按唯一规范路径分组；不同 Markdown 源文件之间不共享关系行。

zip 内 md-to-md 目标尚未上传时，关系以 unresolved 保存；目标上传完成后，现有
`resolve_pending_for_path` 只更新这一条 source-target 关系，不影响 Markdown 中保留的
query/fragment。

### 8.3 update

保持当前顺序：

```text
锁定 source
-> 读取旧内容
-> 物化已有 token
-> 删除 source 拥有的旧出边
-> 按唯一目标重写 Markdown 关系
-> 写入调用方提供的生成关系
-> 更新对象和派生状态
-> commit
```

普通 Markdown update 的新关系数量等于新正文唯一有效目标数，而不是引用出现次数。

### 8.4 文件与目录删除

删除 source 和删除 target 的关系处理语义不同：

- 被删文件作为 source 的全部出向关系直接物理删除，不区分 Markdown、
  Discovery 或 Enrich producer；
- 其他未删 source 指向被删 target 的入向关系保留，并通过
  `mark_targets_deleted` 转为 `broken`，以支持同路径恢复；
- 单文件删除使用 `delete_outgoing_for_source_fs_entry_id`；
- 目录删除使用 `delete_outgoing_for_source_fs_entry_ids` 一次删除子树内所有
  source 的出向关系。

同一事务内的顺序为：

```text
删除被删 source 拥有的出向关系
-> 将指向被删 target 的剩余入向关系标记为 broken
-> 软删除文件或目录子树
```

## 9. references 与 semanticRelations 语义

### 9.1 references

接口路径和响应结构保持不变，但新 Markdown 的返回粒度变为 source-target：

- inbound：每个来源文件最多返回一条指向当前目标的 Markdown 关系；
- outbound：每个目标文件最多返回一条当前 source 的 Markdown 关系；
- `sourcePath`、`targetPath`、`status` 保持原语义；
- `originalTarget` 返回关系级规范 KB path；
- `targetSuffix` 对新记录返回空串。

`originalTarget` 和 `targetSuffix` 不再表示某一个正文 occurrence。逐 occurrence 的
query/fragment 只存在于源 Markdown 中，不通过 references 接口展开；原始相对路径不再
保留。

### 9.2 semanticRelations

逻辑关系仍按 `source_fs_entry_id + relation_code + target_fs_entry_id` 聚合。对 Markdown
Parser：

- 同一 source-target 只贡献一个物理 assertion；
- `assertionCount` 不再表示 Markdown 正文出现次数；
- `representativeEvidence.sourceHeadingPath/startLine/endLine/startOffset/endOffset`
  均为空；
- Discovery/Enrich 产生的其他 assertion 仍可贡献自己的证据和计数。

Enrich 的最近证据文件选择仍可通过 source ID 工作；去重后，单个来源的重复链接不会再
占用多个 recent assertion 名额。

## 10. 历史数据与上线迁移

### 10.1 为什么不能 SQL 去重

历史原文件、Markdown sidecar 和 chunk 中保存的是具体旧行的 `byqa-ref://<kid>`。
如果仅在数据库删除重复行：

- read/download 无法解析被删除的 ID；
- search chunk 会出现缺失引用；
- update 物化旧 token 时会失败；
- 现有对象存储不在数据库 migration 的事务范围内，无法原子修复。

因此，本设计禁止在上线 migration 中直接合并或删除历史 Markdown 引用行。

### 10.2 兼容上线顺序

1. 部署 rewriter 的关系级分组和 suffix 内联写入。
2. 历史文档、旧 token 和旧关系行原样保留并继续可读。
3. 文档发生 update 时，在原事务内自然转换为新写法并删除该 source 的旧行。
4. 需要主动压缩时，通过应用服务逐文件执行“物化—重写—重新构建”，不执行裸 SQL。

新设计继续使用现有 `byqa-ref://<kid>` token，现有 resolver 和 materializer 已具备兼容
新写法所需的“只替换 token 主体”行为。滚动部署时，新旧实例都能读取新内容，因此不需要
token 版本开关或分阶段启用。

### 10.3 历史压缩任务

本期不实现自动后台压缩。后续如确有存量回收需求，任务必须：

1. 按 source 文件逐个处理并锁定；
2. 使用正式 materializer，不自行拼接 target；
3. 同时更新原文件、Markdown sidecar、chunk 和 retrieval projection，或使构建结果失效并
   重新构建；
4. 提交成功后才能删除旧关系行；
5. 支持断点、限速、失败重试和逐文件回滚。

## 11. 并发、幂等与一致性

### 11.1 并发保证

在不增加唯一索引的前提下，关系唯一性依赖现有 source 写串行化：

- import 通过文件路径创建约束避免同一 source 重复创建；
- update 的 `get_file_by_path_for_update` 锁定 source；
- 同一 rewrite 使用一个稳定 `producer_run_id`；
- 分组后每个 normalized target 只调用一次 repository。

所有 Markdown 写入必须经过 `MarkdownReferenceRewriter`；不得从 route 或其他 service
直接逐 occurrence 调用通用 assertion upsert。

### 11.2 事务一致性

关系行和 source 文件数据库状态继续在同一事务中提交。对象存储写入不具备数据库事务，
沿用现有补偿策略：

- import 写对象失败时回滚 source 和关系行；
- update 在 DB commit 前写对象失败时回滚，并恢复旧对象；
- 任何 relation insert 未返回 `kid` 时终止整个文件写入。

### 11.3 幂等

同一次 parser run 内，关系 fingerprint 和现有唯一索引使重复 upsert 返回同一记录。
update 重试会重新物化当前内容、删除该 source 旧出边并完整重建，最终关系集合由当前正文
唯一目标集合决定。

## 12. 性能与容量

设单个 Markdown 有 `N` 次有效引用，归一化后指向 `U` 个唯一目标，`U <= N`。

| 指标 | 当前 | 新设计 |
| --- | --- | --- |
| 目标文件查询 | `N` | `U` |
| 关系 upsert | `N` | `U` |
| Markdown 关系行 | `N` | `U` |
| resolver 查询行 | 最多 `N` | 最多 `U` |
| move/delete/restore 更新行 | 按 occurrence | 按 source-target |
| token 长度 | `byqa-ref://kid` | `byqa-ref://kid` 加 occurrence suffix |

新设计只把原先位于数据库 `target_suffix` 的 query/fragment 放回 Markdown，因此对象大小
增量等于各 occurrence suffix 长度之和，不存在 Base64 膨胀。关系、索引和查询规模从 `N`
下降到 `U`。

## 13. 安全与限制

1. resolver 仍必须校验关系行属于当前 `knowledge_base_id`，防止跨知识库 ID 猜测。
2. query/fragment 只作为 token 后的原始文本保留，不能覆盖数据库解析出的当前目标路径。
3. 关系行缺失时不得把 `byqa-ref://` 暴露给 read、download 或 search 调用方。
4. 自引用、目录引用、外部 URL、页内 anchor 和逃根路径继续沿用当前过滤规则。

## 14. 可观测性

实现输出以下日志字段：

```text
parsed_count
occurrence_count
relation_count
deduplicated_occurrence_count = occurrence_count - relation_count
resolved_relation_count
pending_relation_count
invalid_token_count
```

上线观察：

- 每次 import/update 的 `occurrence_count / relation_count`；
- `MARKDOWN_PARSER` 行增长速度；
- read/search/download 的 token 解析失败数；
- update 物化失败数；
- pending、broken 关系数量。

## 15. 测试设计

### 15.1 token 单元测试

- `byqa-ref://42`、`byqa-ref://42#intro`、
  `byqa-ref://42?download=1#intro` 都只提取关系 ID 42。
- resolver 替换 token 主体后，query/fragment 保持原位。
- 非法 ID 或找不到关系行时不泄漏 token。

### 15.2 rewriter 单元测试

- 同一 target 重复两次：一个 repository row、两个 token 使用同一 ID。
- `b.md`、`./b.md`、百分号编码路径归一到同一目标：一个 row。
- 同一目标使用不同 query/fragment：一个 row，各 occurrence 在 token 后保留自己的 suffix。
- 原始相对路径和外围空白不写入重写后的 Markdown。
- resolved、unresolved 分别按唯一目标写入。
- 目录、外部 URL、页内 anchor、逃根路径不登记。
- 新 Markdown 行的位置字段全部为 NULL。
- parsed/occurrence/relation/deduplicated 计数正确。

### 15.3 resolver/materializer 单元测试

- 新关系 resolved 时输出当前移动后路径加 Markdown 内联 suffix。
- 新关系 broken/unresolved 时输出关系级规范 KB path 加 Markdown 内联 suffix。
- 历史 token 继续读取 row 的 `original_target/target_suffix`。
- 同一 ID 的多个 query/fragment 独立保留。
- 同一批文本只按唯一 ID 查询 repository。
- 跨知识库 row 不参与替换。
- 用户可见输出不包含完整或半截 `byqa-ref://`。

### 15.4 分片单元测试

- 带 query/fragment 的 token 主体不被硬切分。
- 包含 token 和 suffix 的完整 Markdown link/image span 不被切分。
- search chunk 解析后不残留 `byqa-ref://`。

### 15.5 API 集成测试

- import：同一 Markdown 多次引用同一文件，数据库只有一个 Markdown 关系行。
- zip import：重复 md-to-md pending 引用只有一个关系行，目标上传后统一 resolved。
- update：旧 token 被物化，旧重复行删除，新内容按唯一目标登记。
- read/search/download：同一 ID 不同 query/fragment 输出正确。
- move：所有 occurrence 输出目标新路径。
- delete：所有 occurrence 回退关系级规范 KB path，并保留各自 suffix。
- restore：所有 occurrence 恢复为目标当前路径并保留各自 suffix。
- references：inbound/outbound 对 source-target 只返回一项。
- semanticRelations：Markdown assertionCount 不按 occurrence 增长，位置证据为空。
- 小窗口 readFile 和真实小 chunk 场景不泄漏 token。

### 15.6 兼容测试

- 同一文件同时包含“row suffix”历史 token 和“inline suffix”新 token 时可正确读取。
- 旧服务实例可读取 suffix 内联的新内容。
- 旧文档无需迁移即可读取。
- 文件第一次 update 后从旧多行转换为新单行关系。

## 16. 受影响文件

实施涉及：

- `src/by_qa/knowledge_common/markdown_reference.py`
  - 原则上无需修改；现有 token 正则已经只匹配关系 ID 主体，补充兼容测试即可。
- `src/by_qa/knowledge_base/services/markdown_reference_rewriter.py`
  - occurrence 分析、目标分组、关系级写入，以及生成 `token + suffix`。
- `src/by_qa/knowledge_base/services/markdown_reference_resolver.py`
  - 原则上无需修改；验证历史 row suffix 与新 inline suffix 兼容。
- `src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py`
  - 增加 `upsert_markdown_relation` 适配器和关系级 fingerprint 规则。
- `src/by_qa/knowledge_build/services/document_chunking_service.py`
  - 原则上无需修改；现有 Markdown 引用 span 已覆盖 token 和 suffix，补充测试即可。
- `src/by_qa/knowledge_base/api/schemas.py`
  - 仅更新 references 字段文档语义，不要求改变 JSON 结构。
- 对应 knowledge_common、knowledge_base、knowledge_build 单元测试和 API 集成测试。
- `docs/modules/api-integration-test-plan.md`
  - 更新稳定引用、references 和 semanticRelations 验收语义。
- KnowledgeEntity 设计文档
  - 将 Markdown 行从“位置断言”更新为“关系级断言”。

明确不新增或修改 `src/by_qa/knowledge_base/sql/*.sql`。

## 17. 实施顺序

1. 增加 repository 的 Markdown 关系级适配器。
2. 改造 rewriter 为 occurrence 分析、唯一目标分组和 suffix 内联。
3. 验证 resolver、materializer 和 chunk span 对新写法的兼容性。
4. 更新 references/semanticRelations 文档语义与日志指标。
5. 补齐单元和 API 集成测试。
6. 上线后观察 token 错误率和关系压缩率。
7. 根据存量规模另行决定是否建设历史压缩任务。

## 18. 验收标准

1. 一个 Markdown 中同一目标出现 `N` 次，`knowledge_file_reference` 只新增一条
   `MARKDOWN_PARSER` 关系。
2. 不同源文件引用同一目标时，每个源文件各有一条关系。
3. 新 Markdown 关系的位置字段全部为 NULL。
4. 同一关系的不同 query、fragment 在 read/download/search 中分别正确输出。
5. 不承诺保留同一目标的不同原始相对路径、URL 编码形式或外围空白。
6. move 后输出目标新路径；delete 后回退关系级规范 KB path；restore 后恢复当前路径；
   三个阶段均保留各 occurrence 的 query/fragment。
7. references 对 source-target 去重，且不再声称返回逐 occurrence 数据。
8. 内部 token 不泄漏到用户可见响应。
9. 历史数据无需 SQL 迁移即可继续读取。
10. update 可以把旧逐 occurrence 记录自然压缩为关系级记录。
11. Discovery/Enrich 的写入、位置证据和查询行为不受影响。
12. 不新增、不删除、不修改任何数据库表、列、约束或索引。
