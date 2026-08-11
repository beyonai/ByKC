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

`datetime` 输入使用 ISO 8601 字符串，建议显式携带时区偏移；未携带偏移时按 `DB_TIMEZONE` 解释。查询和下载接口会将时间统一转换到 `DB_TIMEZONE` 指定的时区，默认为 `Asia/Shanghai`。例如 `2026-01-01T00:00:00Z` 返回为 `2026-01-01T08:00:00+08:00`。系统字段 `createdAt` 和 `updatedAt` 使用相同规则。

### 系统文件属性

系统文件属性由服务根据 `knowledge_fs_entry` 文件记录提供，不需要通过元数据属性管理接口注册。调用方应将这些名称视为保留字段，避免在 Markdown front matter 等自定义元数据中复用。当前共有以下 8 个字段：

| 属性名 | 元数据类型 | 来源 | 含义 |
| --- | --- | --- | --- |
| `fileName` | `string` | `knowledge_fs_entry.name` | 文件名，包含扩展名 |
| `fileType` | `string` | 根据 `knowledge_fs_entry.name` 计算 | 小写文件扩展名，不包含前导点；无扩展名时为空字符串 |
| `fileSize` | `number` | `knowledge_fs_entry.file_size` | 原始文件大小，单位为字节 |
| `mimeType` | `string` | `knowledge_fs_entry.mime_type` | 文件的 MIME 类型 |
| `createdAt` | `datetime` | `knowledge_fs_entry.created_at` | 文件记录创建时间 |
| `updatedAt` | `datetime` | `knowledge_fs_entry.updated_at` | 文件记录最近更新时间 |
| `fileSignature` | `string` | `knowledge_fs_entry.checksum` | 原始文件内容的 SHA-256 校验值 |
| `filePath` | `string` | `knowledge_fs_entry.virtual_path` | 文件在知识库内的完整路径，以 `/` 开头 |

使用约定：

- `POST /api/v1/knowledgeItems/metadata/get`：可在 `metadataFieldList` 中指定系统文件属性；省略该参数时，系统文件属性会与自定义元数据一起返回。
- `POST /api/v1/knowledgeItems/metadataSearch`：系统文件属性既可用于 `where` 过滤，也可通过 `metadataFieldList` 返回。
- `POST /api/v1/knowledgeItems/search` 和 `POST /api/v1/knowledgeItems/searchFile`：系统文件属性可用于 `where` 过滤；需要返回属性值时可通过 `metadataFieldList` 指定。
- 系统文件属性遵循其元数据类型对应的 DSL 操作符规则。例如 `fileSignature` 支持 `eq` 精确匹配，`fileSize` 支持数值比较，`createdAt` 和 `updatedAt` 支持时间比较。
- 字段名使用现有的 `createdAt` 和 `updatedAt`，不提供 `createTime`、`updateTime` 等别名。

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

### Agent DSL 是什么

Agent DSL 是一套专门用于定义 `where` 的结构化过滤表达式。调用方通过 `where` 传入一棵 JSON AST，服务端会先做 DSL 校验，再把它编译成内部查询条件，最终落到 SQL、全文检索或向量检索链路上。

服务端处理链路如下：

```text
Agent DSL
  -> Internal Query Plan
  -> SQL / FTS / Vector Retrieval
```

本章节只定义 `where` 本身的语法、语义、约束和限制；具体哪些接口支持 `where`、`where` 是否必填、以及可用字段范围，请以各接口定义为准。

### Agent DSL 解决什么问题

它主要解决“先按结构化条件缩小范围，再做检索或直接返回结果”的问题，典型场景包括：

- 精确过滤：例如“只看 `status=active` 的对象”
- 组合条件过滤：例如“同时满足状态、时间、标签等多个条件”
- 在更大检索流程前先缩小候选范围

### 如何使用

最小示例：

1. 单个叶子条件

```json
{
  "where": {
    "eq": {"fieldName": "status", "value": "active"}
  }
}
```

2. 布尔组合条件

```json
{
  "where": {
    "and": [
      {"eq": {"fieldName": "status", "value": "active"}},
      {"contains": {"fieldName": "tags", "value": "contract"}}
    ]
  }
}
```

3. 嵌套布尔条件

```json
{
  "where": {
    "and": [
      {
        "or": [
          {"eq": {"fieldName": "status", "value": "active"}},
          {"eq": {"fieldName": "status", "value": "pending"}}
        ]
      },
      {"gte": {"fieldName": "publishedAt", "value": "2026-01-01T00:00:00Z"}}
    ]
  }
}
```

### 表达式长什么样

`where` 采用 JSON AST 风格表达式。每个节点都必须是“恰好一个操作符”的对象。

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
- `prefix`
- `wildcard`

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

