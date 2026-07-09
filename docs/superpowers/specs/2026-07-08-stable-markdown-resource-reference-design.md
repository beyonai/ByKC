# 稳定 Markdown 资源引用与移动接口适配 - 设计文档

日期：2026-07-08
状态：草案，待评审

## 背景

现有 `/api/v1/knowledgeItems/import` 在上传 markdown 文件时，会把 `![]()` 和 `[]()` 中能解析到知识库文件的相对地址改写为知识库绝对虚拟路径，例如 `/docs/images/a.png`。这解决了导入时的相对路径问题，但路径本身是可变的：后续如果新增移动目录/文件接口，只更新 `knowledge_fs_entry.virtual_path` 会导致已存 markdown sidecar 和已构建 chunk 中的旧路径失效。

目标是在**移动/删除/恢复时不修改已存 markdown 内容、不修改已构建 chunk、不重建索引**的前提下，让 markdown 内部资源引用在文件或目录移动、重命名、删除、恢复上传后仍可持续解析。构建阶段允许增加最小的 token 原子边界保护，避免内部引用 token 被分片切断。

## 目标

1. 新增稳定引用机制，避免 markdown 引用直接绑定可变路径。
2. 支持目标文件晚于源 markdown 上传：`a.md` 引用 `b.md`，即使 `b.md` 上传时尚不存在，也能在 `b.md` 上传后快速反查并绑定。
3. 移动文件、批量文件、目录子树时，不修改 markdown sidecar、不修改已构建 chunk，引用输出仍指向目标文件当前路径。
4. 所有面向用户和 QA 的读输出都不暴露内部引用 token。
5. 删除、恢复/重新上传、反向引用查询等增强能力纳入本次设计。

## 非目标

- 不改变 `knowledge_build` 的分片策略和 embedding 生成逻辑；仅允许增加 markdown 引用 span 与 `byqa-ref://<id>` token 的原子边界保护。
- 不要求移动时同步改写 object 内容。
- 不把 `knowledge_build` 反向耦合到 `knowledge_base`；引用解析属于 `knowledge_base` 读写编排。
- 不为外部 URL、`mailto:`、`data:`、页内 `#anchor` 建引用关系。
- 不考虑旧数据迁移；历史 markdown/chunk 中已经保存的旧绝对路径不在本次方案内修复。

## 核心决策

### 1. 使用引用记录 ID，而不是目标文件 ID

markdown 内部写入稳定 token：

```markdown
![diagram](byqa-ref://12345)
[spec](byqa-ref://12346)
```

`12345` 是引用关系表的主键，不是目标文件 ID。这样即使目标文件上传时尚不存在，也能先创建 unresolved 引用；后续目标文件上传后只更新引用记录的 `target_fs_entry_id`，markdown 和 chunk 中的 token 不需要变化。

### 2. 读时解析为当前路径

`byqa-ref://12345` 在输出阶段解析为目标文件当前 `virtual_path`：

```markdown
![diagram](/new/path/diagram.png)
```

如果引用尚未解析，则按策略输出原始 target；如果目标已删除，则面向用户和 QA 的默认输出也回退到原始 target，避免产生指向已删除文件的死链。管理/调试视图可以展示最后已知 `target_path` 和 broken 状态。任何情况下都不把内部 token 暴露给用户。

### 3. Resolved 路径只通过目标文件 ID 查询

引用处于 `resolved` 状态时，输出路径只通过 `target_fs_entry_id` join 当前 `knowledge_fs_entry.virtual_path` 得到，不读取引用表中的 `target_path`。这样移动目标文件时不需要同步维护引用表路径，避免双写一致性风险。

`target_path` 只在 `target_fs_entry_id IS NULL` 时作为 pending key 使用：

- `unresolved`：目标还没上传，`target_path` 是导入时解析出的待匹配路径。
- `broken`：目标被删除，删除动作会先读取目标删除前路径写入 `target_path`，再清空 `target_fs_entry_id`。

