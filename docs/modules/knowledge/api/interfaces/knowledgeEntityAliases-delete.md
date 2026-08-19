# knowledgeEntityAliases-delete

## 功能描述

删除规范实体的一条 alias。该操作不删除规范实体或 KnowledgeEntity 文件，并立即使当前模型的 `full` 向量失效，再异步或就地重建；`local_name` 向量不受影响。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeEntities/aliases/delete` |
| 兼容路径 | `/api/v1/knowledge-entities/aliases/delete` |

## 请求

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 实体所属知识库编码 |
| `entityId` | integer | 是 | alias 所属规范实体 ID，必须大于 0 |
| `aliasId` | integer | 是 | 待删除 alias 记录 ID，必须大于 0 |

```json
{
  "knCode": "1",
  "entityId": 123,
  "aliasId": 456
}
```

## 成功响应

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "deletedEntityCount": 0,
    "deletedAliasCount": 1,
    "deletedFileCount": 0
  }
}
```

`aliasId` 不属于指定 `entityId`、不是 alias、属于其他知识库或不存在时，返回 `resultCode=-1`。HTTP 状态和统一错误信封见[通用约定](../common.md)。

---

[返回 API 导航](../README.md)