布尔节点使用规则：

- `and` 和 `or` 的值必须是非空数组
- `not` 的值必须是单个子表达式对象，不能是数组
- 每个节点只能出现一个操作符；例如同一层不能同时放 `eq` 和 `ne`

### 按类型划分的叶子操作符与示例

以下各表按“字段类型”整理当前实现中可用的叶子操作符。这里描述的是 `fieldName` 对应字段类型与操作符的关系，不展开具体字段清单。近似语义的操作符放在同一行展示。

#### string

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "status", "value": "active"}}` | `status` 精确等于 `active`；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "status", "value": ["active", "pending"]}}` | `status` 属于给定字符串集合之一。 |
| `exists` | `{"exists": {"fieldName": "status"}}` | 文件上存在 `status` 字段且值非空。 |
| `prefix` | `{"prefix": {"fieldName": "status", "value": "act"}}` | `status` 以前缀 `act` 开头，可理解为仅支持“末尾隐含 `*`”的简化版 `wildcard`。 |
| `wildcard` | `{"wildcard": {"fieldName": "status", "value": "act*"}}` | `status` 匹配通配模式；`*` 表示零个或多个字符，`?` 表示恰好一个字符。 |

不支持：

- `contains`
- `gt` / `gte` / `lt` / `lte`

#### stringList

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `contains` | `{"contains": {"fieldName": "tags", "value": "contract"}}` | `tags` 列表中包含元素 `contract`。 |
| `exists` | `{"exists": {"fieldName": "tags"}}` | 文件上存在 `tags` 字段且值非空。 |

不支持：

- `eq` / `ne`
- `in`
- `gt` / `gte` / `lt` / `lte`
- `prefix` / `wildcard`

#### number

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "priority", "value": 5}}` | `priority` 精确等于 `5`；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "priority", "value": [1, 3, 5]}}` | `priority` 属于给定数值集合之一。 |
| `exists` | `{"exists": {"fieldName": "priority"}}` | 文件上存在 `priority` 字段且值非空。 |
| `gt` / `gte` / `lt` / `lte` | `{"gt": {"fieldName": "priority", "value": 5}}` | `priority > 5`；其余分别表示 `>=`、`<`、`<=`。 |

不支持：

- `contains`
- `prefix` / `wildcard`

#### boolean

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "archived", "value": true}}` | `archived` 精确等于 `true`；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "archived", "value": [true]}}` | `archived` 属于给定布尔集合之一。 |
| `exists` | `{"exists": {"fieldName": "archived"}}` | 文件上存在 `archived` 字段且值非空。 |

不支持：

- `contains`
- `gt` / `gte` / `lt` / `lte`
- `prefix` / `wildcard`

#### datetime

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "publishedAt", "value": "2026-05-15T10:00:00Z"}}` | 时间字段精确等于给定 ISO 8601 时间点；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "publishedAt", "value": ["2026-05-01T00:00:00Z", "2026-05-15T10:00:00Z"]}}` | 时间字段属于给定时间点集合之一。 |
| `exists` | `{"exists": {"fieldName": "publishedAt"}}` | 文件上存在该时间字段且值非空。 |
| `gt` / `gte` / `lt` / `lte` | `{"gte": {"fieldName": "publishedAt", "value": "2026-01-01T00:00:00Z"}}` | 时间字段晚于或等于给定时间点；其余分别表示严格大于、严格小于、小于等于。 |

不支持：

- `contains`
- `prefix` / `wildcard`

### `prefix` / `wildcard` 的使用规则

`prefix` 和 `wildcard` 仅适用于 `string` 类型字段。

`prefix` 可以把它理解为 `wildcard` 的简化版：

- `prefix.value = "report"` 的语义，近似等价于 `wildcard.value = "report*"`
- 它只表达“从某个前缀开始”，不支持在中间或开头写通配符

```json
{"prefix": {"fieldName": "fileName", "value": "report"}}
```

也就是说，上面的条件表示“匹配所有以 `report` 开头的值”。

`wildcard` 为通配符匹配，语法参考 ES `wildcard` 查询：

- `*` 匹配零个或多个字符
- `?` 匹配恰好一个字符
- 输入中的特殊字符会按实现规则自动转义

```json
{"wildcard": {"fieldName": "fileName", "value": "report_?.*"}}
```

实现层面，`prefix` 和 `wildcard` 最终都会被编译为 SQL `LIKE` 条件，并使用单字符 `ESCAPE '!'` 做转义；但调用方理解和编写 DSL 时，建议优先按上面的匹配语义来思考，而不是直接按 SQL 语法来思考。

### 叶子值类型校验

