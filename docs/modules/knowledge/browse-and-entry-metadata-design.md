# 知识条目浏览、分页与元数据能力扩展设计

## 1. 背景

当前知识库已经以 `knowledge_fs_entry` 统一保存文件和目录，并具备文件大小、更新时间、文件构建任务和自定义元数据等基础数据。现有接口仍存在以下能力缺口：

- `listDir`、`glob` 仅返回路径、类型和大小，未返回更新时间与构建情况。
- `listDir`、`glob` 不支持按 `metadataFieldList` 召回元数据。
- `listDir`、`glob` 不支持分页；旧调用方依赖一次返回全部结果。
- 文件导入、文件修改不能通过独立的 `metadata` 参数写入元数据，只能依赖 Markdown YAML front matter 或后续元数据更新接口。
- 目录不能在创建、修改时写入元数据，现有元数据查询、更新和检索服务也显式限制为文件。
- 相关接口文档没有覆盖上述行为。

本设计在保持旧请求兼容的前提下，统一文件和目录的元数据处理能力，并将实现拆分为可独立提交和独立集成验证的功能步骤。

## 2. 目标

1. `listDir`、`glob` 返回更新时间、文件大小和最新构建情况。
2. `listDir`、`glob` 参考 search 接口，通过 `metadataFieldList` 按需召回元数据，元数据统一放在 `metadata` 对象中。
3. `listDir`、`glob` 支持可选分页；未传分页参数时保持原有全量返回逻辑。
4. 浏览结果按目录优先、名称升序的规则稳定排序。
5. 文件导入和文件修改支持可选 `metadata`，并与 Markdown YAML front matter 合并；冲突时 front matter 优先。
6. 目录创建、修改和删除具备与文件一致的元数据生命周期，现有元数据相关接口支持目录。
7. 更新接口文档和集成测试计划。

## 3. 非目标

- 不重构现有 glob 路径匹配语法，不增加 `**` 多层匹配。
- 不重命名现有 `knowledge_file_metadata_value` 数据表。
- 不改变文件构建状态机和 `fileBuildStatus` 的状态定义。
- 不在本次改动中重构整个虚拟文件系统或目录移动机制。
- 不自动将目录送入正文、向量或混合检索流程。

## 4. 当前实现与数据条件

### 4.1 文件与目录

文件和目录均保存在 `knowledge_fs_entry` 中，通过 `entry_type` 区分。该表已经包含：

- `file_size`
- `updated_at`
- `mime_type`
- `checksum`
- `virtual_path`

因此浏览接口补充大小和更新时间不需要数据库迁移。

### 4.2 构建状态

构建状态保存在 `knowledge_build_task` 中，并通过 `fs_entry_id` 关联文件。一个文件可能存在多条历史任务，浏览接口应返回按 `created_at DESC, kid DESC` 排序后的最新任务状态。

### 4.3 自定义元数据

自定义元数据保存在 `knowledge_file_metadata_value` 中。该表通过 `fs_entry_id` 外键关联 `knowledge_fs_entry`，数据库约束没有把目标限制为文件，因此目录元数据不需要新增表或新增列。

现有服务和 SQL 中的“file metadata”命名可以暂时保留，避免引入与功能无关的表迁移和大范围重命名；新增的业务层能力应使用中性的 entry/resource 命名。

## 5. listDir 与 glob 接口设计

### 5.1 请求参数

`listDir` 请求：

```json
{
  "knCode": "1",
  "directoryPath": "/制度",
  "metadataFieldList": ["owner", "status"],
  "pageNum": 1,
  "pageSize": 20
}
```

`glob` 请求：

```json
{
  "knCode": "1",
  "pathRule": "/制度/*/*.md",
  "metadataFieldList": ["owner", "status"],
  "pageNum": 1,
  "pageSize": 20
}
```

新增字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段，语义参考 search |
| `pageNum` | integer | 否 | 页码，从 1 开始 |
| `pageSize` | integer | 否 | 每页条数，范围为 1 到 10000 |

### 5.2 分页兼容规则

| `pageNum` | `pageSize` | 行为 |
| --- | --- | --- |
| 未传 | 未传 | 保持旧逻辑，返回全部匹配结果 |
| 未传 | 已传 | 使用 `pageNum=1` 分页 |
| 已传 | 已传 | 按指定页码和每页条数分页 |
| 已传 | 未传 | 请求校验失败，避免无法确定每页条数 |

