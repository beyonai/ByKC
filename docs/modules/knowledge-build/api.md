# 知识构建模块接口说明

## 文档范围

本文档描述 `by_qa` 开源仓库当前已经实现并对外暴露的知识构建 API。

补充说明：

- Base URL：`/api/v1`
- 协议：`HTTP`，3 个接口均使用 `JSON`
- 当前实现入口：`src/by_qa/main.py`
- 路由定义：`src/by_qa/knowledge_build/api/routes.py`
- 请求/响应模型：`src/by_qa/knowledge_build/api/schemas.py`

说明：

- 本文档以当前开源仓库真实实现为准
- 当前 `knowledge_build` 更偏向示例实现，适合本地联调、协议参考和轻量构建验证
- 文档解析、切片和 embedding 构建能力由 `DocumentChunkingService` 提供，代码位于 `src/by_qa/knowledge_build/services/document_chunking_service.py`

## 通用约定

### 成功响应

3 个接口统一返回以下成功信封：

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {}
}
```

其中：

- `code`：HTTP 状态码
- `message`：成功时固定为 `success`
- `error`：成功时固定为 `null`
- `data`：业务返回体

### 失败响应

知识构建 API 使用统一错误信封：

```json
{
  "code": 422,
  "message": "error",
  "data": null,
  "error": {
    "type": "business_validation",
    "error_code": "FILE_TYPE_UNSUPPORTED",
    "error_message": "xxx",
    "details": {}
  }
}
```

常见错误类型：

- `request_invalid`：请求体结构或字段校验失败
- `business_validation`：业务校验失败
- `configuration_error`：运行时配置缺失
- `dependency_error`：外部依赖不可用
- `internal_error`：未预期内部错误

### 请求校验错误

由 FastAPI/Pydantic 触发的请求校验错误统一返回：

```json
{
  "code": 422,
  "message": "error",
  "data": null,
  "error": {
    "type": "request_invalid",
    "error_code": "REQUEST_VALIDATION_FAILED",
    "error_message": "request validation failed",
    "details": {
      "errors": []
    }
  }
}
```

### 文件类型约定

`file-to-markdown` 和 `file-to-markdown-index` 使用请求字段 `type` 表示文件类型。

当前支持的类型：

- `txt`
- `md`
- `csv`
- `pdf`
- `docx`
- `pptx`
- `xlsx`

补充语义：

- `type` 大小写不敏感，服务端会先做 `strip().lower()` 归一化
- `markdown` 会在服务端统一映射到 `md`
- `content` 字段必须是合法的 base64 编码字符串
- `build-markdown-index` 不接收原始文件，只接收 markdown 文本

### Chunk 返回体约定

涉及 `chunks` 的接口统一返回 `KnowledgeItemChunkPayload` 列表，单个元素包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunk_no` | integer | chunk 序号，从 1 开始 |
| `start_line` | integer | chunk 起始行号 |
| `end_line` | integer | chunk 结束行号 |
| `chunk_text` | string | chunk 文本内容 |
| `embedding` | number[] | embedding 向量 |
| `char_start` | integer \| null | chunk 在原文本中的起始字符偏移 |
| `char_end` | integer \| null | chunk 在原文本中的结束字符偏移 |

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/file-to-markdown` | 将 base64 文件解析成 markdown 文本 |
| `POST` | `/api/v1/build-markdown-index` | 将 markdown 文本切片并生成 embedding |
| `POST` | `/api/v1/file-to-markdown-index` | 一步完成文件解析与切片构建 |

## 解析文件为 Markdown

### `POST /api/v1/file-to-markdown`

将上传的 base64 文件内容解析为 markdown 或纯文本内容。

补充语义：

- 只负责解析，不生成 chunks
- `type` 会做大小写不敏感处理
- 若文件类型受支持但解析器抛出异常，返回 `FILE_PARSE_FAILED`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `content` | string | 是 | 原始文件内容的 base64 字符串 |
| `type` | string | 是 | 文件类型，大小写不敏感 |

### 请求示例

```json
{
  "content": "IyBUaXRsZQoKYm9keQ==",
  "type": "Md"
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `md_content` | string | 解析后的 markdown 或纯文本 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "md_content": "# Title\n\nbody"
  }
}
```

### 典型错误码

- `FILE_TYPE_UNSUPPORTED`
- `FILE_CONTENT_INVALID`
- `FILE_PARSE_FAILED`
- `RUNTIME_CONFIG_ERROR`
- `REQUEST_VALIDATION_FAILED`

## 从 Markdown 构建索引

### `POST /api/v1/build-markdown-index`

将 markdown 文本切片并批量生成 embedding，返回 chunk payload 列表。

补充语义：

- 请求只接收 markdown 文本，不接收文件类型
- 内部固定以 `input.md` 作为文件名进入切片链路
- markdown 为空白、切片为空或抽取不到文本时，统一返回 `CHUNK_EMPTY`
- embedding 服务不可用时返回 `EMBEDDING_SERVICE_ERROR`

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `content` | string | 是 | markdown 文本 |

### 请求示例

```json
{
  "content": "# 员工手册\n\n## 请假制度\n\n员工请假需要提前提交审批。"
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `chunks` | array | chunk payload 列表 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "chunks": [
      {
        "chunk_no": 1,
        "start_line": 1,
        "end_line": 3,
        "chunk_text": "# 员工手册\n\n## 请假制度\n\n员工请假需要提前提交审批。",
        "embedding": [0.1, 0.2],
        "char_start": 0,
        "char_end": 29
      }
    ]
  }
}
```

### 典型错误码

- `CHUNK_EMPTY`
- `EMBEDDING_SERVICE_ERROR`
- `RUNTIME_CONFIG_ERROR`
- `INTERNAL_ERROR`
- `REQUEST_VALIDATION_FAILED`

## 一步式解析并构建索引

### `POST /api/v1/file-to-markdown-index`

先将 base64 文件解析成 markdown，再基于解析结果构建 chunk 与 embedding。

补充语义：

- 行为等价于先调用 `file-to-markdown`，再把 `md_content` 传给 `build-markdown-index`
- 如果解析阶段失败，接口会直接返回解析错误，不继续进入切片阶段
- 如果切片或 embedding 阶段失败，接口返回对应的构建错误，不返回部分成功结果

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `content` | string | 是 | 原始文件内容的 base64 字符串 |
| `type` | string | 是 | 文件类型，大小写不敏感 |

### 请求示例

```json
{
  "content": "bmFtZSxhZ2UKYWxpY2UsMTgK",
  "type": "CSV"
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `md_content` | string | 解析后的 markdown 或纯文本 |
| `chunks` | array | chunk payload 列表 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "md_content": "name | age\nalice | 18",
    "chunks": [
      {
        "chunk_no": 1,
        "start_line": 1,
        "end_line": 2,
        "chunk_text": "name | age\nalice | 18",
        "embedding": [0.1, 0.2],
        "char_start": 0,
        "char_end": 21
      }
    ]
  }
}
```

### 典型错误码

- `FILE_TYPE_UNSUPPORTED`
- `FILE_CONTENT_INVALID`
- `FILE_PARSE_FAILED`
- `CHUNK_EMPTY`
- `EMBEDDING_SERVICE_ERROR`
- `RUNTIME_CONFIG_ERROR`
- `INTERNAL_ERROR`
- `REQUEST_VALIDATION_FAILED`
