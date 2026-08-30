# knowledgeItems-metadata-get

## 功能描述

读取指定知识文件或目录的业务元数据和系统元数据。调用方可指定返回字段，用于检查元数据值、类型及后续检索过滤条件。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/metadata/get` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

查看指定文件或目录当前已入库的元数据值。该接口只读；元数据写入统一使用 `/api/v1/knowledgeItems/metadata/update`。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 知识库内文件或目录路径；字段名为兼容旧接口而保留 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段；省略时返回该条目全部元数据 |

## 请求示例

```json
{
  "knCode": "2",
  "filePath": "/会议纪要/DataCloud平台需求确认会.md",
  "metadataFieldList": ["会议主题", "会议日期"]
}
```

## 成功响应示例

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

- 省略 `metadataFieldList` 时返回该文件或目录的全部自定义元数据和系统属性。
- 显式字段列表可同时包含自定义属性和系统属性。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件或目录路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
