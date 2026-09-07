# processingTaskStatus

> 完整设计见 [文件与目录后台构建设计](../../file-build-background-processing-design.md)。

## 功能描述

查询实体发现、实体补全或文件构建任务的运行状态与结果。接口既支持按 `taskId` 精确定位，也支持按知识库、文件、batch、任务类型和状态分页查询。

Build 任务以 `fileId` 为稳定文件标识；`filePathSnapshot` 只记录受理时路径，文件移动或重命名后不会改写，也不得用于精确关联。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/processingTaskStatus` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

## 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `taskId` | string | 否 | - | 精确任务 ID；使用时必须同时传 `taskType`，并校验任务属于 `knCode` |
| `fileId` | string | 否 | - | 稳定文件 ID；优先于路径用于查询文件任务历史 |
| `filePath` | string | 否 | - | 兼容查询条件；先按当前路径解析文件 ID，再查询任务 |
| `taskType` | string | 否 | 全部 | `ENTITY_DISCOVERY`、`DOCUMENT_ENRICH` 或 `FILE_BUILD` |
| `batchId` | string | 否 | - | 只查询指定 batch 的任务 |
| `statusList` | string[] | 否 | 全部 | 任务状态过滤 |
| `latestOnly` | boolean | 否 | `true` | 是否只返回每个文件、每种任务类型的最新记录 |
| `includeDetails` | boolean | 否 | `false` | 是否返回 `result`、`error` 和 Build 配置快照 |
| `pageNum` | integer | 否 | `1` | 页码，从 1 开始 |
| `pageSize` | integer | 否 | `50` | 每页数量，范围 1～500 |

`taskId`、`batchId`、`fileId` 和 `filePath` 可以单独或组合使用；组合时取交集。Semantic 与 Build 使用独立任务表，数值 ID 可能相同，因此 `taskId` 必须与 `taskType` 组合后才是精确标识。查询文件历史优先使用 `fileId`，`filePath` 只作为兼容条件。

## 请求示例

按任务 ID 精确查询：

```json
{
  "knCode": "1",
  "taskId": "12001",
  "taskType": "FILE_BUILD",
  "includeDetails": true
}
```

按文件查询 Build 历史：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf",
  "taskType": "FILE_BUILD",
  "latestOnly": false,
  "pageNum": 1,
  "pageSize": 20
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knowledgeBaseId": "11",
    "knCode": "1",
    "total": 1,
    "pageNum": 1,
    "pageSize": 50,
    "data": [
      {
        "taskId": "12001",
        "batchId": "fb-20260903-0001",
        "taskType": "FILE_BUILD",
        "origin": "API",
        "executionMode": "BACKGROUND",
        "parentSemanticTaskId": null,
        "status": "SUCCEEDED",
        "currentStage": "COMMITTING",
        "progress": 100,
        "fileId": "2048",
        "filePathSnapshot": "/制度/人事/请假制度.pdf",
        "inputChecksum": "sha256:2e6f...",
        "inputIsDeleted": false,
        "buildProfile": {
          "profileVersion": 1,
          "parser": {"name": "default", "version": "2"},
          "chunking": {"size": 800, "overlap": 100},
          "embedding": {"model": "bge-m3", "dimension": 1024},
          "retrieval": {"schemaVersion": 1}
        },
        "buildProfileHash": "9c00f23d...",
        "outcomeUncertain": false,
        "createdAt": "2026-09-03T10:00:00+08:00",
        "startedAt": "2026-09-03T10:00:01+08:00",
        "finishedAt": "2026-09-03T10:00:08+08:00",
        "result": {
          "lineCount": 128,
          "chunkCount": 8
        }
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | `0` 表示查询成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 文件任务分页结果 |
| `resultObject.knowledgeBaseId` | string | 是 | 内部知识库 ID |
| `resultObject.knCode` | string | 是 | 知识库编码 |
| `resultObject.filePath` | string | 否 | 请求提供的兼容路径条件 |
| `resultObject.total` | integer | 是 | 匹配任务数 |
| `resultObject.pageNum` | integer | 是 | 当前页码 |
| `resultObject.pageSize` | integer | 是 | 每页数量 |
| `resultObject.data` | array[object] | 是 | 任务列表 |
| `resultObject.data[].taskId` | string | 是 | 任务 ID |
| `resultObject.data[].batchId` | string \| null | 否 | 外部 Build/Entity batch；INLINE Build 为 `null` |
| `resultObject.data[].taskType` | string | 是 | 任务类型 |
| `resultObject.data[].origin` | string | 否 | Build 来源：`API`、`ENTITY_DISCOVERY` 或 `ENTITY_ENRICH` |
| `resultObject.data[].executionMode` | string | 否 | Build 执行方式：`BACKGROUND` 或 `INLINE` |
| `resultObject.data[].parentSemanticTaskId` | string \| null | 否 | INLINE Build 的语义父任务 ID |
| `resultObject.data[].status` | string | 是 | 任务状态 |
| `resultObject.data[].currentStage` | string | 否 | 当前或最终阶段 |
| `resultObject.data[].progress` | integer | 否 | 0～100 的里程碑进度 |
| `resultObject.data[].fileId` | string | 否 | 稳定文件 ID |
| `resultObject.data[].filePath` | string | 否 | Entity 任务的兼容路径字段 |
| `resultObject.data[].filePathSnapshot` | string | 否 | Build 受理路径快照，非权威 |
| `resultObject.data[].inputChecksum` | string | 否 | Build 受理时数据库 checksum |
| `resultObject.data[].inputIsDeleted` | boolean | 否 | Build 受理时删除状态 |
| `resultObject.data[].buildProfile` | object | 否 | 可读的实际构建配置；仅 `includeDetails=true` |
| `resultObject.data[].buildProfileHash` | string | 否 | 规范化配置的 SHA-256；仅 `includeDetails=true` |
| `resultObject.data[].outcomeUncertain` | boolean | 否 | Worker 丢失或超时前是否可能已产生副作用 |
| `resultObject.data[].indexVersion` | string | 否 | Entity 任务输入索引版本 |
| `resultObject.data[].createdAt` | string | 是 | 创建时间 |
| `resultObject.data[].startedAt` | string | 否 | 开始时间 |
| `resultObject.data[].finishedAt` | string | 否 | 结束时间 |
| `resultObject.data[].result` | object | 否 | `includeDetails=true` 时的任务结果 |
| `resultObject.data[].result.candidateCount` | integer | 否 | Discovery 候选实体数 |
| `resultObject.data[].result.entityCount` | integer | 否 | Discovery 有效实体数 |
| `resultObject.data[].result.anchoredCount` | integer | 否 | Discovery 锚定已有实体数 |
| `resultObject.data[].result.createdCount` | integer | 否 | Discovery 新建实体数 |
| `resultObject.data[].result.mergedAliasCount` | integer | 否 | Discovery 合并 alias 数 |
| `resultObject.data[].result.droppedCount` | integer | 否 | Discovery 丢弃候选数 |
| `resultObject.data[].result.items` | array[object] | 否 | Discovery 结果项 |
| `resultObject.data[].result.items[].action` | string | 是 | `ANCHORED`、`CREATED` 或 `DROPPED` |
| `resultObject.data[].result.items[].fileId` | string \| null | 否 | 锚定或新建实体文件 ID |
| `resultObject.data[].result.items[].filePath` | string \| null | 否 | 锚定或新建实体文件路径 |
| `resultObject.data[].result.items[].entityName` | string | 否 | 实体规范名称 |
| `resultObject.data[].result.items[].sourceLocation` | object | 否 | 实体在源文件中的代表位置 |
| `resultObject.data[].result.items[].sourceLocation.startLine` | integer | 是 | 证据起始行 |
| `resultObject.data[].result.items[].sourceLocation.endLine` | integer | 是 | 证据结束行 |
| `resultObject.data[].result.items[].sourceLocation.text` | string | 是 | 证据文本摘要 |
| `resultObject.data[].error` | object | 否 | `includeDetails=true` 时的失败、跳过或不支持原因 |
| `resultObject.data[].error.errorCode` | string | 是 | 原因码 |
| `resultObject.data[].error.message` | string | 是 | 已截断、脱敏的说明 |

## Build 状态与阶段

Build 状态为 `PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`SKIPPED`、`UNSUPPORTED`；本期没有 `CANCELLED`。

