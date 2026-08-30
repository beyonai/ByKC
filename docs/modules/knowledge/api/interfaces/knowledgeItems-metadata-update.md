# knowledgeItems-metadata-update

## 功能描述

批量新增、修改或删除指定知识文件或目录的元数据字段。接口对整批变更执行类型及字段约束校验，并以原子方式提交有效更新。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/metadata/update` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

文件和目录元数据的统一写接口。同一个请求只操作一个条目，可批量执行多个元数据操作，统一覆盖新增、修改和删除场景。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 知识库内文件或目录路径，以 `/` 开头；字段名为兼容旧接口而保留 |
| `operationList` | array[object] | 是 | 非空元数据操作列表 |

`operationList` 单项字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `propertyName` | string | 是 | 自定义元数据属性名；不允许使用系统文件属性 |
| `operation` | string | 是 | `set`、`unset`、`append`、`remove` 或 `clear` |
| `valueType` | string | 条件必填 | `set` 时必填；其他操作不传 |
| `value` | any | 条件必填 | `set`、`append`、`remove` 时必填；`unset`、`clear` 时不传 |

`operationList` 中同一个 `propertyName` 只能出现一次。

### 操作语义

| 操作 | 适用类型 | 语义 |
| --- | --- | --- |
| `set` | 全部类型 | 属性不存在时新增，已存在时整值覆盖；允许通过显式 `valueType` 变更类型 |
| `unset` | 全部类型 | 删除整个属性；属性不存在时也视为成功 |
| `append` | `stringList` | 追加尚未存在的元素，已存在的元素不重复追加 |
| `remove` | `stringList` | 删除指定元素，元素不存在时也视为成功 |
| `clear` | `stringList` | 将属性值置为空列表 `[]`，不删除属性 |

补充规则：

- `set.valueType` 只能是 `string`、`stringList`、`number`、`boolean` 或 `datetime`。
- `set.value` 必须与 `valueType` 一致；`datetime` 使用 ISO 8601 字符串，`stringList` 必须是字符串数组。
- `append.value` 和 `remove.value` 必须是非空字符串数组。
- `append`、`remove` 和 `clear` 要求属性已存在且当前类型为 `stringList`。
- 不接受 `null`；删除属性必须使用 `unset`。
- `fileName`、`fileType`、`fileSize`、`mimeType`、`createdAt`、`updatedAt`、`fileSignature` 和 `filePath` 是只读系统字段，不允许写入。
- `set` 变更属性类型时，旧类型值会被删除，同一文件下一个 `propertyName` 最多只有一个当前值。

### 请求示例

```json
{
  "knCode": "2",
  "filePath": "/制度/人事/续签流程.md",
  "operationList": [
    {
      "propertyName": "status",
      "operation": "set",
      "valueType": "string",
      "value": "active"
    },
    {
      "propertyName": "tags",
      "operation": "append",
      "value": ["contract", "renewal"]
    },
    {
      "propertyName": "owner",
      "operation": "unset"
    }
  ]
}
```

### 批量规则

- `operationList` 按整体原子处理，任一操作失败时，本次请求不会保留部分更改。
- `filePath` 必须存在且属于 `knCode` 对应知识库，可以指向文件或目录。
- 所有操作都定义为幂等操作，相同请求可安全重试。
- 更新后的元数据可通过元数据查看、检索和 Markdown 文件下载接口获取。

### 成功响应

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

成功后如需获取文件的完整最新元数据，调用 `POST /api/v1/knowledgeItems/metadata/get`。

### 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "operation append requires an existing stringList value: tags",
  "resultObject": {}
}
```

常见失败原因：

- `request validation failed`：请求字段缺失、类型错误或 `operationList` 为空。
- `knowledge base not found: {knCode}`：知识库不存在。
- `entry not found: {filePath}`：文件或目录不存在。
- `duplicate metadata operation: {propertyName}`：同一属性在 `operationList` 中出现多次。
- `metadata field is read-only: {propertyName}`：尝试修改系统文件属性。
- `metadata value type mismatch: {propertyName}`：`value` 与 `valueType` 不匹配。
- `operation {operation} is not allowed for property: {propertyName}`：操作与属性当前类型或状态不匹配。

## 特殊逻辑

- 整个 `operationList` 在同一事务中校验和提交，其中一项失败时不应部分写入。
- `append/remove/clear` 只适用于 `stringList`；`unset` 在属性不存在时仍视为成功。
- 系统文件属性是保留字段，不允许通过本接口写入。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件或目录路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
