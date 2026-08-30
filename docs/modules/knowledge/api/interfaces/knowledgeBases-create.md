# knowledgeBases-create

## 功能描述

创建一个新的知识库及其基础存储空间。接口登记知识库标识和描述信息，并初始化后续目录、文件和检索能力所需的基础数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeBases/create` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

创建知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knName` | string | 是 | 知识库名称 |
| `knDescription` | string | 否 | 知识库描述 |

## 请求示例

```json
{
  "knName": "人力制度知识库",
  "knDescription": "公司人事制度与流程文档"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "1",
    "knName": "人力制度知识库",
    "knDescription": "公司人事制度与流程文档"
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 新建知识库信息 |
| `resultObject.knCode` | string | 是 | 新建知识库编码 |
| `resultObject.knName` | string | 是 | 知识库名称 |
| `resultObject.knDescription` | string \| null | 是 | 知识库描述；未设置时为 `null` |

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "knowledge base name already exists: 人力制度知识库",
  "resultObject": {}
}
```

## 特殊逻辑

- 知识库名称全局唯一；重复名称不会复用原记录。
- `knCode` 由服务生成，调用方不应预设其数值。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。

---

[返回 API 导航](../README.md)
