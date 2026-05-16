# 知识模块元数据与检索扩展接口说明

## 文档范围

本文档定义知识模块中与文件元数据、纯元数据检索、Agent 检索和业务检索相关的扩展接口。

- Base URL：`/api/v1`
- 协议：`HTTP`
- 默认返回：`application/json`

## 通用约定

### 成功响应

除特殊说明外，接口统一使用如下响应信封：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

说明：

- `resultCode`：接口状态码，`0` 表示成功，`-1` 表示失败
- `resultMsg`：接口返回说明
- `resultObject`：业务返回体；无额外返回时返回空对象

### 失败响应

普通失败时统一返回：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

对于 DSL 校验失败的接口，返回：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {
    "errorCode": "DSL_VALIDATION_ERROR",
    "errorList": [
      {
        "path": "filter.and[1].contains.fieldName",
        "code": "UNKNOWN_FIELD",
        "message": "fieldName 'tagz' is not defined"
      }
    ]
  }
}
```

### 路径字段约定

- `knCode`：知识库编码
- `knCodeList`：知识库编码列表
- `filePath`：文件路径，以 `/` 开头，不包含知识库名称
- `propertyName`：全局唯一的元数据属性名

### 元数据类型约定

当前支持以下元数据类型：

- `string`
- `stringList`
- `number`
- `boolean`
- `datetime`

### 检索模式约定

当前支持以下检索模式：

- `fullTextRecall`
- `embedding`
- `mixedRecall`

### 返回粒度约定

当前支持以下返回粒度：

- `file`
- `chunk`

## DSL 使用说明

本文档当前实现范围中的检索接口只涉及 Agent DSL。

Business DSL 及其对应接口已从当前实现范围中抽离，并标记为废弃候选，当前阶段不做。

服务端处理链路如下：

```text
Agent DSL
  -> Internal Query Plan
  -> SQL / FTS / Vector Retrieval
```

### Agent DSL

适用接口：

- `/api/v1/knowledgeItems/metadataSearch`
- `/api/v1/knowledgeItems/search`
- `/api/v1/knowledgeItems/searchFile`

主要特点：

- 使用 `where`
- 语义检索使用 `query: string`
- 使用 `topK`
- 仅支持受控布尔表达式和有限操作符

第一版复杂度限制：

- 最大布尔嵌套深度
- 最大叶子条件数

### 过滤表达式

`where` 采用 JSON AST 风格的过滤表达式。

布尔节点：

- `and`
- `or`
- `not`

叶子操作符：

- `eq`
- `ne`
- `in`
- `contains`
- `exists`
- `gt`
- `gte`
- `lt`
- `lte`

叶子示例：

```json
{
  "eq": {
    "fieldName": "status",
    "value": "active"
  }
}
```

组合示例：

```json
{
  "and": [
    {"eq": {"fieldName": "status", "value": "active"}},
    {"contains": {"fieldName": "tags", "value": "contract"}}
  ]
}
```

### 系统字段

`where` 中除自定义元数据属性外，还可以引用以下系统字段；这些字段直接来自文件主表，不需要事先通过 `metadataProperties/create` 注册：

| 字段名 | 类型 | 含义 |
| --- | --- | --- |
| `fileName` | `string` | 文件名（含扩展名） |
| `fileType` | `string` | 文件名末尾扩展名（lowercase，如 `md`、`pdf`） |
| `fileSize` | `number` | 文件字节数 |
| `mimeType` | `string` | MIME 类型 |
| `createdAt` | `datetime` | 创建时间 |
| `updatedAt` | `datetime` | 更新时间 |

系统字段不支持 `contains`（仅 `stringList` 适用），其余约束与自定义字段一致。

示例：

```json
{
  "and": [
    {"in": {"fieldName": "fileType", "value": ["md", "pdf"]}},
    {"gt": {"fieldName": "createdAt", "value": "2026-01-01T00:00:00Z"}}
  ]
}
```

### 叶子值类型校验

每个叶子节点的 `value` 必须与 `fieldName` 声明的类型一致，否则返回 `INVALID_FIELD_VALUE_TYPE`：

- `string`：value 必须是字符串
- `number`：value 必须是数值（不接受布尔值）
- `boolean`：value 必须是布尔值
- `datetime`：value 必须是 ISO 8601 字符串（如 `2026-05-15T10:00:00Z`）
- `stringList`：仅支持 `contains`（值为单个字符串）和 `exists`

`in` 不适用于 `stringList`，请使用 `contains`；`gt/gte/lt/lte` 仅适用于 `number` 和 `datetime` 字段；`exists` 不应携带 `value`。

### 使用建议

1. 纯元数据检索
   - 不传 `query`
   - 使用 `where` 做结构化过滤
2. 语义检索
   - 传 `query`
   - 可选传 `where` 缩小候选范围
3. 元数据返回控制
   - 检索接口传 `metadataFieldList` 时才返回元数据
   - `metadata/get` 不传 `metadataFieldList` 时默认返回全部未删除元数据

### DSL 错误修正

DSL 校验失败时，优先根据以下字段修正请求：

- `errorList[].path`
- `errorList[].code`
- `errorList[].message`

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/metadataProperties/create` | 新增元数据属性定义 |
| `POST` | `/api/v1/metadataProperties/batchCreate` | 批量新增元数据属性定义 |
| `POST` | `/api/v1/metadataProperties/delete` | 删除元数据属性定义 |
| `POST` | `/api/v1/metadataProperties/list` | 查看元数据属性定义，支持按属性名过滤 |
| `POST` | `/api/v1/knowledgeItems/metadata/update` | 统一更新文件元数据 |
| `POST` | `/api/v1/knowledgeItems/metadata/get` | 查看文件元数据 |
| `POST` | `/api/v1/knowledgeItems/metadataFields/list` | 查看知识库下实际已使用的元数据属性 |
| `POST` | `/api/v1/knowledgeItems/metadataSearch` | Agent DSL 版纯元数据检索 |
| `POST` | `/api/v1/knowledgeItems/search` | 基于原检索接口升级的 Agent DSL 版 chunk 级语义检索 |
| `POST` | `/api/v1/knowledgeItems/searchFile` | Agent DSL 版文件级语义检索 |

