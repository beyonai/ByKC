# directories-delete

## 功能描述

删除指定知识库中的目录及其受影响内容。接口在执行前校验目录存在性和删除约束，并按目录删除规则处理下级目录、文件及相关数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/directories/delete` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

删除指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需删除的目录路径，以 `/` 开头，不包括知识库名称 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤"
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
  "resultMsg": "directory not found: /制度/人事/考勤",
  "resultObject": {}
}
```

## 特殊逻辑

- 目录删除是子树删除，会处理其下文件、子目录、索引、引用和存储对象。
- 目标目录以及所有后代文件、目录的自定义元数据一并软删除；删除后不能再通过 metadata get/update、listDir、glob 或 metadataSearch 访问。
- 子树中的文件若是规范实体锚点，同一事务只清空对应 `knowledge_entity.fs_entry_id`；实体、alias 和实体向量继续保留。
- 删除根目录或使用越界路径会被拒绝。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