### 4. Unresolved 路径固定为导入时待匹配路径

unresolved 引用的 `target_path` 不随源 markdown 移动重算。它表示导入时根据当时源目录和 `original_target` 归一化出的待匹配路径。移动源 markdown 只改变源文件自身位置，不会让未解析引用悄悄等待另一个路径；如果调用方希望改变未解析引用的目标路径，需要重新上传/重建源 markdown，或未来提供显式 relink 能力。

## 数据模型

新增 SQL migration，例如 `knowledge_file_reference`：

```sql
CREATE TABLE IF NOT EXISTS knowledge_file_reference (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid),
    source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid),
    target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid),
    original_target text NOT NULL,
    target_path text NULL,
    target_suffix text NOT NULL DEFAULT '',
    target_kind text NOT NULL DEFAULT 'FILE',
    status text NOT NULL,
    last_resolved_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
```

`status` 取值：

- `resolved`：已绑定到非删除目标文件。
- `unresolved`：按路径解析出的目标尚不存在。
- `broken`：曾经 resolved，但目标被删除或不可见。
- `external` 不入表；外链保持原样。

关键索引：

```sql
CREATE INDEX IF NOT EXISTS idx_kfr_source
ON knowledge_file_reference(knowledge_base_id, source_fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_kfr_pending_path
ON knowledge_file_reference(knowledge_base_id, target_path)
WHERE status IN ('unresolved', 'broken') AND target_path IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_kfr_target
ON knowledge_file_reference(knowledge_base_id, target_fs_entry_id);
```

可选约束：

```sql
ALTER TABLE knowledge_file_reference
ADD CONSTRAINT chk_kfr_status
CHECK (status IN ('resolved', 'unresolved', 'broken'));

ALTER TABLE knowledge_file_reference
ADD CONSTRAINT chk_kfr_target_kind
CHECK (target_kind IN ('FILE'));
```

字段语义：

- `source_fs_entry_id`：引用所在 markdown 文件 ID。源文件当前路径通过 `knowledge_fs_entry` 查询，不在引用表做导入时路径快照。
- `target_fs_entry_id`：目标文件 ID。存在时以它为准 join `knowledge_fs_entry` 解析当前路径。
- `original_target`：用户原始写法，用于 unresolved/broken 时回退输出；一旦引用 resolved，读时优先输出目标当前路径。
- `target_path`：pending 匹配路径，仅在 `target_fs_entry_id IS NULL` 时有意义。unresolved 时是导入时按源文件所在目录和 `original_target` 算出的待匹配路径；broken 时是目标删除动作写入的删除前最后路径。resolved 时可以为空，读路径不依赖它。
- `target_suffix`：引用中的 query/fragment，例如 `#intro` 或 `?x=1`，存在性匹配只看 `target_path`，输出时拼回。
- `target_kind`：目标类型，首版只支持大写枚举 `FILE`。目录链接不建引用关系，保持原始 markdown target。

## 组件改造

### 1. `services/markdown_reference_rewriter.py`

当前职责是“目标存在则把 markdown 引用改为 KB 绝对路径”。改造后职责变为“登记引用关系并写入内部 token”。

输入：

- markdown 文本
- 源文件所在目录
- `knowledge_base_id`
- `source_fs_entry_id`
- 引用仓储

流程：

1. 调用 `knowledge_common.markdown_reference.detect_reference_spans` 检测 markdown 引用。
2. 跳过外链、空 target、页内 anchor。
3. 使用 `knowledge_common.kb_path_utils.normalize_kb_path` 得到 `target_path`。
4. 仅查询目标文件是否存在；目录目标首版不登记引用，保持原始 target。
5. 创建引用记录：
   - 存在：`target_fs_entry_id=<目标ID>`，`target_path=NULL`，`status='resolved'`
   - 不存在：`target_fs_entry_id=NULL`，`target_path=<待匹配路径>`，`status='unresolved'`
