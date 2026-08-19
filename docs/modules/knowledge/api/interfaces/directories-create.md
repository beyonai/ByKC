# directories-create

## 功能描述

在指定知识库中创建一个目录节点，用于组织知识文件和下级目录。接口支持按完整目录路径创建，并对已存在的同路径目录提供幂等处理。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/directories/create` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

在指定知识库下面创建目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需创建的目录路径，以 `/` 开头，不包括知识库名称，支持递归创建 |
| `directoryDescription` | string | 否 | 目录描述 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤",
  "directoryDescription": "考勤制度目录"
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

- 父目录不存在时递归创建。
- 目标目录已存在时按幂等成功处理，不创建重复记录。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