每个叶子节点的 `value` 必须与 `fieldName` 声明的类型一致，否则返回 `INVALID_FIELD_VALUE_TYPE`：

- `string`：`value` 必须是字符串
- `number`：`value` 必须是数值，不接受布尔值
- `boolean`：`value` 必须是布尔值
- `datetime`：`value` 必须是 ISO 8601 字符串，如 `2026-05-15T10:00:00Z`
- `stringList`：仅支持 `contains` 和 `exists`；其中 `contains.value` 必须是单个字符串

额外规则：

- `exists` 不应携带 `value`
- `in.value` 必须是非空数组
- `in` 不适用于 `stringList`，请改用 `contains`
- `gt/gte/lt/lte` 仅适用于 `number` 和 `datetime`

### 当前局限性

当前实现是“受控 DSL”，目的是让调用方能稳定地表达常见过滤条件，而不是提供一门无限扩展的查询语言。主要局限如下：

- 仅支持 `and` / `or` / `not` 三种布尔操作
- 仅支持 11 个叶子操作符，不支持 `between`、`regex`、脚本表达式等
- 最大布尔嵌套深度为 `3`
- 最大叶子条件数为 `12`
- `stringList` 只支持 `contains` 和 `exists`

### 使用建议

1. 纯元数据检索
   - 先从简单叶子条件开始
   - 确认单个条件正确后，再组合 `and` / `or` / `not`
2. 通配匹配
   - 只需要“某前缀开头”时优先用 `prefix`
   - 只有确实需要 `*` / `?` 语义时再使用 `wildcard`
3. 条件复杂度
   - 尽量避免过深嵌套
   - 尽量控制叶子条件数量，便于排查错误

### DSL 错误修正

DSL 校验失败时，优先根据以下字段修正请求：

- `errorList[].path`
- `errorList[].code`
- `errorList[].message`

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/metadata/update` | 统一新增、修改或删除单个文件的元数据 |
| `POST` | `/api/v1/knowledgeItems/metadata/get` | 查看文件元数据 |
| `POST` | `/api/v1/knowledgeItems/metadataSearch` | Agent DSL 版纯元数据检索 |
| `POST` | `/api/v1/knowledgeItems/search` | 基于原检索接口升级的 Agent DSL 版 chunk 级语义检索 |
| `POST` | `/api/v1/knowledgeItems/searchFile` | Agent DSL 版文件级语义检索 |

## 已移出的元数据管理能力

元数据属性定义管理和元数据字段列表接口已移出本项目，由外部元数据管理项目承担。本项目保留文件元数据查看能力、检索侧 DSL 能力，以及 `/api/v1/knowledgeItems/import` 对 Markdown YAML front matter 的自动入库能力。

文件元数据的写入收口为本文档新增的 `/api/v1/knowledgeItems/metadata/update`，不恢复属性定义管理。该接口直接携带值类型，支持对单个文件的多个元数据进行原子批量操作。

`/api/v1/knowledgeItems/import` 解析 front matter 时不再依赖预注册属性定义，也不做强类型校验。系统会按单个 YAML 值推断存储类型；同一个 key 在不同文档中出现不同类型时允许并存，检索时按查询条件对应的类型列匹配，不会因为同名字段存在其他类型值而报错。

## 单文件元数据批量写入

### `POST /api/v1/knowledgeItems/metadata/update`

文件元数据的唯一写接口。同一个请求只操作一个文件，可批量执行多个元数据操作，统一覆盖新增、修改和删除场景。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 知识库内文件路径，以 `/` 开头 |
| `operationList` | array[object] | 是 | 非空元数据操作列表 |

`operationList` 单项字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 自定义元数据属性名；不允许使用系统文件属性 |
| `operation` | string | 是 | `set`、`unset`、`append`、`remove` 或 `clear` |
| `valueType` | string | 条件必填 | `set` 时必填；其他操作不传 |
| `value` | any | 条件必填 | `set`、`append`、`remove` 时必填；`unset`、`clear` 时不传 |

`operationList` 中同一个 `propertyName` 只能出现一次。

#### 操作语义

| 操作 | 适用类型 | 语义 |
| --- | --- | --- |
| `set` | 全部类型 | 属性不存在时新增，已存在时整值覆盖；允许通过显式 `valueType` 变更类型 |
| `unset` | 全部类型 | 删除整个属性；属性不存在时也视为成功 |
| `append` | `stringList` | 追加尚未存在的元素，已存在的元素不重复追加 |
| `remove` | `stringList` | 删除指定元素，元素不存在时也视为成功 |
| `clear` | `stringList` | 将属性值置为空列表 `[]`，不删除属性 |

补充规则：

- `set.valueType` 只能是 `string`、`stringList`、`number`、`boolean` 或 `datetime`。
- `set.value` 必须与 `valueType` 一致；`datetime` 使用 ISO 8601 字符串，`stringList` 必须是字符串数组。
- `append.value` 和 `remove.value` 必须是非空字符串数组。
- `append`、`remove` 和 `clear` 要求属性已存在且当前类型为 `stringList`。
- 不接受 `null`；删除属性必须使用 `unset`。
- `fileName`、`fileType`、`fileSize`、`mimeType`、`createdAt`、`updatedAt`、`fileSignature` 和 `filePath` 是只读系统字段，不允许写入。
- `set` 变更属性类型时，旧类型值会被删除，同一文件下一个 `propertyName` 最多只有一个当前值。

#### 请求示例

```json
{
  "knCode": "2",
  "filePath": "/制度/人事/续签流程.md",
  "operationList": [
    {
      "propertyName": "status",
      "operation": "set",
      "valueType": "string",
      "value": "active"
    },
    {
      "propertyName": "tags",
      "operation": "append",
      "value": ["contract", "renewal"]
    },
    {
      "propertyName": "owner",
      "operation": "unset"
    }
  ]
}
```

#### 批量规则

- `operationList` 按整体原子处理，任一操作失败时，本次请求不会保留部分更改。
- `filePath` 必须存在、属于 `knCode` 对应知识库且类型为文件。
- 所有操作都定义为幂等操作，相同请求可安全重试。
- 更新后的元数据可通过元数据查看、检索和 Markdown 文件下载接口获取。

#### 成功响应

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

成功后如需获取文件的完整最新元数据，调用 `POST /api/v1/knowledgeItems/metadata/get`。

#### 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "operation append requires an existing stringList value: tags",
  "resultObject": {}
}
```

