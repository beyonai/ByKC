# KnowledgeEntity 发现与文档富化接口设计

## 1. 文档目标

本文档定义 KnowledgeEntity v1 的接口契约，包括：

- 文档处理资格判断；
- 异步 KnowledgeEntity 发现；
- 异步实体文档 Enrich；
- 统一任务状态和结果查询；
- 语义关系查询；
- 内部 Python/SDK Callback；
- 幂等、错误码和并发语义；
- 接口落地所需的数据表复用与增改建议。

方法论和实体定义见 [KnowledgeEntity 发现、身份治理与文档富化方法论设计](./knowledge-entity-discovery-enrichment-design.md)。

## 2. 设计原则

- KnowledgeEntity 是 `knowledgeItems` 的一种，不设计独立实体 CRUD；
- 原始文档和实体文档继续使用现有文档导入、读取、更新、metadata 和引用接口；
- HTTP 请求只能提交可序列化参数，不包含 Python callable；
- 自定义 Callback 只通过内部 Python/SDK 方法传入；
- discovery 和 enrich 都是异步任务；一次请求形成一个 `batchId`，每个实际处理文件形成一个 `taskId`；
- 任务查询结果是最终事实来源，Callback 只是进程内通知；
- 无草稿、审核和发布接口；
- 模板覆盖不足不作为 Enrich 接口失败条件；
- Markdown 引用是带正文位置证据的 `MENTIONS` 断言，与 Discovery/Enrich 关系共用一套持久化和生命周期；兼容引用接口只是物理出现视图。

## 3. 通用约定

### 3.1 Base URL 与返回信封

- Base URL：`/api/v1`
- 协议：HTTP
- 请求与响应：`application/json`

成功响应：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应：

```json
{
  "resultCode": "-1",
  "resultMsg": "document kind does not support entity discovery",
  "resultObject": {
    "errorCode": "DOCUMENT_KIND_MISMATCH"
  }
}
```

### 3.2 文档定位

请求沿用知识模块现有定位方式：

- `knCode`：知识库编码；
- `filePath`：知识库内文件路径，以 `/` 开头；
- 响应中的 `fileId`：`knowledge_fs_entry.kid` 的字符串表示。

调用方不直接使用 `fileId` 绕过知识库和路径权限校验。内部执行和关系响应使用 `fileId` 作为稳定身份。

### 3.3 枚举

能力：

```text
entityDiscovery
entityEnrich
```

处理资格：

```text
ELIGIBLE_AND_STALE
ELIGIBLE_BUT_FRESH
INELIGIBLE
```

任务类型：

```text
ENTITY_DISCOVERY
DOCUMENT_ENRICH
```

任务状态：

```text
PENDING
RUNNING
SUCCEEDED
FAILED
CANCELLED
SKIPPED
```

关系类型：

```text
MENTIONS
PART_OF
IS_A
DEPENDS_ON
```

### 3.4 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/processingEligibility` | 判断文档是否可以、是否需要执行指定能力 |
| `POST` | `/api/v1/knowledgeItems/entityDiscovery` | 按单文件或全库异步执行 KnowledgeEntity 发现 |
| `POST` | `/api/v1/knowledgeItems/entityEnrich` | 按单文件或全库异步富化 KnowledgeEntity 文档 |
| `POST` | `/api/v1/knowledgeItems/processingTaskStatus` | 按知识库及可选文件路径查询任务状态和结果 |
| `POST` | `/api/v1/knowledgeItems/semanticRelations` | 查询文档的语义关系 |

v1 不新增 KnowledgeEntity 创建、修改、删除和读取接口：

- 创建由 discovery 完成；
- 正文读取使用 `readFile`；
- metadata 读取和修改使用现有 metadata 接口；
- 文件更新、移动和删除使用现有 `knowledgeItems` 接口。

## 4. 处理资格接口

### `POST /api/v1/knowledgeItems/processingEligibility`

判断文档对指定能力是否满足资格，以及最近成功任务之后输入是否发生变化。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 文档路径 |
| `capability` | string | 是 | `entityDiscovery` 或 `entityEnrich` |
| `definitionVersion` | string | 否 | Discovery 定义版本，默认当前稳定版本 |
| `enrichVersion` | string | 否 | Enrich 方法版本，默认当前稳定版本 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "capability": "entityDiscovery",
  "definitionVersion": "ke/1.0"
}
```

成功响应：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "fileId": "1024",
    "knCode": "1",
    "filePath": "/原始文档/AI时代的组织革命.md",
    "documentKind": "original",
    "capability": "entityDiscovery",
    "eligibility": "ELIGIBLE_AND_STALE",
    "reasonCode": "INPUT_CHANGED",
    "lastSuccessfulTaskId": "801",
    "lastSuccessfulAt": "2026-08-17T10:00:00+08:00"
  }
}
```

`reasonCode` 建议值：

```text
NEVER_PROCESSED
INPUT_CHANGED
METHOD_VERSION_CHANGED
EVIDENCE_CHANGED
INPUT_UNCHANGED
CAPABILITY_DISABLED
DOCUMENT_KIND_MISMATCH
UNSUPPORTED_FILE_FORMAT
KNOWLEDGE_ENTITY_PATH_REQUIRED
UNSUPPORTED_CONTENT_TYPE
CONTENT_NOT_READY
IDENTITY_METADATA_INCOMPLETE
NO_EVIDENCE
PERMISSION_DENIED
```

判定原则：