6. 将原 span 的 target 替换为 `byqa-ref://<reference_id>`，保留 alt/text。

单个 markdown 内多个相同 target 可以创建多条引用记录，便于保留每个 span 的原始 target 和 suffix；如果后续想去重，可在 resolver 层批量解析相同 ref id。

改造位置必须从 route 层下沉到 ingestion 事务内：新 rewriter 需要 `source_fs_entry_id` 并写入引用表，不能继续作为 route 层纯函数运行。

### 2. 新增 `repositories/knowledge_file_reference_repository.py`

职责：

- `create_reference(...) -> int`
- `list_by_source(source_fs_entry_id) -> list[dict]`
- `list_by_reference_ids(reference_ids) -> list[dict]`
- `resolve_pending_for_path(knowledge_base_id, target_path, target_fs_entry_id)`
- `mark_target_deleted(target_fs_entry_id)`
- `mark_target_restored(knowledge_base_id, target_path, target_fs_entry_id)`
- `list_sources_by_target(target_fs_entry_id)`

仓储只做 SQL，不做 markdown 字符串替换。

### 3. 新增 `services/markdown_reference_resolver.py`

职责：把输出文本中的 `byqa-ref://<id>` 批量解析为用户可见路径。

解析策略：

1. 扫描文本中的 ref ids。
2. 一次性查询引用表和目标 `knowledge_fs_entry`。
3. 对 `resolved` 且目标未删除的引用，输出目标当前 `virtual_path + target_suffix`。
4. 对 `unresolved`，输出 `original_target`。
5. 对 `broken`，面向用户和 QA 默认输出 `original_target`；管理/调试视图可展示 `target_path + target_suffix` 和 broken 状态。

Resolver 必须支持批量文本：

```python
async def resolve_texts(
    self,
    *,
    knowledge_base_id: int,
    texts: list[str],
) -> list[str]
```

搜索 topK chunk、批量 read 片段、下载 markdown 都应走批量接口，避免 N+1 查询。

### 4. `services/knowledge_item_ingestion_service.py`

`upload_file()` 现在返回 `None`，需要改成返回新建或更新后的 file row，至少包含：

```python
{
    "fs_entry_id": int,
    "knowledge_base_id": int,
    "virtual_path": "/docs/a.md",
    "mime_type": "text/markdown"
}
```

上传流程调整：

1. 先创建 `knowledge_fs_entry`，得到 `source_fs_entry_id`。
2. 如果是 markdown，在写 storage 前调用新 rewriter，生成含 `byqa-ref://...` 的 content。
3. 写原始文件 storage。
4. 应用 front matter metadata。
5. 提交事务。
6. 对任意新上传文件，触发 unresolved 引用补偿：
   - 根据新文件当前路径反查 `target_path`
   - 批量更新 matching rows 的 `target_fs_entry_id`、清空 `target_path`、设置 `status='resolved'`

补偿可以放在同一个事务内，保证上传完成即解析可见。按路径重绑是明确取舍：知识库内同路径重新上传视为替换/恢复同一逻辑资源；如果业务需要区分“同路径不同内容”，后续再增加人工 relink 或内容指纹确认。删除与上传并发时，同一 KB 路径的文件创建/删除应沿用现有 fs_entry 路径锁或事务锁，保证引用状态不会在两个文件 ID 间抖动。

调用方影响：

- `api/routes.py` 单文件 markdown 分支不再直接调用 rewriter，只构造上传请求。
- `zip_batch_import_service.py` 不再在服务外重写 markdown；批量导入仍负责非 markdown 先上传、markdown 后上传，并在批次结束后触发 pending 补偿。
- 测试中的 fake ingestion service 需要接受 `upload_file()` 返回 file row 的新契约。
- route 层成功响应仍使用上传请求里的 `filePath`，不依赖 `upload_file()` 返回值。

### 5. `services/zip_batch_import_service.py`

zip 场景必须处理导入顺序问题。

