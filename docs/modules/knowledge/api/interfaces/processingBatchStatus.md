# processingBatchStatus

## 功能描述

查询一次实体发现或实体补全批次的整体进度。接口汇总批次内文件任务的待处理、运行、成功、跳过和失败数量，单文件失败不会自动使整个批次失败。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/processingBatchStatus` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

按 `knCode + batchId` 查询一次触发的整体进度和分页文件明细。请求支持 `includeDetails`、`pageNum`、`pageSize`。返回包含：

- `status`：`PROCESSING` 或 `COMPLETED`；
- `version`、`totalCount`、`completedCount` 和 0–100 的 `progress`；
- `pendingCount`、`runningCount`、`succeededCount`、`failedCount`、`skippedCount`；
- 分页 task 明细。请求中已弃用的 `extParams` 不进入 batch/task，也不在状态响应中返回。

单文件失败只增加 `failedCount`，batch 仍在所有文件进入终态后成为 `COMPLETED`。

## 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `batchId` | string | 是 | - | Discovery/Enrich 受理时返回的批次 ID |
| `includeDetails` | boolean | 否 | `false` | 是否在文件明细中返回 `result` 和 `error` |
| `pageNum` | integer | 否 | `1` | 文件明细页码，从 1 开始 |
| `pageSize` | integer | 否 | `50` | 每页数量，范围 1～500 |

## 请求示例

```json
{
  "knCode": "1",
  "batchId": "ed-20260817-0001",
  "includeDetails": true,
  "pageNum": 1,
  "pageSize": 50
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "batchId": "ed-20260817-0001",
    "knowledgeBaseId": "11",
    "knCode": "1",
    "taskType": "ENTITY_DISCOVERY",
    "scope": "WHOLE_KB",
    "status": "COMPLETED",
    "version": 12,
    "totalCount": 3,
    "completedCount": 3,
    "pendingCount": 0,
    "runningCount": 0,
    "succeededCount": 2,
    "failedCount": 1,
    "skippedCount": 0,
    "progress": 100,
    "createdAt": "2026-08-17T10:00:00+08:00",
    "completedAt": "2026-08-17T10:02:30+08:00",
    "pageNum": 1,
    "pageSize": 50,
    "data": [
      {
        "taskId": "9001",
        "batchId": "ed-20260817-0001",
        "taskType": "ENTITY_DISCOVERY",
        "status": "SUCCEEDED",
        "currentStage": "completed",
        "progress": 100,
        "fileId": "1024",
        "filePath": "/原始文档/A.md",
        "indexVersion": "ac/18",
        "createdAt": "2026-08-17T10:00:00+08:00",
        "startedAt": "2026-08-17T10:00:01+08:00",
        "finishedAt": "2026-08-17T10:00:08+08:00",
        "result": {"createdCount": 1}
      },
      {
        "taskId": "9002",
        "batchId": "ed-20260817-0001",
        "taskType": "ENTITY_DISCOVERY",
        "status": "FAILED",
        "currentStage": "failed",
        "progress": 100,
        "fileId": "1025",
        "filePath": "/原始文档/B.md",
        "createdAt": "2026-08-17T10:00:00+08:00",
        "startedAt": "2026-08-17T10:00:01+08:00",
        "finishedAt": "2026-08-17T10:00:12+08:00",
        "error": {
          "errorCode": "PROCESSING_FAILED",
          "message": "model failed"
        }
      }
    ]
  }
}
```

`includeDetails=false` 时，每个 task 明细不返回 `result` 和 `error`。

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

- batch 不是另一个父 task；它是一次触发及其文件 task 的聚合状态。
- 单文件失败只增加 `failedCount`，不会让其他文件失败。
- 全部文件进入终态后，batch 进入 `COMPLETED`，即使 `failedCount > 0`。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。

---

[返回 API 导航](../README.md)
