# 元数据与 Agent DSL

本文定义元数据类型、系统字段、Agent DSL 语法、每种类型支持/不支持的操作符以及校验限制。

当前支持以下元数据类型：

- `string`
- `stringList`
- `number`
- `boolean`
- `datetime`

`datetime` 输入使用 ISO 8601 字符串，建议显式携带时区偏移；未携带偏移时按 `DB_TIMEZONE` 解释。查询和下载接口会将时间统一转换到 `DB_TIMEZONE` 指定的时区，默认为 `Asia/Shanghai`。例如 `2026-01-01T00:00:00Z` 返回为 `2026-01-01T08:00:00+08:00`。系统字段 `createdAt` 和 `updatedAt` 使用相同规则。

## 系统文件属性

系统属性由服务根据 `knowledge_fs_entry` 文件或目录记录提供，不需要通过元数据属性管理接口注册。调用方应将这些名称视为保留字段，避免在 Markdown front matter 等自定义元数据中复用。当前共有以下 8 个字段：

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

目录沿用上述字段名以保持接口兼容。其 `fileSize` 为 `0`，`mimeType` 和 `fileSignature` 为 `null`，`fileType` 为空字符串，其他字段取目录条目的实际值。

使用约定：

- `POST /api/v1/knowledgeItems/metadata/get`：可在 `metadataFieldList` 中指定系统文件属性；省略该参数时，系统文件属性会与自定义元数据一起返回。
- `POST /api/v1/knowledgeItems/metadataSearch`：系统文件属性既可用于 `where` 过滤，也可通过 `metadataFieldList` 返回。
- `POST /api/v1/knowledgeItems/search` 和 `POST /api/v1/knowledgeItems/searchFile`：系统文件属性可用于 `where` 过滤；需要返回属性值时可通过 `metadataFieldList` 指定。
- 系统文件属性遵循其元数据类型对应的 DSL 操作符规则。例如 `fileSignature` 支持 `eq` 精确匹配，`fileSize` 支持数值比较，`createdAt` 和 `updatedAt` 支持时间比较。
- 字段名使用现有的 `createdAt` 和 `updatedAt`，不提供 `createTime`、`updateTime` 等别名。

## 检索模式约定

当前支持以下检索模式：

- `fullTextRecall`
- `embedding`
- `mixedRecall`

## 返回粒度约定

当前支持以下返回粒度：

- `file`
- `chunk`

## DSL 使用说明

本文档当前实现范围中的检索接口只涉及 Agent DSL。

Business DSL 及其对应接口已从当前实现范围中抽离，并标记为废弃候选，当前阶段不做。

## Agent DSL 是什么

Agent DSL 是一套专门用于定义 `where` 的结构化过滤表达式。调用方通过 `where` 传入一棵 JSON AST，服务端会先做 DSL 校验，再把它编译成内部查询条件，最终落到 SQL、全文检索或向量检索链路上。

服务端处理链路如下：

```text
Agent DSL
  -> Internal Query Plan
  -> SQL / FTS / Vector Retrieval
```

本章节只定义 `where` 本身的语法、语义、约束和限制；具体哪些接口支持 `where`、`where` 是否必填、以及可用字段范围，请以各接口定义为准。

## Agent DSL 解决什么问题

它主要解决“先按结构化条件缩小范围，再做检索或直接返回结果”的问题，典型场景包括：

- 精确过滤：例如“只看 `status=active` 的对象”
- 组合条件过滤：例如“同时满足状态、时间、标签等多个条件”
- 在更大检索流程前先缩小候选范围

## 如何使用

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

## 表达式长什么样

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

## 按类型划分的叶子操作符与示例

以下各表按“字段类型”整理当前实现中可用的叶子操作符。这里描述的是 `fieldName` 对应字段类型与操作符的关系，不展开具体字段清单。近似语义的操作符放在同一行展示。

### string

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

### stringList

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `contains` | `{"contains": {"fieldName": "tags", "value": "contract"}}` | `tags` 列表中包含元素 `contract`。 |
| `exists` | `{"exists": {"fieldName": "tags"}}` | 文件上存在 `tags` 字段且值非空。 |

不支持：

- `eq` / `ne`
- `in`
- `gt` / `gte` / `lt` / `lte`
- `prefix` / `wildcard`

### number

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "priority", "value": 5}}` | `priority` 精确等于 `5`；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "priority", "value": [1, 3, 5]}}` | `priority` 属于给定数值集合之一。 |
| `exists` | `{"exists": {"fieldName": "priority"}}` | 文件上存在 `priority` 字段且值非空。 |
| `gt` / `gte` / `lt` / `lte` | `{"gt": {"fieldName": "priority", "value": 5}}` | `priority > 5`；其余分别表示 `>=`、`<`、`<=`。 |

不支持：

- `contains`
- `prefix` / `wildcard`

### boolean

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "archived", "value": true}}` | `archived` 精确等于 `true`；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "archived", "value": [true]}}` | `archived` 属于给定布尔集合之一。 |
| `exists` | `{"exists": {"fieldName": "archived"}}` | 文件上存在 `archived` 字段且值非空。 |

不支持：

- `contains`
- `gt` / `gte` / `lt` / `lte`
- `prefix` / `wildcard`

### datetime

| 叶子操作符 | 用例 | 用例含义 |
| --- | --- | --- |
| `eq` / `ne` | `{"eq": {"fieldName": "publishedAt", "value": "2026-05-15T10:00:00Z"}}` | 时间字段精确等于给定 ISO 8601 时间点；`ne` 表示不等于该值。 |
| `in` | `{"in": {"fieldName": "publishedAt", "value": ["2026-05-01T00:00:00Z", "2026-05-15T10:00:00Z"]}}` | 时间字段属于给定时间点集合之一。 |
| `exists` | `{"exists": {"fieldName": "publishedAt"}}` | 文件上存在该时间字段且值非空。 |
| `gt` / `gte` / `lt` / `lte` | `{"gte": {"fieldName": "publishedAt", "value": "2026-01-01T00:00:00Z"}}` | 时间字段晚于或等于给定时间点；其余分别表示严格大于、严格小于、小于等于。 |

不支持：

- `contains`
- `prefix` / `wildcard`

## `prefix` / `wildcard` 的使用规则

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

## 叶子值类型校验

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

## 当前局限性

当前实现是“受控 DSL”，目的是让调用方能稳定地表达常见过滤条件，而不是提供一门无限扩展的查询语言。主要局限如下：

- 仅支持 `and` / `or` / `not` 三种布尔操作
- 仅支持 11 个叶子操作符，不支持 `between`、`regex`、脚本表达式等
- 最大布尔嵌套深度为 `3`
- 最大叶子条件数为 `12`
- `stringList` 只支持 `contains` 和 `exists`

## 使用建议

1. 纯元数据检索
   - 先从简单叶子条件开始
   - 确认单个条件正确后，再组合 `and` / `or` / `not`
2. 通配匹配
   - 只需要“某前缀开头”时优先用 `prefix`
   - 只有确实需要 `*` / `?` 语义时再使用 `wildcard`
3. 条件复杂度
   - 尽量避免过深嵌套
   - 尽量控制叶子条件数量，便于排查错误

## DSL 错误修正

DSL 校验失败时，优先根据以下字段修正请求：

- `errorList[].path`
- `errorList[].code`
- `errorList[].message`

---

[返回 API 导航](README.md)