- `original` 默认只允许 `entityDiscovery`；
- `knowledgeEntity` 默认只允许 `entityEnrich`；
- `processingCapabilities` 可以覆盖默认能力；
- 指纹相同返回 `ELIGIBLE_BUT_FRESH`，而不是接口错误；
- 不满足文档类型、权限、内容或证据条件返回 `INELIGIBLE`。

## 5. 实体发现接口

### `POST /api/v1/knowledgeItems/entityDiscovery`

异步扫描一个原始文档或知识库内全部符合条件的原始文档，锚定已有 KnowledgeEntity，发现并创建新的 KnowledgeEntity，建立 `MENTIONS` 关系。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 原始文档所属知识库 |
| `filePath` | string | 否 | - | 原始文档路径；不传表示处理该知识库下全部符合条件的原始文档 |
| `definitionVersion` | string | 否 | 当前稳定版本 | KnowledgeEntity 定义版本 |
| `maxEntities` | integer | 否 | `12` | 最大实体数，v1 不得超过 12 |
| `force` | boolean | 否 | `false` | 是否跳过 freshness 判断；不跳过资格和权限校验 |

HTTP 请求中不包含 `callback` 字段。

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "definitionVersion": "ke/1.0",
  "maxEntities": 12,
  "force": false
}
```

全库触发时不传 `filePath`：

```json
{
  "knCode": "1",
  "definitionVersion": "ke/1.0",
  "maxEntities": 12,
  "force": false
}
```

接受响应：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0001",
    "scope": "SINGLE_FILE",
    "taskType": "ENTITY_DISCOVERY",
    "definitionVersion": "ke/1.0",
    "eligibleCount": 1,
    "acceptedCount": 1,
    "reusedCount": 0,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "9001",
        "status": "PENDING",
        "fileId": "1024",
        "filePath": "/原始文档/AI时代的组织革命.md",
        "reused": false
      }
    ]
  }
}
```

全库请求的响应结构相同，`scope=WHOLE_KB`，计数为本次资格筛选和幂等判断的汇总。`tasks` 只返回本次接受或复用的任务摘要；当数量超过服务端上限时可以截断，完整状态通过 `processingTaskStatus` 按 `batchId` 查询。

相同输入指纹已经成功或已有运行中任务时不创建重复任务，计入 `reusedCount`：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0002",
    "scope": "SINGLE_FILE",
    "taskType": "ENTITY_DISCOVERY",
    "definitionVersion": "ke/1.0",
    "eligibleCount": 1,
    "acceptedCount": 0,
    "reusedCount": 1,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "801",
        "status": "SUCCEEDED",
        "fileId": "1024",
        "filePath": "/原始文档/AI时代的组织革命.md",
        "reused": true
      }
    ]
  }
}
```

Discovery 结果项语义：

| `action` | 含义 |
| --- | --- |
| `ANCHORED` | AC 或精确名称/别名命中已有实体 |
| `DISAMBIGUATED` | 同名多候选经上下文消歧后命中已有实体 |
| `MERGED_AS_ALIAS` | 新名称经身份裁决成为已有实体别名 |
| `CREATED` | 创建新的最小有效 KnowledgeEntity 文档 |
| `DROPPED` | 候选不满足实体定义或身份无法可信确认 |

Discovery 成功任务结果示例见任务状态接口。

### 5.1 Discovery 执行约束

- 只接受 `documentKind=original` 且启用 `entityDiscovery` 的文档；
- 文档必须已经生成可读 Markdown 正文；
- `filePath` 未传时，枚举当前知识库中所有符合上述条件的文件，并排除 `/KnowledgeEntity` 目录；
- 新实体只写入源文档所在知识库的固定 `/KnowledgeEntity` 目录，目录不存在时自动创建；接口不允许调用方指定其他知识库或目录；
- 新实体路径固定为 `/KnowledgeEntity/{规范可读名称}.md`，不附加 MD5、哈希签名或数字序号；
- 同库规范路径已存在时直接锚定该文件，不创建副本：文件必须是 KnowledgeEntity，`entityName` 与候选相同或缺失，subject 身份一致；明显的元数据或文档类型冲突使任务失败；
- Discovery 不自动覆盖已有实体的身份元数据或合并候选别名；缺失 `entityName` 时只在当前任务内以候选名完成锚定；
- 新实体正文中的来源路径以普通文本展示，不生成指向原始文档的 Markdown 链接；只持久化原始文档到实体的单向 `MENTIONS`，反向视图由查询层派生；
- 全系统词表只用于高性能候选召回，最终锚定、别名合并、关系建立和新实体创建都限定在当前知识库，不建立跨库实体关系；
- `maxEntities` 是每个源文件的结果上限，不是整个批次共享上限；不得通过截断隐藏已发生的写入；
- `force=true` 会跳过已成功任务的 freshness 复用并创建新任务；如同文件同类型仍有 `PENDING/RUNNING` 任务，则复用该活动任务，身份和关系写入仍保持幂等；
- 新实体及 `MENTIONS` 写入成功后才触发对应阶段 Callback。

## 6. 文档富化接口

### `POST /api/v1/knowledgeItems/entityEnrich`

异步召回授权证据，生成并原子更新一个 KnowledgeEntity 文档，或批量处理知识库 `/KnowledgeEntity` 目录下全部符合条件的实体文档，同时提取允许的语义关系。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 目标实体文档所属知识库 |
| `filePath` | string | 否 | - | KnowledgeEntity 文档路径；不传表示处理本库 `/KnowledgeEntity` 下全部符合条件的实体文档 |
| `enrichVersion` | string | 否 | 当前稳定版本 | Enrich 方法版本 |
| `evidenceKnCodeList` | string[] | 否 | 调用方有权访问的知识库 | 证据检索范围 |
| `topK` | integer | 否 | `20` | 语义证据候选上限，建议最大 100 |
| `force` | boolean | 否 | `false` | 是否跳过 freshness 判断 |

HTTP 请求中不包含 `callback` 或模板正文。模板由 `enrichVersion` 对应的服务端策略提供，并且只做软约束。

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/KnowledgeEntity/OSOT.md",
  "enrichVersion": "ke-enrich/1.0",
  "evidenceKnCodeList": ["1", "2"],
  "topK": 20,
  "force": false
}
```

