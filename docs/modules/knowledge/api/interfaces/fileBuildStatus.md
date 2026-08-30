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
    "status": "running",
    "currentStep": "vectorizing",
    "statusDict": [
      {
        "standDisplayValue": "已完成",
        "standCode": "complete",
        "standDisplayValueEn": "complete"
      },
      {
        "standDisplayValue": "失败",
        "standCode": "failed",
        "standDisplayValueEn": "failed"
      },
      {
        "standDisplayValue": "构建中",
        "standCode": "running",
        "standDisplayValueEn": "running"
      },
      {
        "standDisplayValue": "不支持构建",
        "standCode": "unsupported",
        "standDisplayValueEn": "unsupported"
      }
    ],
    "stepDict": [
      {
        "standDisplayValue": "原始文件转 Markdown",
        "standCode": "markdown",
        "standDisplayValueEn": "markdown"
      },
      {
        "standDisplayValue": "文档切片",
        "standCode": "chunking",
        "standDisplayValueEn": "chunking"
      },
      {
        "standDisplayValue": "切片向量化",
        "standCode": "vectorizing",
        "standDisplayValueEn": "vectorizing"
      },
      {
        "standDisplayValue": "已完成",
        "standCode": "complete",
        "standDisplayValueEn": "complete"
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 最新构建任务摘要 |
| `resultObject.status` | string | 是 | 构建状态 |
| `resultObject.currentStep` | string | 是 | 当前构建环节 |
| `resultObject.statusDict` | array[object] | 是 | 构建状态字典 |
| `resultObject.statusDict[].standCode` | string | 是 | 状态代码 |
| `resultObject.statusDict[].standDisplayValue` | string | 是 | 状态中文展示值 |
| `resultObject.statusDict[].standDisplayValueEn` | string | 是 | 状态英文展示值 |
| `resultObject.stepDict` | array[object] | 是 | 构建环节字典 |
| `resultObject.stepDict[].standCode` | string | 是 | 环节代码 |
| `resultObject.stepDict[].standDisplayValue` | string | 是 | 环节中文展示值 |
| `resultObject.stepDict[].standDisplayValueEn` | string | 是 | 环节英文展示值 |

`statusDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `complete` | 已完成 | complete |
| `failed` | 失败 | failed |
| `running` | 构建中 | running |
| `unsupported` | 不支持构建 | unsupported |

`unsupported` 表示该文件类型不在可构建类型范围内（见 `POST /api/v1/knowledgeItems/import` 的文件类型说明），构建在「原始文件转 Markdown」环节即结束，不会进入切片与向量化。

`stepDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `markdown` | 原始文件转 Markdown | markdown |
| `chunking` | 文档切片 | chunking |
| `vectorizing` | 切片向量化 | vectorizing |
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
- `complete/failed/running/unsupported` 是任务状态，`markdown/chunking/vectorizing/complete` 是阶段。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