推荐流程：

1. 解压并使用同一套 `split_target` / `normalize_kb_path` 逻辑归一化所有条目路径。
2. 先上传非 markdown 资源。
3. 再上传 markdown，并在 rewriter 中：
   - 查询已经存在的 KB 文件
   - 同时参考本批次文件路径映射
4. 批量结束后，对本批所有成功上传文件统一执行 pending 补偿。

这样 `a.md` 引用 zip 内后上传的 `b.md` 也能被绑定。如果 `b.md` 上传失败，引用保持 `unresolved`，读时输出原始 target。

### 6. `services/knowledge_base_service.py`

#### `read_file`

当前直接读取 markdown sidecar 并返回 `data`。改造为：

1. 读取 sidecar。
2. 如果请求带 `startLine/endLine`，先按原始 sidecar 切行。
3. 对切出的文本执行 resolver。
4. 返回解析后的 `data`。

先切行再解析，保证行号语义与 chunk 构建时保存的 sidecar 内容一致。

#### `download_file`

现有下载读取上传后存储的文件。markdown 上传时存储内容已经被 token 化，系统不再保留用户最初上传的原始 markdown 字节；因此面向用户下载 markdown 时默认先 resolver 成可读路径。若未来增加“内部内容下载”，它只能返回 token 化后的存储内容，不能还原成用户最初上传文本。

非 markdown 文件不做处理。

#### 删除文件/目录

引用表不做软删除。文件删除动作是引用状态变化的触发点，引用查询通过 `knowledge_fs_entry.is_deleted` 判断源文件和目标文件当前是否可见。

删除目标文件时：

- 以目标为 `target_fs_entry_id` 的引用标记为 `broken`。
- 删除前先读取目标当前 `virtual_path` 写入 `target_path`。
- 清空 `target_fs_entry_id`，因为 resolved 路径只通过非空目标 ID 查询。
- 如果未来同路径重新上传文件，按 `target_path` 把 `broken/unresolved` 引用重新绑定到新文件 ID，状态改为 `resolved`。

删除源 markdown 时：

- 引用记录保持不变，不额外标记删除。
- 反向引用查询默认 join 源 `knowledge_fs_entry` 并过滤 `source.is_deleted = FALSE`；管理视图可选择展示已删除源文件的历史引用。

#### 移动文件/目录

新增移动接口只更新 fs entry 树与必要的 storage locator。resolved 引用不更新引用表路径，resolver 通过 `target_fs_entry_id` join 当前 `knowledge_fs_entry.virtual_path` 自然得到新路径。

如果移动源 markdown，自身作为 source 的 unresolved 引用不重算 `target_path`。unresolved 的 `target_path` 是导入时待匹配路径，不随源文件移动变化。

### 7. `services/knowledge_item_search_service.py`

搜索返回的 `chunk_text` 来自 `knowledge_chunk_retrieval_mv`。由于 chunk 内会保存 `byqa-ref://...`，必须在构造 `SearchHit` 前批量 resolver。

流程：

1. merge text/vector hits。
2. 对 top items 的 `chunk_text` 按 KB 分组。
3. 对每组批量 resolver。
4. 构造 `SearchHit(chunk_text=<resolved_text>)`。

搜索结果的 `file_path` 必须和 resolver 输出同源。推荐调整搜索 repository：候选 chunk 仍来自 `knowledge_chunk_retrieval_mv`，但最终 SELECT join 当前 `knowledge_fs_entry fe`，用 `fe.virtual_path` 作为 `file_path`，避免目录子树移动时全量 refresh retrieval projection。若保留 projection 的 `full_path` 字段，只能作为过滤/排序辅助，不作为对外返回路径。

### 8. `knowledge_build` token 原子保护

构建仍然保存 token 化后的 markdown/chunk，但分片时必须保证内部 token 不被切开。

规则：

