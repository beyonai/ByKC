# 知识库模块接口说明

## 文档范围

本文档描述 `by_qa` 开源仓库当前已经实现并对外暴露的知识库 API。

- Base URL：`/api/v1`
- 协议：`HTTP + JSON`
- 当前实现入口：`src/by_qa/main.py`
- 路由定义：`src/by_qa/knowledge_base/api/routes.py`
- 请求/响应模型：`src/by_qa/knowledge_base/api/schemas.py`

说明：

- 本文档以当前开源仓库真实实现为准
- 本仓库只提供知识库运行态 API，不提供文件解析、切片、embedding 构建这类预处理接口
- 若后续补充构建链路说明，建议放在 `examples` 或独立示例文档中，而不是作为服务端正式接口能力

## 通用约定

### 成功响应

大多数接口统一返回以下结构：

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

知识库 API 使用统一错误信封：

```json
{
  "code": 422,
  "message": "error",
  "data": null,
  "error": {
    "type": "business_validation",
    "error_code": "KB_WRITE_FILE_INVALID",
    "error_message": "xxx",
    "details": {}
  }
}
```

常见错误类型：

- `request_invalid`：请求体结构或字段校验失败
- `business_validation`：业务校验失败
- `not_found`：资源不存在
- `conflict`：资源冲突
- `configuration_error`：运行时配置缺失
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

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledge-bases/create` | 创建知识库 |
| `POST` | `/api/v1/knowledge-bases/delete` | 删除知识库 |
| `POST` | `/api/v1/write-file` | 写入原始文件 |
| `POST` | `/api/v1/write-index` | 写入 Markdown sidecar 与 chunk 索引 |
| `POST` | `/api/v1/knowledge-items/import` | 原子导入原始文件、Markdown 与索引 |
| `POST` | `/api/v1/knowledge-items/delete` | 删除知识库文档 |
| `POST` | `/api/v1/knowledge-items/search` | chunk 级混合检索 |
| `POST` | `/api/v1/list_dir` | 列出虚拟目录 |
| `POST` | `/api/v1/glob` | 按路径模式匹配文件或目录 |
| `POST` | `/api/v1/read-file` | 读取原文件访问地址或 Markdown 内容 |

## 创建知识库

### `POST /api/v1/knowledge-bases/create`

创建一个新的知识库。

补充语义：

- 若存在同名 `kb_code` 的未删除知识库，返回冲突
- 若同名 `kb_code` 已被软删除知识库占用，当前实现也返回冲突，不自动恢复

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `kb_code` | string | 是 | - | 知识库编码 |
| `kb_name` | string | 是 | - | 知识库名称 |
| `kb_description` | string \| null | 否 | `null` | 知识库描述 |
| `status` | `ACTIVE` \| `INACTIVE` | 否 | `ACTIVE` | 知识库状态 |
| `metadata` | object \| null | 否 | `null` | 扩展元数据 |

### 请求示例

```json
{
  "kb_code": "hr-policy",
  "kb_name": "人力制度知识库",
  "kb_description": "公司人事制度与流程文档",
  "status": "ACTIVE",
  "metadata": {
    "owner_dept": "HR",
    "language": "zh-CN"
  }
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `kb_name` | string | 知识库名称 |
| `kb_description` | string \| null | 知识库描述 |
| `status` | string | 知识库状态 |
| `metadata` | object \| null | 扩展元数据 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "kb_name": "人力制度知识库",
    "kb_description": "公司人事制度与流程文档",
    "status": "ACTIVE",
    "metadata": {
      "owner_dept": "HR",
      "language": "zh-CN"
    }
  }
}
```

### 典型错误码

- `KB_CODE_CONFLICT`
- `KB_CODE_SOFT_DELETED_CONFLICT`
- `KB_REQUEST_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 删除知识库

### `POST /api/v1/knowledge-bases/delete`

