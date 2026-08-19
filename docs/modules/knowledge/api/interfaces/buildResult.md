# buildResult

## 功能描述

查询指定文件最近一次知识构建任务的最终产物与处理结果。调用方可在构建任务完成后，通过该接口获取 Markdown、分块及索引阶段形成的结果信息。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/buildResult` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

查询指定文件最新一次构建的完整结果，包括构建状态、转换后的 Markdown、切片分页、向量化覆盖率和检索索引覆盖率。

注意：

- 接口查询的是文件最新一次构建任务，不返回历史构建任务列表。
- 文件必须至少触发过一次知识构建，否则返回 `build task not found`。
- `includeMarkdown=false` 时不读取 Markdown 正文，适合只查看构建状态和切片统计的场景。

请求体：`application/json`

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 知识库编码 |
| `filePath` | string | 是 | - | 文件全路径，以 `/` 开头，不包括知识库名称 |
| `chunkPage` | integer | 否 | `1` | 切片页码，从 `1` 开始 |
| `chunkPageSize` | integer | 否 | `20` | 每页切片数量，范围为 `1`～`100` |
| `includeMarkdown` | boolean | 否 | `true` | 是否在响应中返回完整 Markdown 正文 |

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
    "filePath": "/汇报/年度总结.pptx",
    "fileName": "年度总结.pptx",
    "fileType": "pptx",
    "fileSize": 859923,
    "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "build": {
      "status": "complete",
      "currentStep": "complete",
      "errorMessage": null,
      "startedAt": "2026-08-04T09:56:14.989000+00:00",
      "finishedAt": "2026-08-04T09:56:16.239000+00:00",
      "durationMs": 1250,
      "statusDict": [
        {
          "standCode": "complete",
          "standDisplayValue": "已完成",
          "standDisplayValueEn": "complete"
        },
        {
          "standCode": "failed",
          "standDisplayValue": "失败",
          "standDisplayValueEn": "failed"
        },
        {
          "standCode": "running",
          "standDisplayValue": "构建中",
          "standDisplayValueEn": "running"
        },
        {
          "standCode": "unsupported",
          "standDisplayValue": "不支持构建",
          "standDisplayValueEn": "unsupported"
        }
      ],
      "stepDict": [
        {
          "standCode": "markdown",
          "standDisplayValue": "原始文件转 Markdown",
          "standDisplayValueEn": "markdown"
        },
        {
          "standCode": "chunking",
          "standDisplayValue": "文档切片",
          "standDisplayValueEn": "chunking"
        },
        {
          "standCode": "vectorizing",
          "standDisplayValue": "切片向量化",
          "standDisplayValueEn": "vectorizing"
        },
        {
          "standCode": "complete",
          "standDisplayValue": "已完成",
          "standDisplayValueEn": "complete"
        }
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

主要响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `build` | object | 最新构建任务的状态、当前步骤、错误信息、开始/完成时间和耗时 |
| `markdown.available` | boolean | 是否存在已生成的 Markdown 文件 |
| `markdown.data` | string/null | Markdown 正文；`includeMarkdown=false` 时为 `null` |
| `markdown.lineCount` | integer | Markdown 行数 |
| `markdown.characterCount` | integer/null | Markdown 字符数；未读取正文时为 `null` |
| `markdown.byteCount` | integer/null | Markdown UTF-8 字节数；未读取正文时为 `null` |
| `chunks.data` | array[object] | 当前页切片，包含行号、正文、向量和索引状态 |
| `chunks.total` | integer | 文件切片总数 |
| `chunks.reachedEof` | boolean | 当前页是否已经到达最后一页 |
| `embedding.coverageRate` | number | 已生成向量的切片占比，单位为百分比 |
| `retrieval.coverageRate` | number | 已进入检索索引的切片占比，单位为百分比 |

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "build task not found: /汇报/年度总结.pptx",
  "resultObject": {}
}
```

## 特殊逻辑

- 以最新构建任务为主线，同时返回 Markdown 可用性、切片分页、向量和检索覆盖率。
- `includeMarkdown=false` 时不读取完整正文，`markdown.data` 为 `null`。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