全库触发时不传 `filePath`。接受响应：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ee-20260817-0001",
    "scope": "SINGLE_FILE",
    "taskType": "DOCUMENT_ENRICH",
    "enrichVersion": "ke-enrich/1.0",
    "eligibleCount": 1,
    "acceptedCount": 1,
    "reusedCount": 0,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "9101",
        "status": "PENDING",
        "fileId": "2048",
        "filePath": "/KnowledgeEntity/OSOT.md",
        "reused": false
      }
    ]
  }
}
```

没有可用证据的文件不进入可执行任务，计入 `skippedCount`；如果证据在任务执行过程中失效，则该文件任务进入终态 `SKIPPED`：

```json
{
  "status": "SKIPPED",
  "skipReason": "NO_EVIDENCE"
}
```

### 6.1 Enrich 执行约束

- 只接受 `documentKind=knowledgeEntity` 且启用 `entityEnrich` 的文档；
- 传入 `filePath` 时，目标必须属于当前知识库的 `/KnowledgeEntity` 目录；不传时只枚举该固定目录；
- `entityName`、`definitionVersion` 等身份 metadata 必须完整；
- 至少存在一份调用方有权访问的证据，否则任务进入 `SKIPPED`；
- evidence 范围只能收窄调用方权限，不能扩大权限；
- 模板章节缺失、顺序变化或占位符残留只产生 warning；
- 身份漂移、空正文、无权限引用和并发 checksum 冲突阻断写入；
- 非法关系被丢弃并记录，不阻断合法正文写入；
- Enrich 自己生成的新 checksum 不再次触发同一 Enrich；
- 文档和关系提交成功后才发送成功 Callback。

## 7. 任务状态接口

### `POST /api/v1/knowledgeItems/processingTaskStatus`

按知识库查询 discovery/enrich 任务；`filePath` 可选，传入时只查询该文件，不传时查询全库。HTTP 接口不要求调用方保存某个 `taskId` 才能找回任务。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码；服务内部解析为 `knowledge_base_id` 过滤任务 |
| `filePath` | string | 否 | - | 文件路径；不传表示查询该知识库全部文件任务 |
| `taskType` | string | 否 | 全部 | `ENTITY_DISCOVERY` 或 `DOCUMENT_ENRICH` |
| `batchId` | string | 否 | - | 只查询某次单文件或全库触发形成的批次 |
| `statusList` | string[] | 否 | 全部 | 任务状态过滤 |
| `latestOnly` | boolean | 否 | `true` | 是否只返回每个文件、每种任务类型的最新一条记录 |
| `includeDetails` | boolean | 否 | `false` | 是否返回 `result` 与 `error` 明细；全库查询建议保持 `false` |
| `pageNum` | integer | 否 | `1` | 页码 |
| `pageSize` | integer | 否 | `50` | 每页数量，建议最大 500 |

全库任务查询示例：

```json
{
  "knCode": "1",
  "taskType": "ENTITY_DISCOVERY",
  "latestOnly": true,
  "includeDetails": false,
  "pageNum": 1,
  "pageSize": 50
}
```

单文件任务查询示例：

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "latestOnly": false,
  "includeDetails": true,
  "pageNum": 1,
  "pageSize": 20
}
```

