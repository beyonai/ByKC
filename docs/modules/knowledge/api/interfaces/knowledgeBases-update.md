# knowledgeBases-update

## 功能描述

更新指定知识库的名称、描述等可修改属性。接口只调整知识库自身信息，不用于修改知识文件内容。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeBases/update` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

修改知识库名称或描述。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `knName` | string | 否 | 新知识库名称 |
| `knDescription` | string | 否 | 新知识库描述 |

## 请求示例

```json
{
  "knCode": "1",
  "knName": "人力制度知识库（新版）",
  "knDescription": "更新后的公司人事制度与流程文档"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "knowledge base not found: 1",
  "resultObject": {}
}
```

## 特殊逻辑

- 名称修改后仍执行全局唯一性检查。
- 只更新请求中提供的字段，未提供字段保持不变。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。

---

[返回 API 导航](../README.md)
