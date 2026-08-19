# knowledgeItems-metadataSearch

## 功能描述

仅依据文件路径、系统字段和业务元数据筛选知识文件，不执行正文语义召回。接口适合精确查询满足 Agent DSL 条件的文件集合。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/metadataSearch` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

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

## 请求示例

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

## 失败响应示例

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

## 特殊逻辑

- 该接口只执行结构化过滤，不调用全文或向量检索。
- `pageSize` 优先于兼容参数 `topK`；二者均不传时使用默认值。

## 路径与定位规则

- `knCodeList` 是知识库编码字符串列表。

---

[返回 API 导航](../README.md)