响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knowledgeBaseId": "11",
    "knCode": "1",
    "filePath": "/原始文档/AI时代的组织革命.md",
    "total": 1,
    "pageNum": 1,
    "pageSize": 20,
    "data": [
      {
        "taskId": "9001",
        "batchId": "ed-20260817-0001",
        "taskType": "ENTITY_DISCOVERY",
        "status": "SUCCEEDED",
        "currentStage": "entity_persist",
        "progress": 100,
        "fileId": "1024",
        "filePath": "/原始文档/AI时代的组织革命.md",
        "definitionVersion": "ke/1.0",
        "enrichVersion": null,
        "indexVersion": "ac/18",
        "createdAt": "2026-08-17T10:00:00+08:00",
        "startedAt": "2026-08-17T10:00:01+08:00",
        "finishedAt": "2026-08-17T10:00:08+08:00",
        "result": {
          "candidateCount": 6,
          "entityCount": 4,
          "anchoredCount": 2,
          "createdCount": 1,
          "mergedAliasCount": 1,
          "droppedCount": 2,
          "items": [
            {
              "action": "CREATED",
              "fileId": "2051",
              "filePath": "/KnowledgeEntity/OSOT-OCG.md",
              "entityName": "OSOT-OCG",
              "sourceLocation": {
                "startLine": 30,
                "endLine": 32,
                "text": "OCG 是 OSOT 的……"
              }
            }
          ]
        },
        "error": null
      }
    ]
  }
}
```

`includeDetails=false` 时省略 `result` 和 `error`。失败任务仍使用正常成功信封，任务项的 `status=FAILED`，并在启用明细时返回 `errorCode`、`message` 和 `retryable`。

一次全库触发不额外创建“父任务”记录：所有文件任务共用 `batchId`。因此可按知识库查看整体任务面，也可叠加 `filePath` 或 `batchId` 精确收窄范围。

知识库存在但没有匹配任务时返回 `total=0` 和空 `data`，不是 `TASK_NOT_FOUND`；传入的 `filePath` 本身不存在时返回 `DOCUMENT_NOT_FOUND`。

## 8. 文档逻辑关系查询接口

### `POST /api/v1/knowledgeItems/semanticRelations`

查询指定文档的 `MENTIONS`、`PART_OF`、`IS_A` 和 `DEPENDS_ON` 逻辑关系。Markdown 引用作为 `MARKDOWN_PARSER` 生产的 `MENTIONS` 参与逻辑去重；现有 `knowledgeItems/references` 仍作为“物理 Markdown 引用出现”的兼容视图。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 文档所属知识库 |
| `filePath` | string | 是 | - | 文档路径 |
| `direction` | string | 否 | `BOTH` | `OUTGOING`、`INCOMING`、`BOTH` |
| `relationCodeList` | string[] | 否 | 全部 v1 关系 | 关系类型过滤 |
| `pageNum` | integer | 否 | `1` | 页码 |
| `pageSize` | integer | 否 | `50` | 每页数量，建议最大 500 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/KnowledgeEntity/OSOT.md",
  "direction": "BOTH",
  "relationCodeList": ["MENTIONS", "PART_OF"],
  "pageNum": 1,
  "pageSize": 50
}
```

响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "fileId": "2048",
    "total": 2,
    "pageNum": 1,
    "pageSize": 50,
    "data": [
      {
        "relationId": "lr_e83691f6c1ef4d5288e652a0",
        "relationCode": "MENTIONS",
        "direction": "INCOMING",
        "source": {
          "fileId": "1024",
          "knCode": "1",
          "filePath": "/原始文档/AI时代的组织革命.md",
          "documentKind": "original"
        },
        "target": {
          "fileId": "2048",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT.md",
          "documentKind": "knowledgeEntity"
        },
        "assertionCount": 2,
        "confidence": 1.0,
        "discoveredBy": "MARKDOWN_PARSER",
        "definitionVersion": null,
        "sourceTaskId": null,
        "representativeEvidence": {
          "producerRunId": "markdown-update:9001",
          "evidenceFingerprint": "f4a3...",
          "sourceHeadingPath": "组织模式 / OSOT",
          "startLine": 30,
          "endLine": 30,
          "startOffset": 816,
          "endOffset": 842
        }
      },
      {
        "relationId": "lr_b4750ce075076a141a0a5470",
        "relationCode": "PART_OF",
        "direction": "INCOMING",
        "source": {
          "fileId": "2051",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT-OCG.md",
          "documentKind": "knowledgeEntity"
        },
        "target": {
          "fileId": "2048",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT.md",
          "documentKind": "knowledgeEntity"
        },
        "assertionCount": 1,
        "confidence": 0.96,
        "discoveredBy": "ENTITY_ENRICH",
        "definitionVersion": "ke/1.0",
        "sourceTaskId": "9101",
        "representativeEvidence": {
          "producerRunId": "entity-enrich:9101",
          "evidenceFingerprint": "9ba1...",
          "sourceHeadingPath": null,
          "startLine": null,
          "endLine": null,
          "startOffset": null,
          "endOffset": null
        }
      }
    ]
  }
}
```

关系查询执行目标文档和相邻文档的权限过滤。调用方无权访问的边不返回，也不以数量暴露。`relationId` 是由 source/relation/target 派生的稳定逻辑 ID，不随某条物理断言重建而变化；`assertionCount` 是当前聚合的物理断言数。如果存在 Markdown 位置断言，`representativeEvidence` 优先返回其章节、行和偏移。

v1 不提供独立证据正文查询，也不实现 `knowledge_document_relation_evidence`。当前返回的是聚合数量和一条代表性轻量位置；若后续需要展开所有证据出现、证据 checksum 失效检测或长期关系审计，再增加独立证据层。

## 9. 内部 Python/SDK 接口

### 9.1 概念签名

```python
TaskCallback = Callable[[TaskEvent], Awaitable[None] | None]

async def evaluate_processing_eligibility(
    request: ProcessingEligibilityRequest,
) -> ProcessingEligibilityResult: ...

async def discover_knowledge_entities(
    request: EntityDiscoveryRequest,
    *,
    callback: TaskCallback | None = None,
) -> ProcessingBatchAccepted: ...

async def enrich_knowledge_entities(
    request: EntityEnrichRequest,
    *,
    callback: TaskCallback | None = None,
) -> ProcessingBatchAccepted: ...

async def get_processing_task_status(
    request: ProcessingTaskStatusRequest,
) -> ProcessingTaskPage: ...
```

`EntityDiscoveryRequest.file_path` 和 `EntityEnrichRequest.file_path` 均可为空，语义与 HTTP 全库触发一致；`ProcessingTaskStatusRequest` 以知识库为必选条件、文件路径为可选条件。HTTP 层调用相同 service，但固定传入 `callback=None`。只有同一 Python 进程中的 SDK 调用方可以传 callable。

### 9.2 Callback 事件

```python
class TaskEvent:
    event_id: str
    task_id: str
    batch_id: str
    task_type: str
    event_type: str
    stage: str | None
    status: str
    sequence: int
    progress: int
    source_file_id: str | None
    target_file_ids: tuple[str, ...]
    result_summary: Mapping[str, Any] | None
    error: Mapping[str, Any] | None
    occurred_at: datetime
