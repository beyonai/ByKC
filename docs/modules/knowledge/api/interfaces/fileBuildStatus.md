# fileBuildStatus

## 功能描述

查询指定文件知识构建任务的当前状态和阶段进度。调用方可用它轮询解析、分块、向量化及索引流程是否完成或失败。

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

文档构建状态查询。复用 `FileController.fileListByUser` 对应的状态查询链路。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码，对应 `agt_resource.resource_id` |
| `filePath` | string | 是 | 文件全路径，最后一级为文件名 |

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
    "status": "processing",
    "currentStep": "vectorizing",
    "currentStepStatus": "running",
    "statusDict": [
      {
        "standDisplayValue": "处理中",
        "standCode": "processing",
        "standDisplayValueEn": "Processing"
      },
      {
        "standDisplayValue": "成功",
        "standCode": "success",
        "standDisplayValueEn": "Success"
      },
      {
        "standDisplayValue": "失败",
        "standCode": "failed",
        "standDisplayValueEn": "Failed"
      }
    ],
    "stepDict": [
      {
        "standDisplayValue": "原始文件转 Markdown",
        "standCode": "markdown",
        "standDisplayValueEn": "Markdown"
      },
      {
        "standDisplayValue": "文档切片",
        "standCode": "chunking",
        "standDisplayValueEn": "Chunking"
      },
      {
        "standDisplayValue": "切片向量化",
        "standCode": "vectorizing",
        "standDisplayValueEn": "Vectorizing"
      }
    ]
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 构建状态 |
| `currentStep` | string | 当前环节 |
| `currentStepStatus` | string | 当前环节状态 |
| `statusDict` | array[object] | 状态字典 |
| `stepDict` | array[object] | 环节字典 |

`statusDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `processing` | 处理中 | Processing |
| `success` | 成功 | Success |
| `failed` | 失败 | Failed |
| `unsupported` | 不支持构建 | Unsupported |

`unsupported` 表示该文件类型不在可构建类型范围内（见 `POST /api/v1/knowledgeItems/import` 的文件类型说明），构建在「原始文件转 Markdown」环节即结束，不会进入切片与向量化。

`stepDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `markdown` | 原始文件转 Markdown | Markdown |
| `chunking` | 文档切片 | Chunking |
| `vectorizing` | 切片向量化 | Vectorizing |
| `complete` | 已完成 | complete |

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 特殊逻辑

- 返回文件最新一次构建任务的摘要状态。
- `processing/success/failed/unsupported` 是任务状态，`markdown/chunking/vectorizing/complete` 是阶段。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
