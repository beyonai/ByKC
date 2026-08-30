# fileToMarkdown

## 功能描述

将上传的单个文件解析并转换为 Markdown，适用于只需要格式转换而不写入知识库的场景。接口同步返回转换结果，不创建后续分块和索引任务。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/fileToMarkdown` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `multipart/form-data; boundary=...` | 请求体类型 |
| `Accept` | 否 | `text/markdown` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

上传一个原始文件，同步执行原始文件转 Markdown 流程，并以 Markdown 文件流形式返回转换结果。

该接口只执行文件转 Markdown，不会创建知识库文件、不创建构建任务、不执行文档切片、不执行向量化，也不会写入知识库索引。

请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `fileContent` | file | 是 | 需转换的原始文件二进制内容 |

文件类型检查规则：

- 服务端根据上传文件名的扩展名识别文件类型。
- 文件名为空、缺少扩展名或扩展名不受支持时，返回失败响应。
- 当前支持的文件类型：`txt`、`md`、`markdown`、`csv`、`pdf`、`docx`、`doc`、`pptx`、`ppt`、`xlsx`、`xls`。

表单示例：

```bash
curl -X POST http://localhost:8000/api/v1/fileToMarkdown \
  -F "fileContent=@./考勤制度.pdf" \
  -o 考勤制度.md
```

## 成功响应示例

- 响应体为 Markdown 文件流
- `Content-Type`：`application/octet-stream`
- `Content-Disposition`：`attachment; filename="<原文件名去扩展名>.md"`

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="考勤制度.md"

# 考勤制度

...
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| 响应体 | string | 是 | 转换后的 Markdown 正文 |
| `Content-Type` | string | 是 | 固定为 `application/octet-stream` |
| `Content-Disposition` | string | 是 | Markdown 下载文件名信息 |

成功响应不使用 JSON 信封，因此没有 `resultObject.data` 字段。

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "unsupported file type: exe. Supported types: csv, doc, docx, markdown, md, pdf, ppt, pptx, txt, xls, xlsx",
  "resultObject": {}
}
```

## 请求示例

```bash
curl -X POST \
  -H "Accept: text/markdown" \
  -F "fileContent=@example.docx" \
  http://localhost:8000/api/v1/fileToMarkdown
```

## 特殊逻辑

- 该接口同步转换并返回 Markdown 文件流，不把文件写入知识库。
- 成功响应不使用 JSON 信封。

---

[返回 API 导航](../README.md)
