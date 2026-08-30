# knowledgeItems-search

## 功能描述

在一个或多个知识库中执行 chunk 级语义检索，并可结合 Agent DSL 元数据条件过滤结果。接口支持不同召回模式，并按需返回命中文本、评分和指定元数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/search` |
| 兼容路径 | `/api/v1/knowledge-items/search` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

基于 `knowledgeItems-search` 的 Agent DSL 版 chunk 级语义检索。

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
  "metadataFieldList": ["status"],
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

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 切片检索结果 |
| `resultObject.data` | array[object] | 是 | 命中的切片列表；无结果时为空数组 |
| `resultObject.data[].knCode` | string | 是 | 命中切片所属知识库编码 |
| `resultObject.data[].filePath` | string | 是 | 命中切片所属文件路径 |
| `resultObject.data[].chunkId` | integer | 是 | 切片 ID |
| `resultObject.data[].chunkNo` | integer | 是 | 切片在文件中的序号 |
| `resultObject.data[].chunkText` | string | 是 | 切片正文 |
| `resultObject.data[].score` | number | 是 | 相关性得分 |
| `resultObject.data[].startLine` | integer | 是 | 切片起始行 |
| `resultObject.data[].endLine` | integer | 是 | 切片结束行 |
| `resultObject.data[].metadata` | object | 是 | 请求返回的元数据映射；未请求或无值时为空对象 |
| `resultObject.data[].metadata.<propertyName>` | object | 否 | 某个实际返回的元数据属性 |
| `resultObject.data[].metadata.<propertyName>.valueType` | string | 是 | 属性类型 |
| `resultObject.data[].metadata.<propertyName>.value` | any | 是 | 属性值，实际 JSON 类型由 `valueType` 决定 |

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

实际 `resultMsg` 会说明参数、资源状态或依赖失败原因；请勿只根据文案分支处理。

## 特殊逻辑

- `where` 先缩小候选范围，再执行指定的全文/向量/混合检索。
- `fileTypeList` 是向下兼容字段；与 `where` 同时存在时取与。

## 路径与定位规则

- `knCodeList` 是知识库编码字符串列表。

---

[返回 API 导航](../README.md)