未分页请求保持原有 `resultObject` 结构：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": []
  }
}
```

分页请求增加分页信息：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [],
    "total": 128,
    "pageNum": 1,
    "pageSize": 20
  }
}
```

`total` 表示应用路径规则之后、分页之前的匹配总数。请求超出最后一页时返回空 `data`，同时保留正确的 `total`、`pageNum` 和 `pageSize`。

### 5.3 排序规则

分页前必须对完整结果应用统一稳定排序：

1. `directory` 在前，`file` 在后；
2. 按响应字段 `name` 不区分大小写升序；
3. 忽略大小写后名称相同时，按原始 `name` 升序；
4. 名称仍相同时，按 `fs_entry_id` 升序。

`listDir` 的 `name` 是当前目录下子项的完整知识库路径；`glob` 的 `name` 是最终匹配项的完整知识库路径。两者均按最终响应路径排序。

不能在 glob 的分层遍历过程中提前截断分页结果。必须先完成匹配、统一排序，再计算 `total` 和分页切片，否则不同父目录下的匹配顺序不稳定。

### 5.4 返回条目

```json
{
  "knCode": "1",
  "name": "/制度/请假.md",
  "type": "file",
  "size": 245760,
  "updatedAt": "2026-08-30T10:20:30+08:00",
  "buildStatus": "complete",
  "buildCurrentStep": "complete",
  "metadata": {
    "owner": {
      "valueType": "string",
      "value": "Alice"
    },
    "status": {
      "valueType": "string",
      "value": "active"
    }
  }
}
```

字段定义：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `knCode` | string | 知识库编码 |
| `name` | string | 知识库内完整路径 |
| `type` | string | `file` 或 `directory` |
| `size` | integer | 文件字节数；目录固定为 0 |
| `updatedAt` | string | `knowledge_fs_entry.updated_at` 的 ISO 8601 表示 |
| `buildStatus` | string \| null | 最新构建任务状态 |
| `buildCurrentStep` | string \| null | 最新构建任务当前步骤 |
| `metadata` | object | 按需召回的元数据；始终返回对象 |

目录以及从未创建构建任务的文件，`buildStatus` 和 `buildCurrentStep` 均返回 `null`。

### 5.5 metadataFieldList 语义

行为参考现有 search 接口，但 `metadata` 字段始终返回对象：

- 未指定 `metadataFieldList`：不执行元数据查询，返回 `"metadata": {}`。
- 指定空数组：不执行元数据查询，返回 `"metadata": {}`。
- 指定字段：只返回实际存在且位于列表中的字段。
- 指定字段均不存在：返回 `"metadata": {}`。
- 支持现有自定义元数据和系统元数据，例如 `updatedAt`、`fileSize`、`filePath`。
- 即使 `metadataFieldList` 包含 `updatedAt`，顶层固定字段 `updatedAt` 仍然保留；嵌套值使用元数据标准结构。

示例：

```json
{
  "updatedAt": "2026-08-30T10:20:30+08:00",
  "metadata": {
    "updatedAt": {
      "valueType": "datetime",
      "value": "2026-08-30T10:20:30+08:00"
    }
  }
}
```

元数据必须在分页完成后，仅针对当前页的 `fs_entry_id` 批量查询和回填。未分页请求则针对全部结果批量回填。不得逐条查询元数据。

### 5.6 构建状态查询

构建状态同样必须批量查询。推荐在仓储层增加按 `fs_entry_ids` 获取最新任务的方法，使用窗口函数或先聚合最新任务 ID，再一次性返回：

- `fs_entry_id`
- `status`
- `current_step`

仅文件需要构建状态；目录不参与构建查询。

## 6. 文件 metadata 参数设计

### 6.1 文件导入

`POST /api/v1/knowledgeItems/import` 增加可选 multipart 字段 `metadata`。该字段是 JSON object 字符串：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/import \
  -F "knCode=1" \
  -F "filePath=/制度/请假.md" \
  -F 'metadata={"owner":"Alice","tags":["hr","policy"]}' \
  -F "fileContent=@./请假.md" \
  -F "processFrontMatter=true"
```

路由层负责把 JSON 字符串解析为 object；非法 JSON、非 object 顶层值或不支持的值类型按请求校验失败处理。

### 6.2 文件修改

`POST /api/v1/knowledgeItems/update` 同样增加可选 multipart 字段 `metadata`：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/update \
  -F "knCode=1" \
  -F "filePath=/制度/请假.md" \
  -F 'metadata={"owner":"Bob","status":"review"}' \
  -F "fileContent=@./请假-v2.md"
```