## 元数据属性定义

### `POST /api/v1/metadataProperties/create`

新增全局元数据属性定义。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 全局唯一字段名 |
| `valueType` | string | 是 | 属性类型 |
| `description` | string | 否 | 描述 |
| `extParams` | object | 否 | 业务系统透传的扩展参数 |

请求示例：

```json
{
  "propertyName": "status",
  "valueType": "string",
  "description": "用于标记文档状态",
  "extParams": {
    "sourceSystem": "oa",
    "displayOrder": 10
  }
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "propertyName": "status",
    "valueType": "string",
    "description": "用于标记文档状态",
    "extParams": {
      "sourceSystem": "oa",
      "displayOrder": 10
    }
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "metadata property already exists: status",
  "resultObject": {}
}
```

### `POST /api/v1/metadataProperties/batchCreate`

批量新增全局元数据属性定义。

说明：

- 该接口要求原子执行。
- 当任意一个属性创建失败时，整批请求整体回滚，不保留部分成功结果。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyList` | array[object] | 是 | 待批量创建的属性定义列表 |

`propertyList` 单项字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 全局唯一字段名 |
| `valueType` | string | 是 | 属性类型 |
| `description` | string | 否 | 描述 |
| `extParams` | object | 否 | 业务系统透传的扩展参数 |

请求示例：

```json
{
  "propertyList": [
    {
      "propertyName": "status",
      "valueType": "string",
      "description": "用于标记文档状态",
      "extParams": {
        "sourceSystem": "oa",
        "displayOrder": 10
      }
    },
    {
      "propertyName": "tags",
      "valueType": "stringList",
      "description": "用于标记文档主题",
      "extParams": {
        "sourceSystem": "oa",
        "displayOrder": 20
      }
    }
  ]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "propertyName": "status",
        "valueType": "string",
        "description": "用于标记文档状态",
        "extParams": {
          "sourceSystem": "oa",
          "displayOrder": 10
        }
      },
      {
        "propertyName": "tags",
        "valueType": "stringList",
        "description": "用于标记文档主题",
        "extParams": {
          "sourceSystem": "oa",
          "displayOrder": 20
        }
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "metadata property already exists: status",
  "resultObject": {}
}
```

### `POST /api/v1/metadataProperties/delete`

删除全局元数据属性定义。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 需要删除的属性名 |

请求示例：

```json
{
  "propertyName": "status"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "metadata property is still referenced: status",
  "resultObject": {}
}
```

### `POST /api/v1/metadataProperties/list`

查看元数据属性定义，支持按属性名过滤。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyNameList` | array[string] | 否 | 按属性名过滤；不传表示返回全部 |

请求示例：

