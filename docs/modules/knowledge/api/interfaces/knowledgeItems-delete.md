# knowledgeItems-delete

## 功能描述

删除知识库中的一个或多个文件。接口按文件路径定位目标，并清理文件正文、构建数据、检索索引及相关引用数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/delete` |
| 兼容路径 | `/api/v1/knowledge-items/delete` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

删除指定知识库下面的文档。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需删除的文档全路径，以 `/` 开头，不包括知识库名称 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/考勤制度.pdf"
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
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

实际 `resultMsg` 会说明参数、资源状态或依赖失败原因；请勿只根据文案分支处理。

## 特殊逻辑

- 除文件记录和对象存储外，还会处理切片、构建数据以及入向/出向引用状态。
- 删除的 Markdown 目标可能使其他文件的引用转为 `broken`。
- 若文件是规范实体的当前锚点，同一数据库事务会把 `knowledge_entity.fs_entry_id` 置空；实体、alias 和实体向量继续保留。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