### 6.3 合并与更新语义

有效元数据按以下顺序生成：

```text
请求 metadata
    ↓ 被同名字段覆盖
Markdown YAML front matter
    ↓
本次需要 upsert 的有效元数据
```

规则：

- 请求 metadata 和 front matter 无冲突时取并集。
- 同名冲突时，以文件 YAML front matter 为准。
- `processFrontMatter=false` 时不解析 front matter，但仍处理请求 metadata。
- 非 Markdown 文件忽略 front matter，仅处理请求 metadata。
- 导入路径中本次自动创建的父目录仅写入请求 metadata；不合并文件 front matter，也不修改已存在父目录。
- 修改文件时，只 upsert 本次有效元数据中的字段；未出现的既有元数据继续保留。
- 元数据转换复用现有 front matter 类型推断与标准化规则。
- 文件记录、文件派生数据清理和元数据写入处于同一数据库事务。

zip 导入时，请求 metadata 是每个真实文件条目的公共基础元数据；每个 Markdown 文件自己的 front matter 可以覆盖对应同名字段。

## 7. 目录元数据设计

### 7.1 目录创建

`POST /api/v1/directories/create` 增加可选 object 字段 `metadata`：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事",
  "directoryDescription": "人事制度",
  "metadata": {
    "owner": "HR",
    "tags": ["policy", "internal"]
  }
}
```

当完整路径包含需要递归创建的中间目录时，请求 metadata 同时应用于 `directoryPath` 指定的最终目录和本次自动创建的中间目录。已存在的父目录不会被修改。

目标目录已存在时保持现有幂等成功语义，并对传入 metadata 执行幂等 upsert，使请求重试能得到一致结果。

### 7.2 目录修改

`POST /api/v1/directories/update` 增加可选 object 字段 `metadata`：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事",
  "directoryName": "人力资源",
  "metadata": {
    "owner": "People Team"
  }
}
```

目录重命名和 metadata upsert 在同一事务中执行。未传 metadata 时保留全部既有元数据；传入时只 upsert 本次字段。

### 7.3 移动自动创建目标目录

`POST /api/v1/knowledgeItems/move` 增加可选 object 字段 `metadata`。该字段只应用于本次移动中递归自动创建的目标目录：

- `targetDirectoryPath` 不存在时，本次新建的每层目录都写入请求 metadata。
- `targetFilePath` 的父目录不存在时，本次新建的每层父目录都写入请求 metadata。
- 已存在的目标目录不执行 metadata upsert。
- 被移动的源文件、源目录及目录子树保留原元数据，不合并请求 metadata，也不解析文件 YAML front matter。
- 目标目录创建、metadata 写入和单个源条目移动在同一事务中提交或回滚。

### 7.4 目录删除

目录删除继续使用子树软删除语义：

- 软删除目标目录及全部后代 `knowledge_fs_entry`。
- 软删除目标目录及全部后代文件、目录的元数据值。
- 删除后 metadata get/update、listDir、glob、metadataSearch 均不能再访问这些元数据。

现有目录删除流程已经对子树 `fs_entry_ids` 批量软删除元数据，本次主要补充集成测试和跨接口一致性验证。

## 8. 元数据相关接口泛化

### 8.1 metadata/get 与 metadata/update

现有接口路径保持不变：

- `POST /api/v1/knowledgeItems/metadata/get`
- `POST /api/v1/knowledgeItems/metadata/update`

为兼容旧调用方，请求字段 `filePath` 暂时保持不变，但其语义扩展为“文件或目录的知识库内完整路径”。服务内部通过通用 fs-entry 路径解析获取资源，不再强制 `entry_type='FILE'`。

错误信息应从 `file not found` 调整为中性的 `entry not found`。如果需要严格保持错误文案兼容，可以只在目录路径场景使用 `directory not found`，文件场景继续返回原文案。

元数据批量操作的 `set`、`unset`、`append`、`remove`、`clear`、只读系统字段和事务原子性规则对文件与目录完全一致。

### 8.2 metadataSearch

metadataSearch 的请求契约保持不变。入参不区分文件或目录，本次不向请求模型、请求 JSON 或 DSL 增加任何资源类型字段：

- 不增加 `resourceType`、`entryType`、`isDirectory` 等请求参数。
- 不要求调用方在 `where` 中声明要查询文件还是目录。
- 每次查询都在满足现有知识库范围和 `where` 条件的全部有效文件、目录中执行。
- 文件和目录共用完全相同的请求结构和查询入口。

