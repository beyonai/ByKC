# downloadFile

## 功能描述

下载知识库中指定文件的原始二进制内容。接口根据知识库和文件路径定位对象存储文件，成功时直接返回文件流，而不是统一 JSON 响应信封。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/downloadFile` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/octet-stream` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

根据文件路径下载指定知识库下的文件。非 Markdown 文件返回入库字节；Markdown 文件入库时会被 token 化，系统不保留用户最初上传的原始 Markdown 字节，因此下载时返回已解析为用户可见路径的 Markdown 内容。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需下载的文件全路径，以 `/` 开头，不包括知识库名称 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf"
}
```

## 成功响应示例

- `200 OK`
- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename="..."` 或带 `filename*`
- 响应体为文件字节流；Markdown 文件为解析后的 Markdown 字节流

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| 响应体 | binary | 是 | 文件二进制字节流 |
| `Content-Type` | string | 是 | 文件的媒体类型 |
| `Content-Disposition` | string | 是 | 下载文件名信息 |

成功响应不使用 JSON 信封，因此没有 `resultObject.data` 字段。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 特殊逻辑

- 成功时直接返回原始字节流，不使用 JSON 响应信封。
- `Content-Disposition` 使用安全处理后的文件名，`Content-Type` 优先使用存储的 MIME 类型。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
