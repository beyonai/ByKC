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
| `metadata` | object | 否 | 写入最终目录及本次递归自动创建的父目录，值类型按 Markdown YAML front matter 规则推断 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤",
  "directoryDescription": "考勤制度目录",
  "metadata": {
    "owner": "HR",
    "tags": ["制度", "考勤"]
  }
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

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功，`-1` 表示失败 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 成功时为空对象；本接口不返回额外 data |

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
- `metadata` 应用于 `directoryPath` 指定的最终目录，也应用于本次递归自动创建的中间目录；已存在的父目录不会被修改。
- 目标目录已存在时不创建重复记录；如传入 `metadata`，仍对指定字段执行幂等 upsert。
- `metadata` 不允许写入 `fileName`、`filePath`、`fileSize`、`fileType`、`mimeType`、`fileSignature`、`createdAt`、`updatedAt` 等只读系统字段。
- 目录创建和元数据写入在同一数据库事务中提交或回滚。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