本次对 metadataSearch 契约的唯一类型相关变更是：在每条响应结果中增加 `type` 字段，用于区分命中项是文件还是目录。该字段不得出现在请求中：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事",
  "type": "directory",
  "metadata": {
    "owner": {
      "valueType": "string",
      "value": "HR"
    }
  }
}
```

`type` 是纯出参字段，允许值为 `file` 和 `directory`。调用方根据响应项的 `type` 判断该命中项是文件还是目录，不能通过本接口的入参预先限定资源类型。为兼容现有响应，路径字段继续使用 `filePath`，但其语义扩展为“文件或目录的知识库内完整路径”。

目录系统元数据的语义：

- `fileSize`：0
- `mimeType`：null
- `fileSignature`：null
- `fileType`：空字符串
- `fileName`、`filePath`、`createdAt`、`updatedAt`：按 fs-entry 实际值返回

metadataSearch 分页和稳定排序继续沿用其既有规则，文件和目录共同按 `updated_at ASC, kid ASC` 排序。`total` 是同时包含文件和目录的匹配总数。

## 9. 共享元数据处理组件

文件导入、文件修改、目录创建和目录修改应复用一个共享组件，职责包括：

1. 校验 metadata 顶层必须是 mapping/object。
2. 复用 `prepare_front_matter_metadata_value` 推断 `valueType` 并标准化值。
3. 合并请求 metadata 和 front matter。
4. 对同一 `fs_entry_id` 执行批量 upsert。
5. 保持系统字段只读规则一致。

共享组件只依赖中性的元数据模型和仓储，不应把 `knowledge_build` 反向耦合进 `knowledge_base`。

## 10. 仓储与服务改动范围

预计涉及：

- `knowledge_base/api/schemas.py`
  - listDir/glob 的 metadata、分页请求字段。
  - 浏览返回字段。
  - 文件和目录 CRUD 的 metadata 字段。
- `knowledge_base/api/metadata_schemas.py`
  - metadata get/update 的资源路径语义。
  - metadataSearch 结果的 `type` 字段。
- `knowledge_base/api/routes.py`
  - multipart metadata JSON 解析。
  - 分页响应结构。
- `knowledge_base/services/knowledge_base_service.py`
  - 浏览排序、分页、构建状态与元数据回填。
  - 目录 metadata 生命周期。
- `knowledge_base/services/knowledge_item_ingestion_service.py`
  - 导入 metadata 与 front matter 合并。
- `knowledge_base/services/document_update_service.py`
  - 修改 metadata 与 front matter 合并。
- 元数据 query/update service
  - 从 file-only 泛化为 fs-entry。
- `knowledge_base/repositories/knowledge_fs_entry_repository.py`
  - 浏览基础字段和通用路径解析。
- `knowledge_base/repositories/knowledge_build_task_repository.py`
  - 批量查询最新构建任务。
- `knowledge_base/repositories/file_metadata_value_repository.py`
  - 批量获取多个 fs-entry 的指定元数据。
- `knowledge_base/repositories/metadata_search_repository.py`
  - 不增加资源类型入参或类型过滤，统一查询有效文件和目录，并向上层返回条目类型。
- `qa/common/operation_registry.py`
  - Agent 侧 `listDir`/`glob` 工具入参同步暴露 `metadataFieldList`、`pageNum`、`pageSize`，并以 camelCase 原样转发。
  - 工具输出模型包含 `updatedAt`、`buildStatus`、`buildCurrentStep` 和 `metadata`，不能停留在旧版字段集。

原则上不新增 SQL migration。如果实现过程中发现生产旧版本 schema 对目录元数据存在数据库约束，需单独增加幂等迁移并纳入 schema bootstrap 集成测试，不能在业务代码中规避。

## 11. 性能与事务要求

- listDir/glob 不得对每个结果逐条查询构建状态或元数据。
- 构建状态按当前页全部文件 ID 一次查询。
- 元数据按当前页全部 entry ID 和 `metadataFieldList` 一次查询。
- 未指定或指定空 `metadataFieldList` 时完全跳过元数据表查询。
- listDir 可在仓储层排序和分页；glob 必须保证完整匹配后的全局排序和分页语义。
- 文件或目录 CRUD 中，entry 变更与 metadata 变更必须在同一数据库事务内提交或回滚。
- 文件对象存储写入失败、数据库提交失败以及乐观锁失败时，不得留下部分 metadata 更新。

## 12. 分提交实施方案

每一步均产生一个完整提交，并在 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py` 中增加对应集成测试。涉及 SQL 兼容时，同时补充真实 schema bootstrap 集成测试。