```json
{
  "propertyNameList": ["status", "tags"]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "propertyName": "status",
        "valueType": "string",
        "description": "用于标记文档状态",
        "extParams": {
          "sourceSystem": "oa",
          "displayOrder": 10
        }
      },
      {
        "propertyName": "tags",
        "valueType": "stringList",
        "description": "用于标记文档主题",
        "extParams": {
          "sourceSystem": "oa",
          "displayOrder": 20
        }
      }
    ]
  }
}
```

单个属性查询示例：

```json
{
  "propertyNameList": ["status"]
}
```

## 文件元数据管理

### `POST /api/v1/knowledgeItems/metadata/update`

统一更新文件元数据，是唯一的文件元数据写接口。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 文件路径 |
| `operationList` | array[object] | 是 | 元数据更新操作列表 |

`operationList` 单项字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 属性名 |
| `operation` | string | 是 | `set`、`unset`、`append`、`remove`、`clear` |
| `value` | any | 否 | 操作值；`unset` 和 `clear` 可不传 |

更新规则：

- 标量类型支持：`set`、`unset`
- `stringList` 类型支持：`set`、`append`、`remove`、`clear`、`unset`
- `set` 用于新增属性或整值覆盖
- `unset` 用于删除整个属性
- `append` 追加不存在元素，已存在元素不重复追加
- `remove` 删除命中的元素，不报不存在错误
- `clear` 将列表属性置为空列表 `[]`，不删除属性本身

请求示例：

```json
{
  "knCode": "2",
  "filePath": "/制度/人事/续签流程.md",
  "operationList": [
    {
      "propertyName": "tags",
      "operation": "append",
      "value": ["contract", "renewal"]
    },
    {
      "propertyName": "status",
      "operation": "set",
      "value": "active"
    },
    {
      "propertyName": "owner",
      "operation": "unset"
    }
  ]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "2",
    "filePath": "/制度/人事/续签流程.md",
    "metadata": {
      "tags": {
        "valueType": "stringList",
        "value": ["hr", "contract", "renewal"]
      },
      "status": {
        "valueType": "string",
        "value": "active"
      }
    }
  }
}
```

说明：

- 成功响应中的 `metadata` 只返回本次被修改属性在操作完成后的完整最新值。
- 每个属性返回 `valueType` 和 `value`，避免调用方额外查询属性定义。
- 对于 `unset` 后已不存在的属性，不出现在返回结果中。
- 对于 `clear` 后的列表属性，会返回空列表值。

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "operation append is not allowed for property type: string",
  "resultObject": {}
}
```

### `POST /api/v1/knowledgeItems/metadata/get`

查看文件元数据。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 文件路径 |
| `metadataFieldList` | array[string] | 否 | 指定返回的属性名列表；不传时默认返回全部未删除元数据 |

请求示例：

```json
{
  "knCode": "2",
  "filePath": "/制度/人事/续签流程.md"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "2",
    "filePath": "/制度/人事/续签流程.md",
    "metadata": {
      "status": {
        "valueType": "string",
        "value": "active"
      },
      "tags": {
        "valueType": "stringList",
        "value": ["hr", "contract"]
      },
      "priority": {
        "valueType": "number",
        "value": 3
      }
    }
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/续签流程.md",
  "resultObject": {}
}
```

说明：

- `metadata/get` 返回的 `metadata` 使用统一 typed 结构。
- 每个属性返回 `valueType` 和 `value`。

### `POST /api/v1/knowledgeItems/metadataFields/list`

查看指定知识库列表下实际已使用的元数据属性。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCodeList` | array[string] | 是 | 知识库编码列表 |

请求示例：

```json
{
  "knCodeList": ["2", "3"]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "propertyName": "status",
        "valueType": "string",
        "description": "用于标记文档状态"
      },
      {
        "propertyName": "tags",
        "valueType": "stringList",
        "description": "用于标记文档主题"
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "knCodeList must not be empty",
  "resultObject": {}
}
```

## 纯元数据检索

### `POST /api/v1/knowledgeItems/metadataSearch`

Agent DSL 版纯元数据检索，只返回文件级结果。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `where` | object | 是 | Agent DSL 过滤 AST |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `topK` | integer | 否 | 返回条数，省略时默认 500，最大 10000 |

请求示例：

