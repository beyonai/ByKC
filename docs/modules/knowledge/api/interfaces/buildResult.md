# buildResult

> 设计状态：本文包含后台构建改造后的兼容契约，业务代码尚未切换到该实现。
> 精确任务历史应使用 [processingTaskStatus](processingTaskStatus.md)。

## 功能描述

按当前文件路径解析 `fileId`，返回该文件最新一次 Build task、当前文件快照以及当前可见的 Markdown、分块、向量和检索投影。接口不返回历史任务列表。

`isBuilt` 是当前文件是否完整构建的权威判断，不能只看曾经是否有成功任务。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/buildResult` |

## 请求参数

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `filePath` | string | 是 | - | 当前文件完整路径 |
| `chunkPage` | integer | 否 | `1` | 切片页码，从 1 开始 |
| `chunkPageSize` | integer | 否 | `20` | 每页切片数量，范围 1～100 |
| `includeMarkdown` | boolean | 否 | `true` | 是否返回 Markdown 正文 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/汇报/年度总结.pptx",
  "chunkPage": 1,
  "chunkPageSize": 20,
  "includeMarkdown": true
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "1",
    "fileId": "2048",
    "filePath": "/汇报/年度总结.pptx",
    "fileName": "年度总结.pptx",
    "fileType": "pptx",
    "fileSize": 859923,
    "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "currentChecksum": "sha256:2e6f...",
    "isBuilt": true,
    "build": {
      "taskId": "12001",
      "batchId": "fb-20260903-0001",
      "origin": "API",
      "executionMode": "BACKGROUND",
      "status": "complete",
      "currentStep": "complete",
      "errorCode": null,
      "errorMessage": null,
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
      "startedAt": "2026-09-03T01:56:14.989000Z",
      "finishedAt": "2026-09-03T01:56:16.239000Z",
      "durationMs": 1250,
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
    },
    "markdown": {
      "available": true,
      "data": "# 年度总结\n\n## 第一部分\n...",
      "lineCount": 128,
      "characterCount": 4047,
      "byteCount": 9731
    },
    "chunks": {
      "data": [
        {
          "chunkNo": 1,
          "startLine": 1,
          "endLine": 18,
          "content": "# 年度总结\n\n## 第一部分\n...",
          "characterCount": 526,
          "hasEmbedding": true,
          "retrievalIndexed": true
        }
      ],
      "page": 1,
      "pageSize": 20,
      "total": 8,
      "reachedEof": true
    },
    "embedding": {
      "dimension": 1024,
      "embeddedChunkCount": 8,
      "coverageRate": 100.0
    },
    "retrieval": {
      "indexedChunkCount": 8,
      "coverageRate": 100.0
    }
  }
}
```