- `knowledge_common.markdown_reference.detect_reference_spans` 继续保护完整 markdown `![]()` / `[]()` span。
- 额外检测裸 `byqa-ref://<id>` token span。
- `DocumentChunkingService` 的 markdown 分片边界不得落在上述 span 内部。
- 如果单个 span 超过 chunk size，则作为独立 chunk 输出并记录 warning。

resolver 不负责修复半截 token；半截 token 说明构建阶段违反原子边界约束。

### 9. `api/routes.py` 与 schemas

新增移动接口建议：

```text
POST /api/v1/knowledgeItems/move
```

请求：

```json
{
  "knCode": "1",
  "items": [
    {"sourcePath": "/docs/a.md", "targetPath": "/archive/a.md"},
    {"sourcePath": "/docs/images", "targetPath": "/archive/images"}
  ],
  "overwrite": false
}
```

响应：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {"sourcePath": "/docs/a.md", "targetPath": "/archive/a.md", "success": true, "error": null}
    ],
    "summary": {"total": 1, "succeeded": 1, "failed": 0}
  }
}
```

规则：

- 支持文件、目录、批量文件。
- 同一批次内禁止 source/target 互相包含导致循环移动。
- `targetPath` 父目录不存在时可自动创建，沿用现有 create file entry 的父目录创建能力。
- 默认不覆盖已有文件或目录；`overwrite=true` 可作为后续增强，不建议首版打开。

新增反向引用查询接口：

```text
POST /api/v1/knowledgeItems/references
```

请求：

```json
{"knCode": "1", "filePath": "/docs/b.md", "direction": "inbound"}
```

返回哪些 markdown 引用了该文件，包含 source path、original target、status。

## 读写时序

### `a.md` 先上传，`b.md` 后上传

```text
upload a.md
  -> detect [b](b.md)
  -> target_path=/docs/b.md
  -> b.md 不存在
  -> insert reference(status=unresolved)
  -> markdown 写入 [b](byqa-ref://101)

upload b.md
  -> create fs_entry(id=20, virtual_path=/docs/b.md)
  -> update references
       where target_path=/docs/b.md
       set target_fs_entry_id=20, target_path=NULL, status=resolved

read a.md
  -> byqa-ref://101 解析为 /docs/b.md
```

### 移动 `b.md`

```text
move /docs/b.md -> /archive/b.md
  -> update knowledge_fs_entry(id=20).virtual_path=/archive/b.md
  -> reference 101 不变

read a.md
  -> byqa-ref://101 解析为 /archive/b.md
```

### 删除并重新上传 `b.md`

```text
delete /archive/b.md
  -> reference 101 target_path=/archive/b.md
  -> reference 101 target_fs_entry_id=NULL, status=broken

read a.md
  -> byqa-ref://101 输出 original_target=b.md

upload /archive/b.md
  -> exact path compensation 命中 target_path=/archive/b.md
  -> reference 101 target_fs_entry_id=<new_id>, target_path=NULL, status=resolved
```

## 测试计划

单元测试：

- rewriter：存在目标写 resolved token。
- rewriter：缺失目标写 unresolved token。
- rewriter：外链、anchor、逃出 KB 根保持原样。
- resolver：resolved 输出当前路径。
- resolver：移动后输出新路径。
- resolver：unresolved/broken 默认输出 original target，管理视图可展示 broken 的 target_path。
- repository：按 source、target、pending target path 查询和更新。
- move service：文件移动、目录子树移动、批量移动、冲突校验、循环移动校验。
- chunking：`byqa-ref://<id>` token 不会被切到两个 chunk 中；超长 token/span 独立成 chunk。
- normalization：导入、zip、补偿、移动路径判断共用同一套 `split_target` / `normalize_kb_path`，覆盖 URL decode、query/fragment、trailing slash、`.` / `..`。

集成测试：

- 上传 `a.md` 引用已存在 `b.md`，`readFile(a.md)` 输出当前 `/.../b.md`。
- 上传 `a.md` 时 `b.md` 不存在，`readFile(a.md)` 输出原始 `b.md`；上传 `b.md` 后输出 `/.../b.md`。
- 构建 `a.md` 后移动 `b.md`，搜索结果 `chunkText` 输出移动后的路径。
- zip 内 `a.md` 引用 `b.md`，导入结束后引用 resolved。
- 删除 `b.md` 后 `readFile/search` 不暴露内部 token，默认回退 `original_target`。
- 重新上传最后已知路径的 `b.md` 后引用恢复 resolved。
- 移动目录子树后，多个引用目标都输出新路径。
- 反向引用接口返回引用 `b.md` 的源 markdown 列表。

## 受影响文件

- `src/by_qa/knowledge_base/sql/0xx_knowledge_file_reference.sql`
- `src/by_qa/knowledge_base/repositories/knowledge_file_reference_repository.py`
- `src/by_qa/knowledge_base/services/markdown_reference_rewriter.py`
- `src/by_qa/knowledge_base/services/markdown_reference_resolver.py`
- `src/by_qa/knowledge_base/services/knowledge_item_ingestion_service.py`
- `src/by_qa/knowledge_base/services/zip_batch_import_service.py`
- `src/by_qa/knowledge_base/services/knowledge_base_service.py`
- `src/by_qa/knowledge_base/services/knowledge_item_search_service.py`
- `src/by_qa/knowledge_base/api/routes.py`
- `src/by_qa/knowledge_base/api/schemas.py`
- `src/by_qa/knowledge_base/infrastructure/runtime.py`
- `src/by_qa/knowledge_base/repositories/knowledge_item_search_repository.py`
- `src/by_qa/knowledge_build/services/document_chunking_service.py`
- `src/by_qa/knowledge_common/markdown_reference.py`
- `tests/knowledge_base/unit/`
- `tests/knowledge_base/integration/`
- `tests/knowledge_build/unit/`

QA 模块原则上不直接改。如果 QA 只通过 `search` 和 `readFile` 工具获取内容，则 resolver 在 knowledge API 层即可覆盖。如果存在绕过 API 直接读取 chunk 的路径，需要补充 resolver 或改为走 knowledge API。

## 风险与缓解

- **读时解析性能**：批量收集 ref ids，一次查询引用表和目标 fs entry；按 KB 分组处理搜索结果。
- **内部 token 泄漏**：所有用户读出口都接 resolver；构建分片保护 `byqa-ref://<id>` token 不被切断；测试覆盖 `readFile`、download、search 和 chunk 边界。
- **移动后 search filePath 仍旧**：搜索返回路径必须 join 当前 `knowledge_fs_entry.virtual_path`，避免依赖 projection 中可能过期的 `full_path`。
- **删除恢复语义歧义**：同路径重新上传被视为恢复/替换同一逻辑资源；删除时清空 `target_fs_entry_id` 并写入最后 `target_path`，恢复时按 `target_path` 精确补偿。并发删除/上传依赖同一 KB 路径的事务锁保证顺序。
- **目录链接范围**：首版只登记文件目标；目录链接保持原样，避免隐式目录创建无法触发补偿的问题。
- **归一化不一致**：导入、zip、补偿、移动校验必须共用同一套路径拆分和归一化函数。
- **事务复杂度上升**：引用登记与 markdown 写入同事务；上传后补偿保持精确路径更新，后续可异步重扫。

## 实施顺序

1. 新增 SQL 表和 repository。
2. 改造 rewriter 写 `byqa-ref://<id>`，并完成单元测试。
3. 新增 resolver，并接入 `readFile`。
4. 接入上传后 pending 补偿。
5. 接入 `search` 的 `chunkText` resolver。
6. 接入 zip 导入批量补偿。
7. 增加 `knowledge_build` token 原子边界保护。
8. 实现移动接口，并让 search 返回路径 join 当前 fs entry。
9. 实现删除/重新上传引用状态流转。
10. 实现反向引用查询接口。
