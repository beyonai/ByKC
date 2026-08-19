# knowledgeItems-searchFile

## 功能描述

按查询文本召回最相关的文件级结果，用于只关心文件而非具体文本分块的场景。该接口保留既有文件检索能力，返回匹配文件及其相关性信息。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/searchFile` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

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

## 请求示例

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

## 成功响应示例

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

## 失败响应示例

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

## 特殊逻辑

- 返回粒度固定为文件，会将切片召回聚合到文件结果。
- DSL 校验失败时返回 `DSL_VALIDATION_ERROR` 和可定位的 `errorList`。

## 路径与定位规则

- `knCodeList` 是知识库编码字符串列表。

---

[返回 API 导航](../README.md)
