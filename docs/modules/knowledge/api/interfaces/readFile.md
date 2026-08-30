# readFile

## 功能描述

读取知识库中指定文本文件的内容和文件信息。接口适合 Agent 和业务调用方按路径查看 Markdown 等可读文本，不用于下载原始二进制文件。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/readFile` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

根据文件路径读取指定知识库下的文件内容，并以 Markdown 文本形式返回。若文件内容包含内部 Markdown 引用 token，响应会解析为用户可见路径；未解析或目标已删除的引用回退为用户原始写法。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需读取的文件全路径，以 `/` 开头，不包括知识库名称 |
| `startLine` | integer | 否 | Markdown 起始行，默认不填表示全部读取 |
| `endLine` | integer | 否 | Markdown 结束行，默认不填表示全部读取 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf",
  "startLine": 1,
  "endLine": 20
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "1",
    "filePath": "/制度/人事/请假制度.pdf",
    "startLine": 1,
    "endLine": 20,
    "data": "# 请假制度\n\n第一条 适用范围\n...",
    "reachedEof": false
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 文件读取结果 |
| `resultObject.knCode` | string | 是 | 知识库编码 |
| `resultObject.filePath` | string | 是 | 文件完整路径 |
| `resultObject.startLine` | integer \| null | 是 | 本次返回内容的起始行；全量读取时为 `null` |
| `resultObject.endLine` | integer \| null | 是 | 本次返回内容的结束行；全量读取时为 `null` |
| `resultObject.data` | string | 是 | 本次读取的 Markdown 文本 |
| `resultObject.reachedEof` | boolean | 是 | 是否已读取到文件末尾 |

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 特殊逻辑

- 读取的是可用 Markdown 内容，不是原始二进制文件。
- `startLine` 和 `endLine` 用于行范围读取；不传时返回全文。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