逻辑删除知识库。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kb_code` | string | 是 | 知识库编码 |

### 请求示例

```json
{
  "kb_code": "hr-policy"
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `is_deleted` | boolean | 删除后的逻辑删除标记 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "is_deleted": true
  }
}
```

### 典型错误码

- `KB_NOT_FOUND`
- `KB_DELETE_KB_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 写入原始文件

### `POST /api/v1/write-file`

将原始文件写入知识库，并建立文档主记录与当前版本记录。

补充语义：

- `file_path` 中的父目录必须已经存在于知识库文件树中
- 当前实现不会根据 `file_path` 自动创建缺失目录
- 若父目录不存在，接口返回业务校验错误

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `kb_code` | string | 是 | - | 知识库编码 |
| `file_code` | string | 是 | - | 文件编码 |
| `file_path` | string | 是 | - | 文件完整路径 |
| `file_description` | string \| null | 否 | `null` | 文件描述 |
| `file_content` | string | 是 | - | 原始文件内容，base64 编码 |
| `version` | string | 是 | - | 文档版本 |
| `source_code` | string | 是 | - | 来源系统编码 |
| `status` | `ACTIVE` \| `INACTIVE` | 否 | `ACTIVE` | 文件状态 |
| `metadata` | object \| null | 否 | `null` | 扩展元数据 |

### 请求示例

```json
{
  "kb_code": "hr-policy",
  "file_code": "attendance-policy-pdf",
  "file_path": "/考勤制度/异常考勤处理办法.pdf",
  "file_description": "异常考勤制度原文",
  "file_content": "JVBERi0xLjQKJcfs...",
  "version": "v1",
  "source_code": "oa",
  "status": "ACTIVE",
  "metadata": {
    "owner_dept": "HR",
    "doc_category": "policy"
  }
}
```

说明：

- `file_path` 仍然表示知识库内的逻辑路径
- 但对象存储键不再依赖 `file_path`
- 写文件前应先确保目录已经通过独立目录管理流程创建完成

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `file_code` | string | 文件编码 |
| `type_code` | string | 文件类型编码，通常来自扩展名小写 |
| `file_path` | string | 文件路径 |
| `file_description` | string \| null | 文件描述 |
| `version` | string | 文件版本 |
| `status` | string | 文件状态 |
| `metadata` | object \| null | 扩展元数据 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "file_code": "attendance-policy-pdf",
    "type_code": "pdf",
    "file_path": "/考勤制度/异常考勤处理办法.pdf",
    "file_description": "异常考勤制度原文",
    "version": "v1",
    "status": "ACTIVE",
    "metadata": {
      "owner_dept": "HR",
      "doc_category": "policy"
    }
  }
}
```

### 典型错误码

- `KB_FILE_VERSION_CONFLICT`
- `KB_FILE_CODE_SOFT_DELETED_CONFLICT`
- `KB_WRITE_FILE_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 写入索引

### `POST /api/v1/write-index`

为已存在的文件版本写入 Markdown sidecar 和 chunk 索引。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kb_code` | string | 是 | 知识库编码 |
| `file_code` | string | 是 | 文件编码 |
| `version` | string | 是 | 文档版本 |
| `markdown_content` | string | 是 | Markdown 原文 |
| `chunks` | object[] | 是 | chunk 列表，请求内 `chunk_no` 必须唯一 |

### 请求示例

```json
{
  "kb_code": "hr-policy",
  "file_code": "attendance-policy-pdf",
  "version": "v1",
  "markdown_content": "# 异常考勤处理办法\n\n第一条 员工应按时打卡。\n\n第二条 异常考勤需提交说明。",
  "chunks": [
    {
      "chunk_no": 1,
      "start_line": 1,
      "end_line": 3,
      "chunk_text": "第一条 员工应按时打卡。",
      "embedding": [0.11, 0.22, 0.33],
      "char_start": 0,
      "char_end": 15
    },
    {
      "chunk_no": 2,
      "start_line": 4,
      "end_line": 5,
      "chunk_text": "第二条 异常考勤需提交说明。",
      "embedding": [0.44, 0.55, 0.66],
      "char_start": 16,
      "char_end": 33
    }
  ]
}
```

### `chunks[]` 字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `chunk_no` | integer | 是 | chunk 序号 |
| `start_line` | integer | 是 | 起始行 |
| `end_line` | integer | 是 | 结束行 |
| `chunk_text` | string | 是 | chunk 文本 |
| `embedding` | number[] | 是 | 向量值 |
| `char_start` | integer \| null | 否 | 起始字符偏移 |
| `char_end` | integer \| null | 否 | 结束字符偏移 |

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `file_code` | string | 文件编码 |
| `version` | string | 文档版本 |
| `chunks.count` | integer | 成功写入的 chunk 数量 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "file_code": "attendance-policy-pdf",
    "version": "v1",
    "chunks": {
      "count": 2
    }
  }
}
```