### 提交 1：浏览接口补充系统信息

建议提交信息：

```text
feat(knowledge): expose timestamps and build state in browse APIs
```

实现：

- listDir/glob 增加 `updatedAt`、`buildStatus`、`buildCurrentStep`、`metadata`。
- 此阶段 `metadata` 固定返回 `{}`。
- 批量获取最新构建任务。

集成测试：

- 未构建、构建中、完成、失败、不支持构建的文件。
- 目录构建字段为 null。
- listDir 与 glob 对同一条目返回相同系统字段。
- metadata 始终为 `{}`。

### 提交 2：浏览接口增加兼容分页和稳定排序

建议提交信息：

```text
feat(knowledge): add optional pagination to browse APIs
```

实现：

- 增加可选 `pageNum`、`pageSize`。
- 未分页请求保持旧响应结构。
- 分页请求返回 `total/pageNum/pageSize`。
- 应用目录优先、名称和 entry ID 稳定排序。

集成测试：

- 不分页返回全部结果。
- 只传 pageSize 时默认第一页。
- 正常首页、中间页、末页和越界页。
- 只传 pageNum、零值、负数、超大 pageSize 的校验。
- listDir/glob 的目录优先和名称排序。
- 分页之间无重复、无遗漏。

### 提交 3：浏览接口按需返回嵌套 metadata

建议提交信息：

```text
feat(knowledge): support selected metadata in browse APIs
```

实现：

- 增加 `metadataFieldList`。
- 按 search 语义返回指定字段。
- 元数据统一嵌套在 `metadata` 下。
- metadata 始终为 object，未指定或无命中时返回 `{}`。
- 仅对当前页执行批量回填。

集成测试：

- 未指定和空数组均返回 `{}`。
- 仅返回指定字段。
- 未知字段返回 `{}`。
- 支持 string、number、boolean、datetime、stringList。
- 支持系统元数据字段。
- listDir/glob 元数据一致。

### 提交 4：文件导入支持 metadata

建议提交信息：

```text
feat(knowledge): accept metadata during document import
```

实现：

- import 增加 multipart metadata JSON。
- 抽取共享元数据转换、合并和 upsert 逻辑。
- 请求 metadata 与 front matter 合并，front matter 优先。
- zip 公共 metadata 支持。

集成测试：

- 非 Markdown metadata。
- Markdown 无冲突合并和冲突覆盖。
- `processFrontMatter=false`。
- 非法 metadata JSON。
- zip 公共 metadata 和单文件 front matter 覆盖。
- 失败时文件和 metadata 一起回滚。

### 提交 5：文件修改支持 metadata

建议提交信息：

```text
feat(knowledge): merge request metadata on document update
```

实现：

- update 增加 multipart metadata JSON。
- 复用共享处理逻辑。
- 未涉及的既有字段保留。
- metadata 与文件修改事务和补偿流程集成。

集成测试：

- 新增、覆盖和保留字段。
- front matter 冲突优先级。
- `processFrontMatter=false`。
- 乐观锁、重复 checksum、对象存储或数据库失败时回滚。
- 修改后 listDir/glob 返回新 metadata，并显示构建状态已清空。

### 提交 6：目录元数据生命周期

建议提交信息：

```text
feat(knowledge): support directory metadata lifecycle
```

实现：

- directories/create、directories/update 增加 metadata。
- metadata get/update 支持目录。
- 重命名和移动保留 metadata。
- 删除目录子树清理全部文件和目录 metadata。

集成测试：

- 创建目录并通过 get/listDir/glob 读取 metadata。
- 对目录执行 set、unset、append、remove、clear。
- 目录元数据批量操作的原子回滚。
- 重命名、移动后 metadata 保留。
- 更新目录时只修改传入字段。
- 删除目录和父目录子树后所有相关 metadata 不可访问。
- 已存在目录的 create + metadata 重试幂等。

### 提交 7：metadataSearch 出参标识资源类型与文档收口

建议提交信息：

```text
feat(knowledge): include directories in metadata search
```

实现：

- metadataSearch 请求结构保持不变，不增加任何文件/目录区分参数。
- 每次查询统一覆盖满足条件的有效文件和目录。
- 结果增加 `type`，值为 `file` 或 `directory`。
- `total`、分页和排序统一覆盖文件与目录。
- 更新所有受影响接口文档、API 导航和集成测试计划。

集成测试：

