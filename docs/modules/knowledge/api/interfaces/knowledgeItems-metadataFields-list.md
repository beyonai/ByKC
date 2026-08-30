# knowledgeItems-metadataFields-list

## 功能描述

枚举指定知识库中实际使用的自定义元数据字段。字段统计同时覆盖文件和目录，不需要传入资源类型。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/metadataFields/list` |

## 请求参数

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCodeList` | array[string] | 是 | 非空知识库编码列表 |

## 请求示例

```json
{
  "knCodeList": ["1", "2"]
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
        "propertyName": "owner",
        "valueType": "string",
        "description": null,
        "extParams": null
      }
    ]
  }
}
```

## 特殊逻辑

- 只返回当前未删除的元数据值所使用的自定义字段。
- 某字段只在目录上使用时也会返回。
- 文件或目录删除后，若没有其他有效条目使用该字段，该字段不再返回。
- 系统属性不依赖实际元数据值，不在本接口中枚举；清单见 [元数据与 Agent DSL](../metadata-and-dsl.md)。

---

[返回 API 导航](../README.md)