常见失败原因：

- `request validation failed`：请求字段缺失、类型错误或 `operationList` 为空。
- `knowledge base not found: {knCode}`：知识库不存在。
- `file not found: {filePath}`：文件不存在。
- `duplicate metadata operation: {propertyName}`：同一属性在 `operationList` 中出现多次。
- `metadata field is read-only: {propertyName}`：尝试修改系统文件属性。
- `metadata value type mismatch: {propertyName}`：`value` 与 `valueType` 不匹配。
- `operation {operation} is not allowed for property: {propertyName}`：操作与属性当前类型或状态不匹配。

## 文件元数据查看

### `POST /api/v1/knowledgeItems/metadata/get`

查看指定文件当前已入库的元数据值。该接口只读；文件元数据写入统一使用 `/api/v1/knowledgeItems/metadata/update`。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 知识库内文件路径 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段；省略时返回该文件全部元数据 |

请求示例：

```json
{
  "knCode": "2",
  "filePath": "/会议纪要/DataCloud平台需求确认会.md",
  "metadataFieldList": ["会议主题", "会议日期"]
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "metadata": {
      "会议主题": {
        "valueType": "string",
        "value": "DataCloud平台需求确认会"
      },
      "会议日期": {
        "valueType": "datetime",
        "value": "2026-05-25T00:00:00"
      }
    }
  }
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
| `topK` | integer | 否 | 兼容参数；未传 `pageSize` 时作为每页条数，默认 500，最大 10000 |
| `pageNum` | integer | 否 | 页码，从 1 开始，默认 1 |
| `pageSize` | integer | 否 | 每页条数，最大 10000；传入时优先于 `topK` |

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
  "metadataFieldList": ["status", "tags", "fileSignature"],
  "pageNum": 1,
  "pageSize": 20
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
    ],
    "total": 1,
    "pageNum": 1,
    "pageSize": 20
  }
}
```

结果固定按 `knowledge_fs_entry.updated_at` 从旧到新排序；更新时间相同时按文件 `kid` 从小到大排序，确保分页稳定。

完整的系统文件属性清单见“系统文件属性”章节。所有系统文件属性均可用于 `where` 过滤，也可通过 `metadataFieldList` 获取值。

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
| `knCodeList` | array[string] | 是 | 知识库范围 |
| `where` | object | 否 | Agent DSL 过滤 AST |
| `searchMode` | string | 是 | 检索模式 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `topK` | integer | 是 | 返回条数，必须大于 0 |
| `fileTypeList` | array[string] | 否 | 按文件类型过滤；向下兼容字段，与 `where` 同时存在时合取 |

> 推荐通过 `where` 中的 `fileType` 系统字段表达文件类型过滤，例如 `{"in": {"fieldName": "fileType", "value": ["md", "pdf"]}}`。`fileTypeList` 仅为兼容老调用方保留，新代码不要依赖。

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