| `currentStage` | `progress` | 含义 |
| --- | ---: | --- |
| `ACCEPTED` | 0 | 已入库 |
| `EXTRACTING` | 10 | 原文件转 Markdown |
| `CHUNKING` | 35 | 正文分块 |
| `EMBEDDING` | 55 | 生成向量 |
| `COMMITTING` | 90 | 最终校验与提交 |
| 任意终态 | 100 | 已结束 |

`SKIPPED` 原因包括 `INPUT_STALE`、`SOURCE_DELETED`、`SUPERSEDED` 和 `KNOWLEDGE_BASE_DELETED`。`FAILED` 原因包括 `TASK_TIMEOUT`、`WORKER_LOST` 和执行异常；前两者设置 `outcomeUncertain=true`，不会自动重试。

`buildProfileHash` 由完整默认值补齐后的强类型 `buildProfile` 生成：递归按 key 排序，使用固定 UTF-8、分隔符和数值格式，拒绝 `NaN/Infinity`。不得依赖 JSONB 的物理 key 顺序。

## 查询语义

- 无匹配任务返回 `total=0` 和空 `data`，不是 `TASK_NOT_FOUND`。
- 传入的 `filePath` 本身不存在时返回 `DOCUMENT_NOT_FOUND`。
- `includeDetails=false` 时省略可能较大的 `result`、`error`、`buildProfile` 和 `buildProfileHash`。
- Build 历史暂不清理；文件更新不能删除历史任务。
- `latestOnly=true` 按 `fileId + taskType` 选择最新记录，不以文件名或路径分组。
- 同时传 `fileId` 和 `filePath` 时，两者必须解析到同一当前文件，否则请求失败。
- Discovery/Enrich 现有字段与业务行为不因增加 `FILE_BUILD` 而改变。
- Discovery/Enrich 请求中已弃用的 `extraParams/extra_params` 不进入任务状态响应。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

---

[返回 API 导航](../README.md)