### 典型错误码

- `KB_FILE_NOT_FOUND`
- `KB_FILE_VERSION_NOT_FOUND`
- `KB_WRITE_INDEX_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 原子导入文档与索引

### `POST /api/v1/knowledge-items/import`

一次性写入原始文件、Markdown sidecar 和 chunk 索引。

语义上等价于按顺序执行：

1. `POST /api/v1/write-file`
2. `POST /api/v1/write-index`

但服务端按单次导入事务处理。

补充语义：

- `file_path` 中的父目录必须已经存在于知识库文件树中
- 当前实现不会在导入时自动补建目录
- 若父目录不存在，接口返回业务校验错误

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `kb_code` | string | 是 | - | 知识库编码 |
| `file_code` | string | 是 | - | 文件编码 |
| `file_path` | string | 是 | - | 文件路径 |
| `file_description` | string \| null | 否 | `null` | 文件描述 |
| `file_content` | string | 是 | - | 原始文件内容，base64 编码 |
| `version` | string | 是 | - | 文档版本 |
| `source_code` | string | 是 | - | 来源系统编码 |
| `status` | `ACTIVE` \| `INACTIVE` | 否 | `ACTIVE` | 文档状态 |
| `metadata` | object \| null | 否 | `null` | 扩展元数据 |
| `markdown_content` | string | 是 | - | Markdown 原文 |
| `chunks` | object[] | 是 | - | chunk 列表，请求内 `chunk_no` 必须唯一 |

### 请求示例

```json
{
  "kb_code": "hr-policy",
  "file_code": "attendance-policy-pdf",
  "file_path": "/考勤制度/异常考勤处理办法.pdf",
  "file_description": "异常考勤制度原文",
  "file_content": "JVBERi0xLjQKJcfs...",
  "version": "v1",
  "source_code": "oa",
  "status": "ACTIVE",
  "metadata": {
    "owner_dept": "HR",
    "doc_category": "policy"
  },
  "markdown_content": "# 异常考勤处理办法\n\n第一条 员工应按时打卡。\n\n第二条 异常考勤需提交说明。",
  "chunks": [
    {
      "chunk_no": 1,
      "start_line": 1,
      "end_line": 3,
      "chunk_text": "第一条 员工应按时打卡。",
      "embedding": [0.11, 0.22, 0.33],
      "char_start": 0,
      "char_end": 15
    },
    {
      "chunk_no": 2,
      "start_line": 4,
      "end_line": 5,
      "chunk_text": "第二条 异常考勤需提交说明。",
      "embedding": [0.44, 0.55, 0.66],
      "char_start": 16,
      "char_end": 33
    }
  ]
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `file_code` | string | 文件编码 |
| `type_code` | string | 文件类型编码 |
| `file_path` | string | 文件路径 |
| `file_description` | string \| null | 文件描述 |
| `version` | string | 文档版本 |
| `status` | string | 文档状态 |
| `metadata` | object \| null | 扩展元数据 |
| `chunks.count` | integer | 成功写入的 chunk 数量 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "file_code": "attendance-policy-pdf",
    "type_code": "pdf",
    "file_path": "/考勤制度/异常考勤处理办法.pdf",
    "file_description": "异常考勤制度原文",
    "version": "v1",
    "status": "ACTIVE",
    "metadata": {
      "owner_dept": "HR",
      "doc_category": "policy"
    },
    "chunks": {
      "count": 2
    }
  }
}
```

### 典型错误码

- `KB_FILE_VERSION_CONFLICT`
- `KB_FILE_CODE_SOFT_DELETED_CONFLICT`
- `KB_IMPORT_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 删除知识库文档