```

v1 事件：

```text
task.started
stage.completed
task.succeeded
task.failed
```

示例：

```python
async def on_task_event(event: TaskEvent) -> None:
    if event.event_type == "task.succeeded":
        await refresh_local_cache(event.target_file_ids)

accepted = await discover_knowledge_entities(
    request,
    callback=on_task_event,
)
```

### 9.3 Callback 语义

- callable 保存在进程内，不写入数据库；
- 任务必须在能够持有该 callable 的同一进程执行；
- Callback 异常被捕获和记录，不改变主任务结果；
- 成功和阶段完成事件在对应数据提交后触发；
- 服务重启可能丢失 Callback；
- v1 不承诺 exactly-once；
- Callback 不接收数据库连接、事务对象或可变内部状态。

## 10. 幂等与错误码

### 10.1 幂等

Discovery 输入指纹至少包含：

```text
sourceFileChecksum
definitionVersion
discoveryMethodVersion
processingPolicyVersion
```

Enrich 输入指纹至少包含：

```text
entityIdentityMetadata
evidenceFileIdsAndChecksums
semanticRelationVersions
enrichVersion
templateVersion
```

规则：

- 相同指纹已有运行中任务：返回该任务；
- 相同指纹已有成功任务且 `force=false`：返回成功任务；
- `force=true`：跳过已成功任务的 freshness 复用；若已有同文件同类型的活动任务则仍复用该任务，否则创建新任务；
- AC 每次 `indexVersion` 变化不自动改变全库 Discovery 指纹；
- Enrich 输出 checksum 不作为下一次 Enrich 的自动触发输入。

### 10.2 接口错误码

| 错误码 | 含义 | 是否可重试 |
| --- | --- | --- |
| `DOCUMENT_NOT_FOUND` | 文档不存在 | 否 |
| `DOCUMENT_KIND_MISMATCH` | 文档类型不支持该能力 | 否 |
| `CAPABILITY_DISABLED` | `processingCapabilities` 禁用该能力 | 否 |
| `UNSUPPORTED_FILE_FORMAT` | Discovery 输入后缀不在文本白名单，或无后缀且 MIME 不是 `text/*`；PDF/Office 即使已生成 Markdown 也不参与处理 | 否 |
| `KNOWLEDGE_ENTITY_PATH_REQUIRED` | Enrich 目标不在本库 `/KnowledgeEntity` 目录下 | 否 |
| `UNSUPPORTED_CONTENT_TYPE` | Enrich 目标不是 `.md/.markdown` 文档 | 否 |
| `CONTENT_NOT_READY` | Markdown 尚未构建或正文为空 | 是 |
| `IDENTITY_METADATA_INCOMPLETE` | 实体身份 metadata 不完整 | 否 |
| `NO_EVIDENCE` | Enrich 无可用证据；通常进入 `SKIPPED` | 条件变化后可重试 |
| `INVALID_DEFINITION_VERSION` | 定义版本不存在 | 否 |
| `INVALID_ENRICH_VERSION` | Enrich 版本不存在 | 否 |
| `PERMISSION_DENIED` | 无文档或证据访问权限 | 否 |
| `STALE_WRITE` | 执行期间目标 checksum 改变 | 是 |
| `LLM_OUTPUT_INVALID` | 有限重试后仍无法解析 | 是 |
| `INDEX_UNAVAILABLE` | AC/精确词表均不可用 | 是 |
| `PERSISTENCE_FAILED` | 数据库或对象存储写入失败 | 是 |

资格接口使用 `eligibility/reasonCode` 表达正常的“不需要执行”或“不具备资格”；只有请求格式、资源访问和系统执行失败才返回 `resultCode=-1`。

## 11. 数据表增改建议

### 11.1 结论

KnowledgeEntity v1 **不新增实体主表、语义关系表或关系证据表**。原始文档与 KnowledgeEntity 继续复用文档主表，Markdown 引用与语义关系复用 `knowledge_file_reference`。任务存储按领域边界拆分：保留 `knowledge_build_task` 只服务文件构建，新增 `knowledge_semantic_processing_task` 统一承载 Discovery/Enrich。

建议：

| 分类 | 表或配置 | 结论 |
| --- | --- | --- |
| 文档主数据 | `knowledge_fs_entry` | 直接复用，不改表 |
| 实体/原始文档属性 | `knowledge_file_metadata_value`（增量 SQL `031`） | 复用自包含 EAV；回填历史 live FILE 的 `documentKind`，不改表结构 |
| Markdown 引用 / `MENTIONS` 断言 | `knowledge_file_reference` | 继续复用；历史行由增量脚本回填为 `MENTIONS` |
| 文档 chunk 和向量检索 | `knowledge_chunk`、embedding 表 | 直接复用 |
| Enrich 文档更新时间线 | `knowledge_file_update_timeline` | v1 直接复用，不改表 |
| 文件构建任务 | `knowledge_build_task`（SQL `006`） | 保留现有表与仓储语义，不修改旧 SQL |
| Discovery/Enrich 异步任务 | `knowledge_semantic_processing_task`（增量 SQL `029`） | 新增一张语义处理任务表，两种任务共用生命周期 |
| 文档关系断言 | `knowledge_file_reference`（增量 SQL `030`） | 增量扩展为统一断言投影；原 `026` 不变 |
| 轻量关系证据 | `knowledge_file_reference`（增量 SQL `030`） | 保存标题路径、行/偏移、生产者运行和证据指纹；不实现独立 evidence 表 |
| 全系统实体词面投影 | `knowledge_entity_surface` | 生产规模强烈建议新增；PoC 可暂时从 metadata 重建 |
| 合并后的旧 ID 重定向 | `knowledge_document_redirect` | v1 不需要，实体合并/拆分阶段再增加 |

### 11.2 复用现有表

#### `knowledge_fs_entry`

原始文档和 KnowledgeEntity 继续共用该表：

- `kid` 是文档和实体稳定 ID；
- 路径、对象存储位置、checksum 和删除状态继续复用；
- 不新增 `entity_type`、`document_kind` 等专用列。

#### Metadata 表

SQL `025` 已将 `knowledge_file_metadata_value` 迁移为自包含 EAV，属性名和类型直接保存在
`property_name` / `value_type`，并删除旧 `knowledge_metadata_property_def`。因此以下属性
不需要新增属性定义或专用列：

| 属性 | valueType | 适用文档 |
| --- | --- | --- |
| `documentKind` | `string` | 全部内容文档 |
| `processingCapabilities` | `stringList` | 全部内容文档，可选覆盖默认能力 |
| `sourceType` | `string` | 原始文档 |
| `sourceUri` | `string` | 原始文档 |
| `sourceTime` | `datetime` | 原始文档 |
| `entityName` | `string` | KnowledgeEntity |
| `aliases` | `stringList` | KnowledgeEntity |
| `definitionVersion` | `string` | KnowledgeEntity |
| `subjectFileId` | `string` | subject-local KnowledgeEntity |
| `entityType` | `string` | KnowledgeEntity，可选 |
| `enrichVersion` | `string` | KnowledgeEntity，可选 |

`subjectFileId` 建议使用 `string`，避免接口层大整数精度和 `numeric` 序列化差异。

import/upload/update 会在文档写入事务内补齐 `documentKind`：
`/KnowledgeEntity` 保留目录下默认为 `knowledgeEntity`，其他路径默认为
`original`；显式已有值不覆盖。增量 SQL `031` 仅回填缺失该属性的 live FILE，
反复执行不会新增重复行。`processingCapabilities` 不物化默认值：缺失表示按
`documentKind` 使用默认能力，显式空列表才表示禁用。

#### `knowledge_file_reference`

SQL `026` 已经具备同库 source/target 文档 ID、状态和双向查询所需基础索引，因此 v1 复用它承载统一关系断言：

```text
Markdown 链接：MENTIONS + MARKDOWN_PARSER + KB_PATH 恢复定位 + 章节/行/偏移证据
Discovery 命中：MENTIONS + ENTITY_DISCOVERY + ENTITY_SURFACE 恢复定位
Enrich 关系：MENTIONS / PART_OF / IS_A / DEPENDS_ON + ENTITY_ENRICH + ENTITY_SURFACE 恢复定位
```

一行表示一次断言或证据出现，不直接等于一条唯一逻辑边。同一 source/relation/target 可以同时有 Markdown 证据和 Discovery/Enrich 证据；查询层聚合为一条逻辑关系，但保留断言数量和代表性证据信息。

#### `knowledge_file_update_timeline`

Enrich 对实体 Markdown 的更新可以继续记录为 `UPDATE`，因此 v1 不需要修改该表的 `event_type` 约束。

当后续需要明确记录 `MERGE`、`SPLIT`、`RENAME` 时，再扩展时间线事件类型或引入专门的身份治理审计记录。

### 11.3 新增：`knowledge_semantic_processing_task`（增量 SQL `029`）

`knowledge_build_task` 的边界保持不变，只记录文件到 Markdown、chunk 和向量索引的构建任务。不在 `006`、`013` 中增加 `task_type` 或改写原有索引。

Discovery 和 Enrich 共用批次、状态、幂等、Callback 与分页查询模型，因此不按任务类型拆成两张表。新表仅承载 `ENTITY_DISCOVERY`、`DOCUMENT_ENRICH` 两类语义处理任务，不作为全系统通用任务表。

统一语义：

- `fs_entry_id` 是本任务实际处理的文件：Discovery 为原始文档，Enrich 为 KnowledgeEntity 文档；
- `current_stage` 对应接口的 `currentStage`；
- 数据库使用小写状态，HTTP 层映射为大写枚举；
- `kid` 作为对外 `taskId`。

字段建议：

| 字段 | 建议 | 说明 |
| --- | --- | --- |
| `knowledge_base_id` | `bigint NOT NULL` | 知识库 ID |
| `fs_entry_id` | `bigint NOT NULL` | 被处理文件 ID；路径查询时 join 文档表 |
| `task_type` | `varchar(32) NOT NULL` | `ENTITY_DISCOVERY` 或 `DOCUMENT_ENRICH` |
| `batch_id` | `varchar(64) NULL` | 一次单文件或全库触发的批次 ID |
| `progress` | `smallint NULL` | 0 至 100 |
| `input_fingerprint` | `varchar(128) NULL` | 幂等和 freshness 判断 |
| `input_checksum` | `varchar(128) NULL` | 任务开始时被处理文件的 checksum，用于审计和并发校验 |
| `definition_version` | `varchar(64) NULL` | Discovery 定义版本 |
| `enrich_version` | `varchar(64) NULL` | Enrich 方法版本 |
| `method_version` | `varchar(64) NULL` | 编排/提取方法版本 |
| `index_version` | `varchar(64) NULL` | 实际使用的 AC snapshot 版本 |
| `request_params` | `jsonb NULL` | 去除 callback 后的请求快照 |
| `result_payload` | `jsonb NULL` | 每文件任务结果，不作为独立证据表 |
| `error_code` | `varchar(64) NULL` | 结构化错误码 |
| `error_message` | `text NULL` | 错误详情 |

一次全库请求先完成资格筛选，再为每个实际处理文件创建一行，所有行共享 `batch_id`；不增加父任务或批次表。未满足资格的文件只进入接受响应的 `skippedCount`，不强制落一条 `SKIPPED` 记录；执行中才发现证据失效等情况时，文件任务可以落为 `skipped`。

新表建议增加：

- `(knowledge_base_id, task_type, created_at DESC, kid DESC)`：全库状态查询；
- `(knowledge_base_id, fs_entry_id, task_type, created_at DESC, kid DESC)`：可选路径查询；
- `(batch_id, created_at, kid)`：批次查询；
- `(task_type, fs_entry_id, input_fingerprint, status)`：幂等复用；
- `(fs_entry_id, task_type) WHERE status IN ('pending', 'running')` 部分唯一索引：防止同一文件的同类活动任务重复接受。

仓储边界：

- `KnowledgeBuildTaskRepository` 保留原有构建方法，不感知 `task_type`；
- 新增 `KnowledgeSemanticProcessingTaskRepository`，负责语义处理任务的创建、更新、分页和计数；
- 按 `knowledge_base_id`、可选 `fs_entry_id`、可选 `batch_id` 分页查询；
- Callback callable 不写数据库，进程内执行器通过 `taskId` 暂时持有引用。

### 11.4 增量扩展：`knowledge_file_reference`（SQL `030`，原 `026` 不变）

建议增加：

| 字段 | 建议 | 说明 |
| --- | --- | --- |
| `relation_code` | `varchar(32) NOT NULL DEFAULT 'MENTIONS'` | v1 四种关系之一；历史 Markdown 行回填为 `MENTIONS` |
| `confidence` | `numeric(5,4) NULL` | 精确 `MENTIONS` 可为 1.0 |
| `discovered_by` | `varchar(32) NOT NULL DEFAULT 'MARKDOWN_PARSER'` | `MARKDOWN_PARSER`、`ENTITY_DISCOVERY`、`ENTITY_ENRICH` 等生产者 |
| `producer_run_id` | `varchar(64) NULL` | 一次 parser/update/task 运行的稳定标识，用于范围替换和重试幂等 |
| `evidence_fingerprint` | `varchar(128) NOT NULL` | 同一生产者运行内证据出现的稳定指纹 |
| `source_heading_path` | `text NULL` | Markdown 引用所在标题路径 |
| `start_line`、`end_line` | `integer NULL` | 1 基行号，成对出现 |
| `start_offset`、`end_offset` | `bigint NULL` | 0 基字符偏移，区间为左闭右开 |
| `target_locator_type` | `varchar(32) NOT NULL` | `KB_PATH`、`ENTITY_SURFACE` 或兼容性 `FS_ENTRY_ID` |
| `target_locator_value` | `text NOT NULL` | 目标删除后仍可用的恢复键；与当前是否已解析到 ID 解耦 |
| `target_suffix`（现有） | 改为 `text NULL` | openGauss A 兼容模式把空字符串视为 `NULL`；读取层继续归一为空后缀 |
| `definition_version` | `varchar(64) NULL` | 生成关系时使用的定义版本 |
| `source_task_id` | `bigint NULL REFERENCES knowledge_semantic_processing_task(kid) ON DELETE SET NULL` | 生成该断言的语义处理任务 |

目标 ID 是当前解析结果，locator 是稳定恢复依据，两者不能混为一个概念：

```text
已解析 Markdown：target_fs_entry_id = 当前文件 ID，locator = KB_PATH + 规范路径
已解析实体关系：target_fs_entry_id = 当前实体文档 ID，locator = ENTITY_SURFACE + 实体名
未解析/已断开：target_fs_entry_id = NULL，target_path 保存可解析值，locator 仍保留
```

`original_target` 保留正文或模型输出中的原始表达。v1 关系限定同一知识库，服务层校验 source、target 的 `knowledge_base_id` 一致。

建议约束和索引：

- `relation_code` 仅允许 `MENTIONS`、`PART_OF`、`IS_A`、`DEPENDS_ON`；
- 行号和偏移必须成对出现且区间有效；
- 精确断言唯一键包含 knowledgeBase、source、relation、locator、discoveredBy、producerRunId 和 evidenceFingerprint；
- 逻辑关系查询按 source、relation、target 分组去重，这与物理断言的唯一约束是两个层次；
- source 出边、target 入边、producer 范围和 sourceTask 分别建立索引；
- source 和 target 不能相同，由约束或服务层校验；
- `MENTIONS` 的 source 必须是 original、target 必须是 KnowledgeEntity；其他三种关系两端都必须是 KnowledgeEntity。

生命周期与 repository 统一为一套逻辑：

- 所有生产者通过 `upsert_relation_assertion` 写入，按 source/target 的列表和计数查询默认返回逻辑去重结果；
- 文档移动时，已解析关系继续依赖稳定 `fs_entry_id`，locator 按类型保留或更新可恢复值；
- 目标删除时不直接删边，而是将断言转为 broken，用 locator 回填可恢复值；目标恢复或重建后用 `KB_PATH` 或 `ENTITY_SURFACE` 重绑；
- 更新一份文档时只替换它拥有的全部出边，不删除其他文档的入边；
- Enrich 在一个事务中执行“物化旧令牌→删除 source 全部出边→重写 Markdown `MENTIONS`→写入 Enrich 关系”；
- Discovery 不修改文档正文，只替换同 source 上 `discovered_by='ENTITY_DISCOVERY'` 的 `MENTIONS`，不误删 Markdown 断言。
- Discovery 创建实体文档时将来源路径写为非链接文本，不由 Markdown Parser 产生实体到原始文档的反向 `MENTIONS`。

不在数据库中保存反向边；`PART_OF` 的“包含”、`IS_A` 的“具有实例/下位类型”和 `MENTIONS` 的“被提及于”由查询层派生。

### 11.5 v1 的轻量证据边界

当前版本不新增 `knowledge_document_relation_evidence`，也不向 `knowledge_file_reference` 填充大段 evidence JSON。先保留四层最低追溯能力：

- 断言行的 `discovered_by`、`producer_run_id`、`evidence_fingerprint` 标识生产者和证据出现；
- Markdown Parser 断言保存标题路径、行号和字符偏移；
- `source_task_id` 指向 Discovery/Enrich 生成任务；
- `knowledge_semantic_processing_task.result_payload` 保留有界的任务结果和丢弃原因，主要用于调试，不作为长期证据主数据；
- KnowledgeEntity Markdown 正文只展示非链接的原始来源路径；长期来源关系以原始文档到实体的 `MENTIONS` 为准。

代价是 v1 不保存证据片段正文，也不承诺证据 checksum 失效检测和长期关系审计。出现明确的审计、失效重算或多证据正文查询需求后，再增加独立证据投影表，不影响现有关系语义。

### 11.6 生产规模强烈建议：`knowledge_entity_surface`

PoC 可以从 metadata 中读取 `entityName` 和 `aliases` 后构建 AC snapshot。但全系统高性能匹配需要：

- 快速精确兜底；
- 别名增量更新；
- snapshot + delta；
- surface 到多个实体 postings 的查询；
- 不重复扫描 metadata EAV 和 stringList JSON。

因此生产规模建议新增可重建的实体词面投影表：

| 字段 | 说明 |
| --- | --- |
| `kid` | 投影 ID |
| `entity_fs_entry_id` | KnowledgeEntity 文档 ID |
| `surface_text` | 原始词面 |
| `normalized_surface` | 归一化词面 |
| `surface_type` | `CANONICAL`、`ALIAS`、`QUALIFIED` |
| `subject_fs_entry_id` | subject-local 主体，可空 |
| `definition_version` | 词面对应定义版本 |
| `is_deleted` | 逻辑删除 |
| `created_at`、`updated_at` | 审计时间 |

建议索引：

- `normalized_surface` 精确查询；
- `entity_fs_entry_id` 反查全部词面；
- `(subject_fs_entry_id, normalized_surface)` subject 查询；
- 活跃词面过滤索引。

该表不是 KnowledgeEntity 主数据：

- 权威名称和别名仍在文档 metadata；
- 该表可以从 metadata 全量重建；
- AC snapshot 从该投影构建；
- 投影或 snapshot 故障不能造成实体文档丢失。

### 11.7 v1 暂不新增

以下表在第一版不是必需项：

- `knowledge_document_processing_state`：可先通过任务表索引查询最近成功指纹；规模化调度出现瓶颈后再增加水位投影；
- `knowledge_processing_task_event`：Callback v1 不持久化逐事件日志，当前阶段和结果保存在任务表即可；
- `knowledge_document_relation`：复用扩展后的 `knowledge_file_reference`；
- `knowledge_document_relation_evidence`：v1 明确不建设独立关系证据层；
- `knowledge_document_redirect`：实体合并和拆分正式上线时再增加；
- 独立实体属性表或 ontology 表：不符合统一文档主模型。

## 12. 数据表最小落地组合

### v1 最小组合

```text
复用：knowledge_fs_entry
复用：metadata property/value
原样保留：knowledge_build_task（006）
原样保留：knowledge_build_task 索引（013）
新增：knowledge_semantic_processing_task（029）
增量扩展：knowledge_file_reference（030，原 026 不变）
不实现：knowledge_document_relation_evidence
AC snapshot：直接从 metadata 构建
```

### 生产推荐组合

```text
v1 最小组合
+ knowledge_entity_surface
+ AC immutable snapshot
+ delta surface index
```

因此，回答“是否需要增改数据表”：

> v1 新增一张 `knowledge_semantic_processing_task` 承载按文件的 Discovery/Enrich 任务，保持 `006_knowledge_build_task`、`013` 不变；通过增量脚本 `030` 把 `knowledge_file_reference` 扩展为 Markdown/Discovery/Enrich 共用的统一关系断言投影，原 `026` 不变；保存轻量位置证据，不实现独立 `knowledge_document_relation_evidence`。生产级全系统词表仍建议新增可重建的 `knowledge_entity_surface` 投影。`knowledge_fs_entry`、metadata 表和更新时间线不需要改结构。
