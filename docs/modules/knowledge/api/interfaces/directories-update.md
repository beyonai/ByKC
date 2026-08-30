# directories-update

## 功能描述

修改指定目录的名称和可选元数据。接口会同步维护受影响文件与子目录的逻辑路径，并校验同级名称冲突。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/directories/update` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

修改指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需要修改的目录路径，以 `/` 开头，不包括知识库名称 |
| `directoryName` | string | 是 | 新目录名称，仅修改 `directoryPath` 最后一个层级的名称 |
| `metadata` | object | 否 | 需要 upsert 的目录自定义元数据；未传时保留全部已有元数据 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤",
  "directoryName": "考勤管理",
  "metadata": {
    "owner": "HR",
    "retentionYears": 7
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

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "directory name already exists under parent: 考勤管理",
  "resultObject": {}
}
```

## 特殊逻辑

- 只修改最后一级目录名，不改变父目录。
- 重命名会同步更新整个子树的逻辑路径及相关引用。
- 传入 `metadata` 时只 upsert 本次字段，未指定字段保留原值。
- `metadata` 不允许写入 `fileName`、`filePath`、`fileSize`、`fileType`、`mimeType`、`fileSignature`、`createdAt`、`updatedAt` 等只读系统字段。
- 重命名与元数据写入在同一数据库事务中提交或回滚。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
