# processingBatchStatus

> 设计状态：`FILE_BUILD` 相关字段描述已经确认的目标契约，业务代码尚未切换到该实现。
> 完整设计见 [文件与目录后台构建设计](../../file-build-background-processing-design.md)。

## 功能描述

按 `knCode + batchId` 查询一次实体发现、实体补全或文件构建批次的聚合进度与分页任务明细。单个文件任务失败不会使 batch 失败；本批次新建的全部任务进入终态后，batch 统一为 `COMPLETED`。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/processingBatchStatus` |

## 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `batchId` | string | 是 | - | Discovery、Enrich 或 File Build 受理时返回的 batch ID |
| `includeDetails` | boolean | 否 | `false` | 是否在任务明细中返回 `result`、`error` 和 Build 配置快照 |
| `pageNum` | integer | 否 | `1` | 任务明细页码，从 1 开始 |
| `pageSize` | integer | 否 | `50` | 每页数量，范围 1～500 |

## 请求示例

```json
{
  "knCode": "1",
  "batchId": "fb-20260903-0001",
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
    "batchId": "fb-20260903-0001",
    "knowledgeBaseId": "11",
    "knCode": "1",
    "taskType": "FILE_BUILD",
    "scope": "DIRECTORY",
    "targetPath": "/制度/人事",
    "status": "COMPLETED",
    "version": 61,
    "candidateCount": 80,
    "eligibleCount": 78,
    "acceptedCount": 60,
    "reusedCount": 18,
    "acceptanceSkippedCount": 2,
    "totalCount": 60,
    "completedCount": 60,
    "pendingCount": 0,
    "runningCount": 0,
    "succeededCount": 55,
    "failedCount": 2,
    "skippedCount": 2,
    "unsupportedCount": 1,
    "progress": 100,
    "createdAt": "2026-09-03T10:00:00+08:00",
    "completedAt": "2026-09-03T10:02:30+08:00",
    "pageNum": 1,
    "pageSize": 50,
    "data": [
      {
        "taskId": "12001",
        "batchId": "fb-20260903-0001",
        "taskType": "FILE_BUILD",
        "status": "SUCCEEDED",
        "currentStage": "COMMITTING",
        "progress": 100,
        "fileId": "2048",
        "filePathSnapshot": "/制度/人事/请假制度.pdf",
        "inputChecksum": "sha256:2e6f...",
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
| `resultObject.batchId` | string | 是 | 批次 ID |
| `resultObject.knowledgeBaseId` | string | 是 | 内部知识库 ID |
| `resultObject.knCode` | string | 是 | 知识库编码 |
| `resultObject.taskType` | string | 是 | `ENTITY_DISCOVERY`、`DOCUMENT_ENRICH` 或 `FILE_BUILD` |
| `resultObject.scope` | string | 是 | `SINGLE_FILE`、`DIRECTORY` 或 `WHOLE_KB` |
| `resultObject.targetPath` | string | 否 | `FILE_BUILD` 请求目标路径快照；仅供审计和展示 |
| `resultObject.status` | string | 是 | `PENDING`、`PROCESSING` 或 `COMPLETED` |
| `resultObject.version` | integer | 是 | 批次聚合版本号 |
| `resultObject.candidateCount` | integer | 否 | `FILE_BUILD` 快照候选文件数 |
| `resultObject.eligibleCount` | integer | 否 | `FILE_BUILD` 新建任务数加复用命中数 |
| `resultObject.acceptedCount` | integer | 否 | `FILE_BUILD` 在本批次新建的任务数 |
| `resultObject.reusedCount` | integer | 否 | `FILE_BUILD` 命中的已有任务数 |
| `resultObject.acceptanceSkippedCount` | integer | 否 | `FILE_BUILD` 受理前跳过数；与终态 `skippedCount` 区分 |
| `resultObject.totalCount` | integer | 是 | 本 batch 真正创建的任务数；等于 Build 的 `acceptedCount` |
| `resultObject.completedCount` | integer | 是 | 已进入终态的新建任务数 |
| `resultObject.pendingCount` | integer | 是 | 待处理任务数 |
| `resultObject.runningCount` | integer | 是 | 运行中任务数 |
| `resultObject.succeededCount` | integer | 是 | 成功任务数 |
| `resultObject.failedCount` | integer | 是 | 失败任务数 |
| `resultObject.skippedCount` | integer | 是 | 已创建任务中进入 `SKIPPED` 的数量 |
| `resultObject.unsupportedCount` | integer | 是 | `UNSUPPORTED` 数量；非 Build 批次固定为 0 |
| `resultObject.progress` | integer | 是 | 批次进度，范围 0～100 |
| `resultObject.createdAt` | string | 是 | 创建时间，ISO 8601 |
| `resultObject.completedAt` | string | 否 | 完成时间，未完成时省略 |
| `resultObject.pageNum` | integer | 是 | 当前页码 |
| `resultObject.pageSize` | integer | 是 | 每页数量 |
| `resultObject.data` | array[object] | 是 | 本批次新建的任务；不包含 Reuse Hit |
| `resultObject.data[].taskId` | string | 是 | 任务 ID |
| `resultObject.data[].batchId` | string | 否 | 所属 batch；Build 外部任务必返 |
| `resultObject.data[].taskType` | string | 是 | 任务类型 |
| `resultObject.data[].status` | string | 是 | 任务状态 |
| `resultObject.data[].currentStage` | string | 否 | 当前或最终阶段 |
| `resultObject.data[].progress` | integer | 否 | 任务进度，范围 0～100 |
| `resultObject.data[].fileId` | string | 否 | 稳定文件 ID |
| `resultObject.data[].filePath` | string | 否 | Entity 任务的兼容路径字段 |
| `resultObject.data[].filePathSnapshot` | string | 否 | Build 受理路径快照，不作为机器标识 |
| `resultObject.data[].inputChecksum` | string | 否 | Build 受理时的数据库 checksum |
| `resultObject.data[].indexVersion` | string | 否 | Entity 任务输入索引版本 |
| `resultObject.data[].createdAt` | string | 是 | 创建时间 |
| `resultObject.data[].startedAt` | string | 否 | 开始时间 |
| `resultObject.data[].finishedAt` | string | 否 | 结束时间 |
| `resultObject.data[].result` | object | 否 | `includeDetails=true` 时的结果 |
| `resultObject.data[].result.<fieldName>` | any | 否 | 任务类型对应的结果字段，例如 Discovery 的 `createdCount` 或 Build 的 `chunkCount` |
| `resultObject.data[].error` | object | 否 | `includeDetails=true` 时的错误或跳过原因 |
| `resultObject.data[].error.errorCode` | string | 是 | 原因码 |
| `resultObject.data[].error.message` | string | 是 | 已截断、脱敏的说明 |

## 聚合规则

```text
candidateCount = acceptedCount + reusedCount + acceptanceSkippedCount
eligibleCount = acceptedCount + reusedCount
totalCount = acceptedCount
completedCount = succeededCount + failedCount + skippedCount + unsupportedCount
```

- Build 的 Reuse Hit 不写入当前 batch 的任务集合，不影响其终态计数。
- 空目录、全受理前跳过或纯复用 batch 直接为 `COMPLETED`，`totalCount=0`。
- batch 没有 `FAILED` 状态；即使 `failedCount > 0`，全部新建任务终结后仍为 `COMPLETED`。
- `PENDING` 是 Build 新增的受理后状态；现有 Entity batch 继续使用 `PROCESSING/COMPLETED`。
- `DIRECTORY` 可用于 Build 和 Entity Discovery；Enrich 的现有 scope 语义不变。
- `includeDetails=false` 时省略 task 的 `result`、`error`、`buildProfile` 和 `buildProfileHash`。
- Discovery/Enrich 请求中已弃用的 `extraParams/extra_params` 不进入 batch 或状态响应。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "batch not found: fb-20260903-0001",
  "resultObject": {}
}
```

---

[返回 API 导航](../README.md)