### `POST /api/v1/knowledge-items/delete`

逻辑删除知识库中的单个文档。

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kb_code` | string | 是 | 知识库编码 |
| `file_code` | string | 是 | 文件编码 |

### 请求示例

```json
{
  "kb_code": "hr-policy",
  "file_code": "attendance-policy-pdf"
}
```

### 成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `file_code` | string | 文件编码 |
| `is_deleted` | boolean | 删除后的逻辑删除标记 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "file_code": "attendance-policy-pdf",
    "is_deleted": true
  }
}
```

### 典型错误码

- `KB_NOT_FOUND`
- `KB_FILE_NOT_FOUND`
- `KB_DELETE_FILE_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## chunk 级混合检索

### `POST /api/v1/knowledge-items/search`

执行文本召回与向量召回融合后的 chunk 级检索。

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `query` | string | 是 | - | 查询文本 |
| `kb_codes` | string[] | 是 | - | 参与检索的知识库编码列表 |
| `top_k` | integer | 否 | `10` | 最终返回条数，必须大于 `0` |
| `vector_top_k` | integer | 否 | `40` | 向量召回候选数，必须大于等于 `top_k` |
| `text_top_k` | integer | 否 | `30` | 文本召回候选数，必须大于等于 `top_k` |
| `source_codes` | string[] \| null | 否 | `null` | 来源过滤 |
| `type_codes` | string[] \| null | 否 | `null` | 文件类型过滤 |

### 请求示例

```json
{
  "query": "员工请假制度怎么规定",
  "kb_codes": ["hr-policy"],
  "top_k": 2,
  "vector_top_k": 10,
  "text_top_k": 10,
  "source_codes": ["oa"],
  "type_codes": ["pdf"]
}
```

### 成功响应 `data.items[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `file_code` | string | 文件编码 |
| `version` | string | 命中文档版本 |
| `chunk_no` | integer | chunk 序号 |
| `chunk_text` | string | chunk 文本 |
| `score` | number | 融合得分 |
| `text_score` | number \| null | 文本召回得分 |
| `vector_score` | number \| null | 向量召回得分 |
| `source_code` | string | 来源系统编码 |
| `type_code` | string | 文件类型编码 |
| `file_path` | string | 文件路径 |

### 成功响应 `data.meta`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `query` | string | 原始查询 |
| `top_k` | integer | 最终返回条数配置 |
| `vector_top_k` | integer | 向量召回候选数 |
| `text_top_k` | integer | 文本召回候选数 |
| `returned_count` | integer | 实际返回条数 |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "items": [
      {
        "kb_code": "hr-policy",
        "file_code": "attendance-policy-pdf",
        "version": "v1",
        "chunk_no": 2,
        "chunk_text": "第二条 异常考勤需提交说明。",
        "score": 0.93,
        "text_score": 0.81,
        "vector_score": 0.88,
        "source_code": "oa",
        "type_code": "pdf",
        "file_path": "/考勤制度/异常考勤处理办法.pdf"
      },
      {
        "kb_code": "hr-policy",
        "file_code": "leave-policy-docx",
        "version": "v3",
        "chunk_no": 5,
        "chunk_text": "员工请假应至少提前一天提交申请。",
        "score": 0.89,
        "text_score": 0.79,
        "vector_score": 0.83,
        "source_code": "oa",
        "type_code": "docx",
        "file_path": "/请假制度/员工请假管理办法.docx"
      }
    ],
    "meta": {
      "query": "员工请假制度怎么规定",
      "top_k": 2,
      "vector_top_k": 10,
      "text_top_k": 10,
      "returned_count": 2
    }
  }
}
```

### 典型错误码

- `KB_SEARCH_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 列出虚拟目录