- 同一份原有请求可以同时返回文件和目录，不需要传入类型条件。
- 每项通过 type 正确区分文件和目录。
- DSL 自定义字段和系统字段可以命中目录。
- 分页总数与稳定排序正确。
- 端到端验证创建、修改、浏览、查询、检索、重命名和删除全过程。

## 13. 文档更新范围

至少更新：

- `docs/modules/knowledge/api/interfaces/listDir.md`
- `docs/modules/knowledge/api/interfaces/glob.md`
- `docs/modules/knowledge/api/interfaces/knowledgeItems-import.md`
- `docs/modules/knowledge/api/interfaces/knowledgeItems-update.md`
- `docs/modules/knowledge/api/interfaces/directories-create.md`
- `docs/modules/knowledge/api/interfaces/directories-update.md`
- `docs/modules/knowledge/api/interfaces/directories-delete.md`
- `docs/modules/knowledge/api/interfaces/knowledgeItems-metadata-get.md`
- `docs/modules/knowledge/api/interfaces/knowledgeItems-metadata-update.md`
- `docs/modules/knowledge/api/interfaces/knowledgeItems-metadataSearch.md`
- `docs/modules/knowledge/api/metadata-and-dsl.md`
- `docs/modules/knowledge/api/README.md`
- `docs/modules/api-integration-test-plan.md`

每个功能提交同步更新对应接口文档；最后一个提交执行全量交叉检查和导航收口，而不是把所有文档延迟到最后才修改。

## 14. 验收标准

- 旧版 listDir/glob 请求不传新增字段时仍返回全部结果。
- 每个 listDir/glob 条目始终包含 object 类型的 `metadata`，未请求元数据时为 `{}`。
- 分页前按目录优先和名称稳定排序。
- 分页结果的 `total`、页数据、越界行为正确，无重复或遗漏。
- `metadataFieldList` 只控制 metadata 内容，不影响固定系统字段。
- 文件请求 metadata 与 front matter 正确合并，冲突时 front matter 优先。
- 文件和目录元数据写入具备原子性。
- 目录元数据在重命名、移动后保留，在删除后不可访问。
- metadata get/update/metadataSearch 对目录行为明确且有集成测试。
- 不产生逐条构建状态或逐条元数据查询。
- 每个实施步骤均为可独立审查、独立运行集成测试的完整提交。

## 15. 集成测试清单

以下用例是实施时必须落入 `tests/knowledge_base/integration/test_kb_api_stateful_integration.py` 的验收清单。用例按提交分组，每个提交完成时必须保证该组新增用例和已有知识库集成测试全部通过。

### 15.1 提交 1：浏览系统字段与构建状态

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| BR1 | listDir 返回固定字段 | 创建目录并导入未构建文件后调用 listDir | 文件和目录均包含 `size`、`updatedAt`、`buildStatus`、`buildCurrentStep`、`metadata`；metadata 为 `{}` |
| BR2 | glob 返回固定字段 | 创建多层目录和文件后调用 glob | glob 与 listDir 对同一条目的固定字段一致 |
| BR3 | 未构建文件 | 导入文件但不调用构建接口 | `buildStatus` 和 `buildCurrentStep` 均为 null |
| BR4 | 构建中 | 让构建任务停留在 running/markdown 后浏览 | 返回 `running` 和 `markdown` |
| BR5 | 构建成功 | 完成文件构建后浏览 | 返回 `complete` 和 `complete` |
| BR6 | 构建失败 | 注入构建失败后浏览 | 返回 `failed` 和失败发生时的 current step |
| BR7 | 不支持构建 | 导入不支持类型并触发构建后浏览 | 返回 `unsupported` 和实际 current step |
| BR8 | 最新任务优先 | 同一文件先失败再成功 | 只返回最新成功任务状态 |
| BR9 | 文件修改使构建失效 | 完成构建后修改文件再浏览 | 构建任务清理后两个构建字段恢复为 null，updatedAt 更新 |

