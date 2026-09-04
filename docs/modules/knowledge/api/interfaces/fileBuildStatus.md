# fileBuildStatus

> 新调用方应优先使用带 `taskId` 的 [processingTaskStatus](processingTaskStatus.md)。

## 功能描述

按当前文件路径查询与文件当前 checksum、删除态一致的最新一次知识构建任务摘要。该接口保留旧的状态和阶段值，并增加 `skipped` 与 `errorCode`；路径只用于把当前文件解析为稳定 `fileId`，不用于关联历史任务。

文件内容更新后，历史任务仍保留用于审计，但不再表示当前文件已经构建。此时若尚未重新构建，接口返回 `build task not found` 失败信封；重新构建完成后恢复返回 `complete`。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/fileBuildStatus` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

## 请求参数

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 当前文件完整路径 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "taskId": "12001",
    "fileId": "2048",
    "status": "skipped",
    "currentStep": "vectorizing",
    "errorCode": "INPUT_STALE",
    "statusDict": [
      {"standCode": "complete", "standDisplayValue": "已完成", "standDisplayValueEn": "complete"},
      {"standCode": "failed", "standDisplayValue": "失败", "standDisplayValueEn": "failed"},
      {"standCode": "running", "standDisplayValue": "构建中", "standDisplayValueEn": "running"},
      {"standCode": "skipped", "standDisplayValue": "已跳过", "standDisplayValueEn": "skipped"},
      {"standCode": "unsupported", "standDisplayValue": "不支持构建", "standDisplayValueEn": "unsupported"}
    ],
    "stepDict": [
      {"standCode": "markdown", "standDisplayValue": "原始文件转 Markdown", "standDisplayValueEn": "markdown"},
      {"standCode": "chunking", "standDisplayValue": "文档切片", "standDisplayValueEn": "chunking"},
      {"standCode": "vectorizing", "standDisplayValue": "切片向量化", "standDisplayValueEn": "vectorizing"},
      {"standCode": "complete", "standDisplayValue": "已完成", "standDisplayValueEn": "complete"}
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | `0` 表示查询成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 与当前文件内容匹配的最新 Build task 摘要 |
| `resultObject.taskId` | string | 是 | 最新 Build task ID |
| `resultObject.fileId` | string | 是 | 稳定文件 ID |
| `resultObject.status` | string | 是 | 兼容构建状态 |
| `resultObject.currentStep` | string | 是 | 兼容构建阶段 |
| `resultObject.errorCode` | string \| null | 是 | 失败、跳过或不支持原因；其他状态为 `null` |
| `resultObject.statusDict` | array[object] | 是 | 状态字典 |
| `resultObject.statusDict[].standCode` | string | 是 | 状态代码 |
| `resultObject.statusDict[].standDisplayValue` | string | 是 | 中文展示值 |
| `resultObject.statusDict[].standDisplayValueEn` | string | 是 | 英文展示值 |
| `resultObject.stepDict` | array[object] | 是 | 阶段字典 |
| `resultObject.stepDict[].standCode` | string | 是 | 阶段代码 |
| `resultObject.stepDict[].standDisplayValue` | string | 是 | 中文展示值 |
| `resultObject.stepDict[].standDisplayValueEn` | string | 是 | 英文展示值 |

## 状态映射

| Build task 状态 | `status` |
| --- | --- |
| `PENDING`、`RUNNING` | `running` |
| `SUCCEEDED` | `complete` |
| `FAILED` | `failed` |
| `SKIPPED` | `skipped` |
| `UNSUPPORTED` | `unsupported` |

| Build task 阶段或终态 | `currentStep` |
| --- | --- |
| `ACCEPTED`、`EXTRACTING` | `markdown` |
| `CHUNKING` | `chunking` |
| `EMBEDDING`、`COMMITTING` | `vectorizing` |
| `SUCCEEDED` | `complete` |

非成功终态保留任务结束前最后一个可映射阶段。`errorCode` 用于区分 `INPUT_STALE`、`SOURCE_DELETED`、`SUPERSEDED`、`KNOWLEDGE_BASE_DELETED`、`TASK_TIMEOUT`、`WORKER_LOST` 和 `UNSUPPORTED_FILE_TYPE` 等原因。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 路径与定位规则

- `filePath` 必须以 `/` 开头，不允许使用 `..` 越界。
- 文件移动或重命名后，旧路径不能再定位该文件；需要稳定查询时使用受理响应中的 `taskId` 或 `fileId`。

---

[返回 API 导航](../README.md)
