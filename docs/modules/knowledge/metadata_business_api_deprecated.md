# 知识模块业务 DSL 检索接口归档

## 文档范围

本文档归档 by-qa 元数据检索方案中曾设计过、但当前阶段已抽离且暂不实现的业务 DSL 接口。

当前状态：

- 文档用途：设计归档
- 实现状态：暂不实现
- 对外状态：废弃候选，不作为当前接口契约
- 路径状态：文中历史候选路径当前已被 Agent DSL 版接口占用

## 归档背景

这组接口原本用于承接 Business DSL，即面向业务系统调用的统一检索 DSL。

后续范围收敛后，当前阶段只保留 Agent DSL 对外接口，因此以下接口从主文档中抽离，单独留档：

- `/api/v1/knowledgeItems/metadataSearch`
- `/api/v1/knowledgeItems/searchChunk`
- `/api/v1/knowledgeItems/searchFile`

## Business DSL 归档说明

Business DSL 的原始定位：

- 面向业务系统调用
- 表达力高于 Agent DSL
- 支持 `filter`
- 支持 `page.offset` / `page.limit`
- 支持更完整的排序与返回字段控制

当前仅保留为设计归档，不进入本阶段实现范围。

阅读说明：

- 本文档中的路径仅用于保留历史 Business DSL 契约设计
- 即使路径与当前生效接口相同，也不表示当前服务会按本文档的请求或响应结构处理

## 归档接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/metadataSearch` | 业务版纯元数据检索 |
| `POST` | `/api/v1/knowledgeItems/searchChunk` | 业务版 chunk 级语义检索 |
| `POST` | `/api/v1/knowledgeItems/searchFile` | 业务版文件级语义检索 |

## 业务版纯元数据检索

### `POST /api/v1/knowledgeItems/metadataSearch`

业务版纯元数据检索，只返回文件名级结果。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `filter` | object | 否 | Business DSL 过滤 AST |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `sortList` | array[object] | 否 | 排序规则 |
| `page` | object | 是 | 分页信息 |

`sortList` 单项字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `fieldName` | string | 是 | 排序字段 |
| `order` | string | 是 | `asc` 或 `desc` |

`page` 字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `offset` | integer | 是 | 起始偏移，必须大于等于 0 |
| `limit` | integer | 是 | 返回条数，必须大于 0 |

请求示例：

```json
{
  "knCodeList": ["2"],
  "filter": {
    "and": [
      {"eq": {"fieldName": "status", "value": "active"}},
      {"contains": {"fieldName": "tags", "value": "contract"}}
    ]
  },
  "metadataFieldList": ["status", "tags"],
  "sortList": [
    {"fieldName": "updatedAt", "order": "desc"}
  ],
  "page": {
    "offset": 0,
    "limit": 20
  }
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
        "fileName": "续签流程.md",
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

## 业务版语义检索

### `POST /api/v1/knowledgeItems/searchChunk`

业务版 chunk 级语义检索。

说明：

- 可以考虑直接在现有 `/api/v1/knowledgeItems/search` 原接口上扩展业务 DSL 能力
- 如果原接口扩展成本可控，则当前接口可以不单独新增

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 检索文本 |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `filter` | object | 否 | Business DSL 过滤 AST |
| `returnMode` | string | 是 | 固定为 `chunk` |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `sortList` | array[object] | 否 | 排序规则 |
| `page` | object | 是 | 分页信息 |
| `searchMode` | string | 是 | 检索模式 |

请求示例：

```json
{
  "query": "续签流程",
  "filter": {
    "eq": {
      "fieldName": "status",
      "value": "active"
    }
  },
  "returnMode": "chunk",
  "metadataFieldList": ["status"],
  "page": {
    "offset": 0,
    "limit": 10
  },
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

业务版文件级语义检索。

实现说明：

- 先按 chunk 粒度召回 `page.limit * 50` 条候选结果
- 再按 `knCode + filePath` 聚合为文件级结果
- 最终最多返回 `page.limit` 个文件

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 检索文本 |
| `knCodeList` | array[string] | 否 | 知识库范围 |
| `filter` | object | 否 | Business DSL 过滤 AST |
| `returnMode` | string | 是 | 固定为 `file` |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `sortList` | array[object] | 否 | 排序规则 |
| `page` | object | 是 | 分页信息 |
| `searchMode` | string | 是 | 检索模式 |

请求示例：

```json
{
  "query": "续签流程",
  "filter": {
    "eq": {
      "fieldName": "status",
      "value": "active"
    }
  },
  "returnMode": "file",
  "metadataFieldList": ["status", "tags"],
  "page": {
    "offset": 0,
    "limit": 10
  },
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

## 归档结论

这组接口当前不进入实现范围，但保留归档，便于后续在需要恢复业务 DSL 时继续演进，而不必重新整理历史设计。
