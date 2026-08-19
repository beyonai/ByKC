# fileToMarkdownIndex

## 功能描述

为知识库中的指定文件发起完整知识构建，包括转 Markdown、分块、向量化和索引。接口异步受理任务，处理进度和最终结果需通过构建查询接口获取。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/fileToMarkdownIndex` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

异步触发指定知识库下文件的构建任务。接口会先检查当前文件是否已存在构建中的任务，再决定是否受理新的构建请求。

后台处理规则：

1. 如果对应文件已存在未完成的构建任务，则不再重复触发构建，直接返回失败响应，`resultCode` 为 `"-1"`，`resultMsg` 返回错误提示。
2. 如果对应文件上一次构建失败，则重新触发构建。
3. 如果对应文件不存在未完成的构建任务，则触发构建流程，自动完成原始文件转 Markdown、切片和切片向量化处理。

构建进度和当前处理环节需要通过 `POST /api/v1/fileBuildStatus` 查询。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需构建的文档全路径，以 `/` 开头，不包括知识库名称 |

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
  "resultObject": {}
}
```

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "build task already exists for file: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 特殊逻辑

- 该接口只受理后台构建，不等待 Markdown、切片和向量化完成。
- 同文件已有活动构建任务时拒绝重复创建。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