### 15.2 提交 2：分页与排序

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| PG1 | listDir 旧式不分页 | 创建多项后不传分页参数 | 返回全部结果；resultObject 不增加分页字段 |
| PG2 | glob 旧式不分页 | 多路径匹配且不传分页参数 | 返回全部匹配结果；resultObject 不增加分页字段 |
| PG3 | pageSize 默认首页 | 只传 pageSize | pageNum 为 1，返回第一页及正确 total |
| PG4 | 正常多页 | 连续请求全部页 | 页间无重复、无遗漏，合并后等于不分页结果 |
| PG5 | 越界页 | 请求超过最后一页 | data 为空，total/pageNum/pageSize 正确 |
| PG6 | 目录优先 | 同层混合创建文件和目录 | 所有目录排在所有文件之前 |
| PG7 | 名称排序 | 创建大小写混合名称和同名排序边界 | 按不区分大小写名称、原始名称、entry ID 稳定排序 |
| PG8 | glob 全局排序 | 在不同父目录创建匹配项 | 最终结果按完整 name 全局排序，不受遍历顺序影响 |
| PG9 | 非法分页组合 | 只传 pageNum | 请求校验失败且不执行查询 |
| PG10 | 非法分页数值 | 传 0、负数或 pageSize 大于 10000 | 请求校验失败 |

### 15.3 提交 3：浏览元数据召回

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| BM1 | 未指定字段 | 不传 metadataFieldList 浏览 | 每项 metadata 为 `{}` |
| BM2 | 空字段列表 | 传空 metadataFieldList | 每项 metadata 为 `{}` |
| BM3 | 选择部分字段 | 文件有多个元数据，只请求其中两个 | metadata 只包含请求且实际存在的字段 |
| BM4 | 未知字段 | 仅请求不存在字段 | metadata 为 `{}` |
| BM5 | 多值类型 | 请求 string、number、boolean、datetime、stringList | valueType 和 value 与 metadata/get 一致 |
| BM6 | 系统元数据 | 请求 updatedAt、fileSize、filePath | 嵌套 metadata 返回标准结构，顶层固定字段仍存在 |
| BM7 | 分页后回填 | 分页浏览且每页条目元数据不同 | 当前页只出现当前页条目的元数据，无串值 |
| BM8 | listDir/glob 一致 | 对同一资源使用相同 metadataFieldList | 两接口返回的 metadata 完全一致 |
| BM9 | QA 工具转发 | Agent 通过 listDir/glob 工具传 metadataFieldList 和分页参数 | 远程 HTTP JSON 保留全部 camelCase 字段，响应 metadata 不被工具模型丢弃 |

### 15.4 提交 4：文件导入 metadata

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| FI1 | 非 Markdown 显式元数据 | 导入 PDF 并传 metadata | metadata/get 可读取全部显式字段 |
| FI2 | Markdown 合并 | 请求 metadata 与 front matter 字段不冲突 | 两侧字段均被写入 |
| FI3 | front matter 冲突优先 | 两侧包含同名字段 | 最终值来自 front matter |
| FI4 | 关闭 front matter | processFrontMatter=false 且两侧有冲突 | 仅请求 metadata 生效 |
| FI5 | 类型转换 | 传入所有支持的 JSON 类型 | 存储类型与现有 front matter 推断规则一致 |
| FI6 | 非法 JSON | multipart metadata 不是合法 JSON | 请求失败且文件未创建 |
| FI7 | 非 object JSON | metadata 为数组或标量 | 请求校验失败且文件未创建 |
| FI8 | zip 公共元数据 | zip 中包含多个文件并传 metadata | 每个成功文件获得公共元数据 |
| FI9 | zip 单文件覆盖 | zip 中 Markdown front matter 与公共字段冲突 | 该 Markdown 使用 front matter，其他文件使用公共值 |
| FI10 | 原子回滚 | 注入元数据写入失败 | 文件记录、存储对象和元数据均不留下半成功状态 |
| FI11 | 导入自动创建父目录 | 向不存在的多层路径导入 Markdown，请求 metadata 与 front matter 冲突 | 新建父目录只获得请求 metadata；文件获得合并结果且 front matter 优先；已存在父目录不变 |

### 15.5 提交 5：文件修改 metadata

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| FU1 | 新增和覆盖 | 修改文件并传新旧 metadata 字段 | 新字段增加，同名旧字段覆盖 |
| FU2 | 保留未涉及字段 | 原文件有多个字段，修改只传一个 | 未传字段继续保留 |
| FU3 | front matter 冲突优先 | 请求与新 front matter 同名 | 最终值来自新 front matter |
| FU4 | 关闭 front matter | processFrontMatter=false | 请求 metadata 生效，front matter 不参与写入 |
| FU5 | 构建状态和浏览同步 | 已构建文件修改成功 | listDir/glob 显示新 metadata、更新时间和空构建状态 |
| FU6 | 乐观锁失败 | referSignature 使用旧值 | 文件内容和 metadata 均保持原值 |
| FU7 | 重复校验失败 | skipIfDuplicate 命中其他文件 | 文件内容和 metadata 均保持原值 |
| FU8 | 存储失败回滚 | 注入原文件写入失败 | 数据库 metadata 无变化 |
| FU9 | 数据库失败补偿 | 对象写入后注入数据库失败 | 原对象恢复，metadata 无部分更新 |