### `POST /api/v1/list_dir`

列出知识库虚拟目录中的直接子项。

说明：

- `path` 默认值为 `/`
- 返回结构中的 `data` 是数组，不是包裹在 `items` 对象中的结果

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `kb_codes` | string[] | 是 | - | 知识库编码列表 |
| `path` | string | 否 | `/` | 目录路径 |
| `source_codes` | string[] \| null | 否 | `null` | 来源过滤 |
| `type_codes` | string[] \| null | 否 | `null` | 文件类型过滤 |

### 请求示例

```json
{
  "kb_codes": ["hr-policy"],
  "path": "/考勤制度",
  "source_codes": ["oa"],
  "type_codes": ["pdf", "docx"]
}
```

### 成功响应 `data[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `name` | string | 文件或目录完整路径 |
| `type` | `file` \| `directory` | 节点类型 |
| `size` | integer | 文件大小；目录通常为 `0` |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "kb_code": "hr-policy",
      "name": "/考勤制度/异常考勤处理办法.pdf",
      "type": "file",
      "size": 204800
    },
    {
      "kb_code": "hr-policy",
      "name": "/考勤制度/归档",
      "type": "directory",
      "size": 0
    }
  ]
}
```

### 典型错误码

- `KB_LIST_DIR_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 按路径模式匹配

### `POST /api/v1/glob`

基于路径模式查找文件或目录。

说明：

- 返回结构中的 `data` 是数组，不是包裹在 `items` 对象中的结果
- 当前实现支持多层路径模式匹配，适合做虚拟文件树下的按层检索

### 请求体

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `kb_codes` | string[] | 是 | 知识库编码列表 |
| `path` | string | 是 | 路径模式 |
| `source_codes` | string[] \| null | 否 | 来源过滤 |
| `type_codes` | string[] \| null | 否 | 文件类型过滤 |

### 请求示例

```json
{
  "kb_codes": ["hr-policy"],
  "path": "/考勤制度/*.pdf",
  "source_codes": ["oa"],
  "type_codes": ["pdf"]
}
```

### 成功响应 `data[]`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 知识库编码 |
| `name` | string | 文件或目录完整路径 |
| `type` | `file` \| `directory` | 节点类型 |
| `size` | integer | 文件大小；目录通常为 `0` |

### 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "kb_code": "hr-policy",
      "name": "/考勤制度/异常考勤处理办法.pdf",
      "type": "file",
      "size": 204800
    }
  ]
}
```

### 典型错误码

- `KB_GLOB_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 读取文件

### `POST /api/v1/read-file`

根据路径读取原始文件访问地址或 Markdown 内容。

读取规则：

- `content_type=original`：返回原文件访问 `url`
- `content_type=markdown` 且提供 `start_line/end_line`：返回行区间文本
- `content_type=markdown` 且不提供行区间：返回完整 Markdown 内容
- `start_line` 和 `end_line` 需要成对出现

### 请求体

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `kb_codes` | string[] | 是 | - | 查询的知识库编码列表 |
| `path` | string | 是 | - | 文件路径 |
| `content_type` | `original` \| `markdown` | 否 | `markdown` | 读取内容类型 |
| `start_line` | integer \| null | 否 | `null` | Markdown 起始行 |
| `end_line` | integer \| null | 否 | `null` | Markdown 结束行 |

### 请求示例

读取 Markdown：

```json
{
  "kb_codes": ["hr-policy"],
  "path": "/考勤制度/异常考勤处理办法.pdf",
  "content_type": "markdown",
  "start_line": 1,
  "end_line": 5
}
```

读取原文件：