## 关键响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | `0` 表示查询成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 文件最新任务与当前产物 |
| `resultObject.knCode` | string | 是 | 知识库编码 |
| `resultObject.fileId` | string | 是 | 稳定文件 ID |
| `resultObject.filePath` | string | 是 | 查询时的当前路径 |
| `resultObject.fileName` | string | 是 | 当前文件名 |
| `resultObject.fileType` | string | 是 | 扩展类型，不含点号 |
| `resultObject.fileSize` | integer | 是 | 文件字节数 |
| `resultObject.mimeType` | string \| null | 是 | 文件媒体类型 |
| `resultObject.currentChecksum` | string | 是 | 当前数据库 checksum |
| `resultObject.isBuilt` | boolean | 是 | 当前文件是否满足完整构建判定 |
| `resultObject.build.taskId` | string | 是 | 最新 Build task ID |
| `resultObject.build.batchId` | string \| null | 是 | 外部 batch ID；INLINE Build 为 `null` |
| `resultObject.build.origin` | string | 是 | `API`、`ENTITY_DISCOVERY` 或 `ENTITY_ENRICH` |
| `resultObject.build.executionMode` | string | 是 | `BACKGROUND` 或 `INLINE` |
| `resultObject.build.status` | string | 是 | `running`、`complete`、`failed`、`skipped` 或 `unsupported` |
| `resultObject.build.currentStep` | string | 是 | `markdown`、`chunking`、`vectorizing` 或 `complete` |
| `resultObject.build.errorCode` | string \| null | 是 | 终态原因码 |
| `resultObject.build.errorMessage` | string \| null | 是 | 已截断、脱敏的错误说明 |
| `resultObject.build.inputChecksum` | string | 是 | 任务输入数据库 checksum |
| `resultObject.build.inputIsDeleted` | boolean | 是 | 任务输入删除状态 |
| `resultObject.build.buildProfile` | object | 是 | 可读的实际构建配置快照 |
| `resultObject.build.buildProfileHash` | string | 是 | 规范化配置 SHA-256 |
| `resultObject.build.startedAt` | string \| null | 是 | 开始时间 |
| `resultObject.build.finishedAt` | string \| null | 是 | 结束时间 |
| `resultObject.build.durationMs` | integer \| null | 是 | 耗时毫秒数 |
| `resultObject.build.statusDict` | array[object] | 是 | 兼容状态字典 |
| `resultObject.build.statusDict[].standCode` | string | 是 | 状态代码 |
| `resultObject.build.statusDict[].standDisplayValue` | string | 是 | 中文展示值 |
| `resultObject.build.statusDict[].standDisplayValueEn` | string | 是 | 英文展示值 |
| `resultObject.build.stepDict` | array[object] | 是 | 兼容阶段字典 |
| `resultObject.build.stepDict[].standCode` | string | 是 | 阶段代码 |
| `resultObject.build.stepDict[].standDisplayValue` | string | 是 | 中文展示值 |
| `resultObject.build.stepDict[].standDisplayValueEn` | string | 是 | 英文展示值 |
| `resultObject.markdown.available` | boolean | 是 | 当前 Markdown 对象与数据库引用是否可用 |
| `resultObject.markdown.data` | string \| null | 是 | Markdown 正文；未请求或不可用时为 `null` |
| `resultObject.markdown.lineCount` | integer | 是 | 行数；不可用时为 0 |
| `resultObject.markdown.characterCount` | integer \| null | 是 | 字符数；未读取正文时为 `null` |
| `resultObject.markdown.byteCount` | integer \| null | 是 | Markdown UTF-8 字节数；未读取时为 `null` |
| `resultObject.chunks.data` | array[object] | 是 | 当前页分块 |
| `resultObject.chunks.data[].chunkNo` | integer | 是 | 分块序号 |
| `resultObject.chunks.data[].startLine` | integer | 是 | 起始行 |
| `resultObject.chunks.data[].endLine` | integer | 是 | 结束行 |
| `resultObject.chunks.data[].content` | string | 是 | 分块正文 |
| `resultObject.chunks.data[].characterCount` | integer | 是 | 分块字符数 |
| `resultObject.chunks.total` | integer | 是 | 当前分块总数 |
| `resultObject.chunks.data[].hasEmbedding` | boolean | 是 | 是否有向量 |
| `resultObject.chunks.data[].retrievalIndexed` | boolean | 是 | 是否进入检索投影 |
| `resultObject.chunks.page` | integer | 是 | 当前页码 |
| `resultObject.chunks.pageSize` | integer | 是 | 每页数量 |
| `resultObject.chunks.reachedEof` | boolean | 是 | 是否到最后一页 |
| `resultObject.embedding.dimension` | integer | 是 | 向量维度 |
| `resultObject.embedding.embeddedChunkCount` | integer | 是 | 已生成向量的分块数 |
| `resultObject.embedding.coverageRate` | number | 是 | 向量覆盖率百分比 |
| `resultObject.retrieval.indexedChunkCount` | integer | 是 | 已进入检索投影的分块数 |
| `resultObject.retrieval.coverageRate` | number | 是 | 检索覆盖率百分比 |

## `isBuilt` 判定

仅当以下条件同时满足时为 `true`：

1. 最新 Build task 为 `SUCCEEDED`；
2. `build.inputChecksum` 等于当前文件的数据库 `currentChecksum`；
3. 当前文件未删除；
4. Markdown、分块、向量和检索投影均完整存在。

Build Profile 变化不会自动触发重建，也不单独令 `isBuilt=false`；它只影响下一次显式构建请求是否复用。显式开始新构建后会先清理旧派生数据，因此新任务失败时 `isBuilt=false`，不会继续暴露旧协议产物为可用构建结果。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "build task not found: /汇报/年度总结.pptx",
  "resultObject": {}
}
```

文件不存在或从未产生 Build task 时返回失败信封。已有任务但构建失败、跳过或不支持时仍返回成功信封，并通过 `build.status`、`errorCode`、`isBuilt=false` 和空产物表达状态。

## 路径与定位规则

- `filePath` 必须以 `/` 开头，不允许使用 `..` 越界。
- 路径只用于解析当前文件；跨移动或重命名的精确任务查询必须使用 `taskId` 或 `fileId`。

---

[返回 API 导航](../README.md)
