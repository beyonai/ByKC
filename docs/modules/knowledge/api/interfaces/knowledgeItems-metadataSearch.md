# knowledgeItems-metadataSearch

## 功能描述

仅依据路径、系统字段和业务元数据筛选知识库中的文件和目录，不执行正文语义召回。接口适合精确查询满足 Agent DSL 条件的条目集合。

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

Agent DSL 版纯元数据检索，同一份请求同时查询文件和目录。请求模型不定义任何资源类型参数；资源类型仅由响应项的 `type` 字段标识。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCodeList` | array[string] | 是 | 非空知识库编码列表 |
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
        "type": "file",
        "metadata": {
          "status": {
            "valueType": "string",
            "value": "active"
          },
          "tags": {
            "valueType": "stringList",
            "value": ["hr", "contract"]
          },
          "fileSignature": {
            "valueType": "string",
            "value": "b6f1d2c3..."
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

`type` 是纯出参字段，值为 `file` 或 `directory`。为保持响应兼容，文件和目录的路径均继续使用 `filePath` 字段。

结果固定按 `knowledge_fs_entry.updated_at` 从旧到新排序；更新时间相同时按条目 `kid` 从小到大排序，确保文件和目录共同分页时稳定。`total` 是两类条目的匹配总数。

完整的系统文件属性清单见“系统文件属性”章节。所有系统文件属性均可用于 `where` 过滤，也可通过 `metadataFieldList` 获取值。

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 元数据检索结果 |
| `resultObject.data` | array[object] | 是 | 当前页条目；无结果时为空数组 |
| `resultObject.data[].knCode` | string | 是 | 条目所属知识库编码 |
| `resultObject.data[].filePath` | string | 是 | 文件或目录的知识库内完整路径 |
| `resultObject.data[].type` | string | 是 | 条目类型：`file` 或 `directory` |
| `resultObject.data[].metadata` | object | 是 | 请求返回的元数据映射；无值时为空对象 |
| `resultObject.data[].metadata.<propertyName>` | object | 否 | 某个实际返回的元数据属性 |
| `resultObject.data[].metadata.<propertyName>.valueType` | string | 是 | 属性类型 |
| `resultObject.data[].metadata.<propertyName>.value` | any | 是 | 属性值，实际 JSON 类型由 `valueType` 决定 |
| `resultObject.total` | integer | 是 | 文件和目录的匹配总数 |
| `resultObject.pageNum` | integer | 是 | 当前页码 |
| `resultObject.pageSize` | integer | 是 | 每页条数 |

失败时 `resultObject` 可进一步包含以下校验明细：

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultObject.errorCode` | string | 否 | 结构化错误码 |
| `resultObject.errorList` | array[object] | 否 | DSL 校验错误列表 |
| `resultObject.errorList[].path` | string | 是 | 错误所在请求字段路径 |
| `resultObject.errorList[].code` | string | 是 | 错误项代码 |
| `resultObject.errorList[].message` | string | 是 | 错误项说明 |

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
- 请求结构不区分文件和目录，不增加 `resourceType`、`entryType` 或 `isDirectory` 等参数。
- 目录的 `fileSize`、`mimeType`、`fileSignature`、`fileType` 分别为 `0`、`null`、`null`、空字符串；其他系统字段按目录条目的实际值返回。
- `pageSize` 优先于兼容参数 `topK`；二者均不传时使用默认值。

## 路径与定位规则

- `knCodeList` 是知识库编码字符串列表。

---

[返回 API 导航](../README.md)