### 15.6 提交 6：目录元数据生命周期

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| DM1 | 创建目录 metadata | directories/create 传 metadata | metadata/get、listDir、glob 均可读取 |
| DM2 | 递归创建目标范围 | 创建多层路径并传 metadata | 最终目录和本次自动创建的中间目录均具有请求 metadata，已存在父目录不变 |
| DM3 | 幂等创建 | 对已存在目录重复 create 并传相同 metadata | 不产生重复值，结果保持一致 |
| DM4 | 创建时更新 metadata | 对已存在目录 create 并传新值 | 指定字段按 upsert 语义更新 |
| DM5 | 目录 metadata/get | 使用目录路径查询部分和全部字段 | 返回目录自定义及系统元数据 |
| DM6 | 目录 metadata/update | 对目录执行 set/unset/append/remove/clear | 每种操作与文件语义一致 |
| DM7 | 更新原子性 | 同一批中先 set 再触发非法 append | 整批回滚，目录元数据无部分更新 |
| DM8 | 重命名并更新 | directories/update 同时改名和传 metadata | 新路径有效、旧路径失效、metadata 正确更新 |
| DM9 | 仅重命名 | directories/update 不传 metadata | 全部既有 metadata 保留 |
| DM10 | 移动目录 | knowledgeItems/move 移动目录子树 | 目录和后代的 metadata 均跟随 fs_entry 保留 |
| DM11 | 删除空目录 | 删除带 metadata 的空目录 | get/update/listDir/glob 均不可再访问该目录及元数据 |
| DM12 | 删除目录子树 | 删除含目录和文件 metadata 的父目录 | 子树所有元数据被软删除且不可召回 |
| DM13 | 移动目标目录 metadata | 通过 targetDirectoryPath 和 targetFilePath 移动到部分不存在的路径并传 metadata | 仅本次新建的各层目标目录获得请求 metadata；已存在目标目录和源子树不变；失败时一起回滚 |

### 15.7 提交 7：metadataSearch 与跨接口一致性

| 编号 | 场景 | 操作 | 核心断言 |
| --- | --- | --- | --- |
| MS1 | 入参不区分资源类型 | 使用原有 metadataSearch 请求体，不传任何类型参数 | 请求成功，服务不要求调用方声明文件或目录 |
| MS2 | 同时返回两类资源 | 文件和目录具有相同可检索字段，使用同一份 where 查询 | 同一次查询同时返回 file 和 directory |
| MS3 | type 字段 | 查询混合结果 | 每项 type 与实际 fs-entry 类型一致 |
| MS4 | 路径字段兼容 | 查询目录结果 | 目录路径继续通过 filePath 返回 |
| MS5 | 目录自定义字段过滤 | DSL 按目录独有字段过滤 | 正确命中目录且不误命中其他资源 |
| MS6 | 目录系统字段过滤 | DSL 使用 fileName、filePath、updatedAt | 正确命中目录 |
| MS7 | 目录空系统字段 | 请求 fileSize、mimeType、fileSignature、fileType | 分别返回 0、null、null、空字符串 |
| MS8 | 混合分页 | 文件和目录共同超过一页 | total 包含两类资源，分页无重复遗漏 |
| MS9 | 稳定排序 | 多资源 updatedAt 相同 | 按 kid 稳定排序 |
| MS10 | 重命名一致性 | 重命名带 metadata 的目录后查询 | 旧 filePath 不再出现，新 filePath 保留 metadata |
| MS11 | 删除一致性 | 删除匹配目录子树后再次查询 | 已删除文件、目录及 metadata 均不再命中 |
| MS12 | 端到端链路 | 创建目录和文件、写入、浏览、更新、搜索、重命名、删除 | 各接口在每个阶段观察到一致状态 |

### 15.8 测试执行门禁

每个提交至少执行：

```bash
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= \
no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
uv run python -m pytest \
tests/knowledge_base/integration/test_kb_api_stateful_integration.py -v
```

同时执行相关模块单元测试。全部功能完成后执行：

```bash
bash scripts/knowledge_base/run_unit_tests.sh
```

若改动包含 SQL migration 或依赖真实 OpenGauss 行为，还必须启动知识库中间件并执行：

```bash
bash scripts/knowledge_base/run_integration_tests.sh
```