```json
{
  "knCodeList": ["2"],
  "where": {
    "and": [
      {"eq": {"fieldName": "status", "value": "active"}},
      {"contains": {"fieldName": "tags", "value": "contract"}}
    ]
  },
  "metadataFieldList": ["status", "tags"],
  "topK": 20
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "2",
        "filePath": "/制度/人事/续签流程.md",
        "metadata": {
          "status": {
            "valueType": "string",
            "value": "active"
          },
          "tags": {
            "valueType": "stringList",
            "value": ["hr", "contract"]
          }
        }
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {
    "errorCode": "DSL_VALIDATION_ERROR",
    "errorList": [
      {
        "path": "where.and[2]",
        "code": "TOO_MANY_CONDITIONS",
        "message": "leaf condition count exceeds limit 12"
      }
    ]
  }
}
```

## 语义检索

### `POST /api/v1/knowledgeItems/search`

基于 [api.md](docs/modules/knowledge/api.md:1) 中原有 `POST /api/v1/knowledgeItems/search` 升级后的 Agent DSL 版 chunk 级语义检索。

说明：

- 当前方案确定为升级原 `/api/v1/knowledgeItems/search` 接口，不新增 `searchChunk` 接口。
- 在保留原接口能力的基础上，新增 Agent DSL 风格的 `where` 过滤、`knCodeList` 范围控制和 `metadataFieldList` 元数据返回控制。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 检索文本 |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `where` | object | 否 | Agent DSL 过滤 AST |
| `searchMode` | string | 是 | 检索模式 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `topK` | integer | 是 | 返回条数，必须大于 0 |

请求示例：

```json
{
  "query": "续签流程",
  "where": {
    "eq": {
      "fieldName": "status",
      "value": "active"
    }
  },
  "metadataFieldList": ["status"],
  "topK": 10,
  "searchMode": "mixedRecall",
  "knCodeList": ["2"]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "2",
        "filePath": "/制度/人事/续签流程.md",
        "chunkId": 1024,
        "chunkNo": 3,
        "chunkText": "合同续签需由业务负责人发起审批。",
        "score": 92.5,
        "startLine": 18,
        "endLine": 24,
        "metadata": {
          "status": {
            "valueType": "string",
            "value": "active"
          }
        }
      }
    ]
  }
}
```

### `POST /api/v1/knowledgeItems/searchFile`

Agent DSL 版文件级语义检索。

实现说明：

- 先按 chunk 粒度召回 `topK * 50` 条候选结果
- 再按 `knCode + filePath` 聚合为文件级结果
- 最终最多返回 `topK` 个文件

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 检索文本 |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `where` | object | 否 | Agent DSL 过滤 AST |
| `searchMode` | string | 是 | 检索模式 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `topK` | integer | 是 | 返回条数，必须大于 0 |

请求示例：

```json
{
  "query": "续签流程",
  "where": {
    "eq": {
      "fieldName": "status",
      "value": "active"
    }
  },
  "metadataFieldList": ["status", "tags"],
  "topK": 10,
  "searchMode": "mixedRecall",
  "knCodeList": ["2"]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "2",
        "filePath": "/制度/人事/续签流程.md",
        "score": 94.2,
        "metadata": {
          "status": {
            "valueType": "string",
            "value": "active"
          },
          "tags": {
            "valueType": "stringList",
            "value": ["hr", "contract"]
          }
        }
      }
    ]
  }
}
```

文件级 `score` 为聚合后的最终排序分值，具体融合策略由服务端内部实现决定。

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {
    "errorCode": "DSL_VALIDATION_ERROR",
    "errorList": [
      {
        "path": "where.eq.fieldName",
        "code": "UNKNOWN_FIELD",
        "message": "fieldName 'statuz' is not defined"
      }
    ]
  }
}
```

## 已废弃接口

以下业务 DSL 接口已从当前实现范围中抽离，标记为废弃候选，当前阶段不实现：

- `/api/v1/knowledgeItems/metadataSearch`
- `/api/v1/knowledgeItems/searchFile`

说明：

- `/api/v1/knowledgeItems/metadataSearch` 与 `/api/v1/knowledgeItems/searchFile` 当前已被 Agent DSL 版接口占用
- chunk 级业务 DSL 检索接口历史上曾候选为 `/api/v1/knowledgeItems/searchChunk`，当前方案改为基于 `/api/v1/knowledgeItems/search` 升级实现
- 归档文档中的业务 DSL 契约仅作为历史设计留档，不代表当前生效接口定义

归档文档：

- [metadata_business_api_deprecated.md](/Users/jialangli/code/workspace/by-qa/docs/modules/knowledge/metadata_business_api_deprecated.md:1)

对应的 Business DSL 说明同样仅保留在归档设计范围，不进入当前接口实现范围。
