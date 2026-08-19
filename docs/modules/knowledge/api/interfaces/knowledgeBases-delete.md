# knowledgeBases-delete

## 功能描述

删除指定知识库及其关联资源。接口用于整体下线知识库，并按删除规则清理目录、文件、索引及其他从属数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeBases/delete` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

删除指定知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |

## 请求示例

```json
{
  "knCode": "1"
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

- 删除知识库会级联清理其目录、文件、切片、构建记录和受管存储对象。
- 当前 MVP 不在知识库删除后积累实体资产：知识库行和文件执行逻辑删除时，同一事务会物理删除该 `knowledge_base_id` 下的规范实体；aliases 和所有动态模型表中的实体向量随外键级联删除。
- 该操作不可撤销，调用方应在上层做确认和权限控制。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。

---

[返回 API 导航](../README.md)