```json
{
  "kb_codes": ["hr-policy"],
  "path": "/考勤制度/异常考勤处理办法.pdf",
  "content_type": "original"
}
```

### 读取 Markdown 的成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 命中的知识库编码 |
| `path` | string | 文件路径 |
| `content_type` | `markdown` | 内容类型 |
| `start_line` | integer \| null | 返回起始行 |
| `end_line` | integer \| null | 返回结束行 |
| `data` | string \| null | Markdown 文本内容 |
| `reached_eof` | boolean \| null | 是否到达文件末尾 |

### Markdown 成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "path": "/考勤制度/异常考勤处理办法.pdf",
    "content_type": "markdown",
    "start_line": 1,
    "end_line": 5,
    "data": "# 异常考勤处理办法\n\n第一条 员工应按时打卡。\n\n第二条 异常考勤需提交说明。\n",
    "reached_eof": true
  }
}
```

### 读取原文件的成功响应 `data`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kb_code` | string | 命中的知识库编码 |
| `path` | string | 文件路径 |
| `content_type` | `original` | 内容类型 |
| `url` | string | 原文件访问地址 |

### 原文件成功响应示例

```json
{
  "code": 200,
  "message": "success",
  "error": null,
  "data": {
    "kb_code": "hr-policy",
    "path": "/考勤制度/异常考勤处理办法.pdf",
    "content_type": "original",
    "url": "https://minio.example/knowledge-base/hr-policy/%E8%80%83%E5%8B%A4%E5%88%B6%E5%BA%A6/%E5%BC%82%E5%B8%B8%E8%80%83%E5%8B%A4%E5%A4%84%E7%90%86%E5%8A%9E%E6%B3%95.pdf?ttl=3600"
  }
}
```

### 典型错误码

- `KB_FILE_NOT_FOUND`
- `KB_READ_FILE_INVALID`
- `KB_RUNTIME_CONFIG_ERROR`

## 错误码摘要

| 错误码 | HTTP 状态码 | 说明 |
| --- | --- | --- |
| `REQUEST_VALIDATION_FAILED` | `422` | 请求结构或字段校验失败 |
| `KB_RUNTIME_CONFIG_ERROR` | `503` | 运行时配置缺失或基础设施不可用 |
| `KB_INTERNAL_ERROR` | `500` | 未预期内部错误 |
| `KB_CODE_CONFLICT` | `409` | 知识库编码已存在 |
| `KB_CODE_SOFT_DELETED_CONFLICT` | `409` | 知识库编码被软删除记录占用 |
| `KB_REQUEST_INVALID` | `422` | 创建知识库请求不满足业务约束 |
| `KB_NOT_FOUND` | `404` | 知识库不存在 |
| `KB_DELETE_KB_INVALID` | `422` | 删除知识库请求不满足业务约束 |
| `KB_FILE_VERSION_CONFLICT` | `409` | 文件版本已存在 |
| `KB_FILE_CODE_SOFT_DELETED_CONFLICT` | `409` | 文件编码被软删除记录占用 |
| `KB_WRITE_FILE_INVALID` | `422` | 写入原始文件请求不满足业务约束 |
| `KB_FILE_NOT_FOUND` | `404` | 文件不存在 |
| `KB_FILE_VERSION_NOT_FOUND` | `404` | 文件版本不存在 |
| `KB_WRITE_INDEX_INVALID` | `422` | 写入索引请求不满足业务约束 |
| `KB_IMPORT_INVALID` | `422` | 原子导入请求不满足业务约束 |
| `KB_DELETE_FILE_INVALID` | `422` | 删除文档请求不满足业务约束 |
| `KB_SEARCH_INVALID` | `422` | 检索请求不满足业务约束 |
| `KB_LIST_DIR_INVALID` | `422` | 目录浏览请求不满足业务约束 |
| `KB_GLOB_INVALID` | `422` | 路径匹配请求不满足业务约束 |
| `KB_READ_FILE_INVALID` | `422` | 读文件请求不满足业务约束 |
