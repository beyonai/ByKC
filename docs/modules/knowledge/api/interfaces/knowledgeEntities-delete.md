# knowledgeEntities-delete

## 功能描述

删除当前知识库中的一个规范实体。若实体通过 `fs_entry_id` 锚定 KnowledgeEntity 文件，服务先复用文件删除流程清理正文、切片、引用和检索投影，再删除规范实体；实体的 aliases 和所有模型动态表中的向量通过外键级联删除。

存在以该实体作为 Subject 的直接子实体时拒绝删除，避免子实体被静默改成无 Subject 实体。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeEntities/delete` |
| 兼容路径 | `/api/v1/knowledge-entities/delete` |

## 请求

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 实体所属知识库编码 |
| `entityId` | integer | 是 | `name_role=canonical` 的实体资产 ID，必须大于 0 |

```json
{
  "knCode": "1",
  "entityId": 123
}
```

## 成功响应

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "deletedEntityCount": 1,
    "deletedAliasCount": 2,
    "deletedFileCount": 1
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 删除统计 |
| `resultObject.deletedEntityCount` | integer | 是 | 删除的规范实体记录数 |
| `resultObject.deletedAliasCount` | integer | 是 | 随规范实体删除的 alias 记录数 |
| `resultObject.deletedFileCount` | integer | 是 | 随规范实体删除的锚定文件数 |

## 失败语义

- `entityId` 是 alias、属于其他知识库或不存在：返回 `resultCode=-1`；
- 仍有直接子实体以它作为 Subject：返回 `resultCode=-1`，应先迁移或删除子实体；
- 文件删除失败：规范实体不会被删除，可以重试；
- 文件已删除但实体删除失败：文件锚点已被清空，重试会继续删除实体。

HTTP 响应沿用知识模块统一信封，业务失败仍使用 HTTP 200，以 `resultCode` 判断。

---

[返回 API 导航](../README.md)
