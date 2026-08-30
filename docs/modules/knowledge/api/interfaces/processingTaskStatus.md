# processingTaskStatus

## 功能描述

查询单个实体处理文件任务的运行状态和结果。接口用于获取任务阶段、Worker 执行信息、完成结果或失败原因。

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

## 请求示例（全库）

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

## 请求示例（单文件）

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

## 成功响应示例

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
        }
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示查询成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 文件任务分页结果 |
| `resultObject.knowledgeBaseId` | string | 是 | 内部知识库 ID |
| `resultObject.knCode` | string | 是 | 知识库编码 |
| `resultObject.filePath` | string | 否 | 请求指定的文件路径；全库查询时省略 |
| `resultObject.total` | integer | 是 | 匹配任务总数 |
| `resultObject.pageNum` | integer | 是 | 当前页码 |
| `resultObject.pageSize` | integer | 是 | 每页任务数 |
| `resultObject.data` | array[object] | 是 | 当前页任务；无匹配任务时为空数组 |
| `resultObject.data[].taskId` | string | 是 | 文件任务 ID |
| `resultObject.data[].batchId` | string | 否 | 所属批次 ID |
| `resultObject.data[].taskType` | string | 是 | 任务类型 |
| `resultObject.data[].status` | string | 是 | 任务状态 |
| `resultObject.data[].currentStage` | string | 否 | 当前或最终处理阶段 |
| `resultObject.data[].progress` | integer | 否 | 任务进度，范围 0～100 |
| `resultObject.data[].fileId` | string | 否 | 文件 ID |
| `resultObject.data[].filePath` | string | 是 | 文件路径 |
| `resultObject.data[].indexVersion` | string | 否 | 任务使用的输入索引版本 |
| `resultObject.data[].createdAt` | string | 是 | 任务创建时间，ISO 8601 格式 |
| `resultObject.data[].startedAt` | string | 否 | 任务开始时间 |
| `resultObject.data[].finishedAt` | string | 否 | 任务结束时间 |
| `resultObject.data[].result` | object | 否 | 成功或跳过任务的结果；仅 `includeDetails=true` 时返回 |
| `resultObject.data[].result.candidateCount` | integer | 否 | Discovery 候选实体数 |
| `resultObject.data[].result.entityCount` | integer | 否 | Discovery 有效实体数 |
| `resultObject.data[].result.anchoredCount` | integer | 否 | 锚定已有实体数 |
| `resultObject.data[].result.createdCount` | integer | 否 | 新建实体数 |
| `resultObject.data[].result.mergedAliasCount` | integer | 否 | 合并 alias 数 |
| `resultObject.data[].result.droppedCount` | integer | 否 | 丢弃候选数 |
| `resultObject.data[].result.items` | array[object] | 否 | Discovery 结果项 |
| `resultObject.data[].result.items[].action` | string | 是 | `ANCHORED`、`CREATED` 或 `DROPPED` |
| `resultObject.data[].result.items[].fileId` | string \| null | 否 | 锚定或新建实体文件 ID |
| `resultObject.data[].result.items[].filePath` | string \| null | 否 | 锚定或新建实体文件路径 |
| `resultObject.data[].result.items[].entityName` | string | 否 | 实体规范名称 |
| `resultObject.data[].result.items[].sourceLocation` | object | 否 | 实体在源文件中的代表位置 |
| `resultObject.data[].result.items[].sourceLocation.startLine` | integer | 是 | 证据起始行 |
| `resultObject.data[].result.items[].sourceLocation.endLine` | integer | 是 | 证据结束行 |
| `resultObject.data[].result.items[].sourceLocation.text` | string | 是 | 证据文本摘要 |
| `resultObject.data[].error` | object | 否 | 失败任务错误；仅 `includeDetails=true` 时返回 |
| `resultObject.data[].error.errorCode` | string | 是 | 任务错误码 |
| `resultObject.data[].error.message` | string | 是 | 任务错误说明 |

`includeDetails=false` 时省略 `result` 和 `error`。失败任务仍使用正常成功信封，任务项的 `status=FAILED`，并在启用明细时返回 `errorCode` 和 `message`。Discovery/Enrich 请求中已弃用的 `extraParams` 不会出现在任务状态中。

一次全库触发不额外创建“父任务”记录：所有文件任务共用 `batchId`。因此可按知识库查看整体任务面，也可叠加 `filePath` 或 `batchId` 精确收窄范围。

知识库存在但没有匹配任务时返回 `total=0` 和空 `data`，不是 `TASK_NOT_FOUND`；传入的 `filePath` 本身不存在时返回 `DOCUMENT_NOT_FOUND`。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

实际 `resultMsg` 会说明参数、资源状态或依赖失败原因；请勿只根据文案分支处理。

## 特殊逻辑

- 查询无匹配任务时返回 `total=0` 和空 `data`，不是任务错误。
- `includeDetails=false` 时省略可能较大的 `result/error`。
- 响应不包含 `extraParams` 或 `extra_params`。
- 失败是 task 的正常业务终态，HTTP 仍返回成功信封。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
