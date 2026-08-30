# listDir

## 功能描述

列出指定知识库目录下的直接子目录和文件。接口提供类似文件系统的目录浏览能力，不递归返回更深层级内容。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/listDir` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

获取指定知识库目录下的所有文件和文件夹。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 目录路径，以 `/` 开头，不包括知识库名称 |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段，支持自定义元数据和系统元数据 |
| `pageNum` | integer | 否 | 页码，从 1 开始；传入 `pageSize` 但未传本字段时默认为 1 |
| `pageSize` | integer | 否 | 每页条数，范围 1 到 10000；与 `pageNum` 均未传时保持全量返回 |

## 请求示例

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "1",
        "name": "/制度/人事/考勤",
        "type": "directory",
        "size": 0,
        "updatedAt": "2026-08-30T10:00:00+08:00",
        "buildStatus": null,
        "buildCurrentStep": null,
        "metadata": {}
      },
      {
        "knCode": "1",
        "name": "/制度/人事/请假制度.pdf",
        "type": "file",
        "size": 245760,
        "updatedAt": "2026-08-30T10:20:30+08:00",
        "buildStatus": "complete",
        "buildCurrentStep": "complete",
        "metadata": {}
      }
    ]
  }
}
```

## 失败响应示例

```json
{
  "resultCode": "-1",
  "resultMsg": "directory not found: /制度/人事",
  "resultObject": {}
}
```

## 特殊逻辑

- 只返回直接子项，不递归遍历整个子树。
- 结果固定按目录优先、完整 `name` 不区分大小写升序排列；名称相同时使用原始名称和内部条目 ID 保证稳定顺序。
- `pageNum` 和 `pageSize` 均未传时不分页，响应保持只有 `data` 的旧结构。分页时响应额外包含 `total`、`pageNum`、`pageSize`。
- 只传 `pageNum` 不传 `pageSize` 时请求校验失败。
- `updatedAt` 使用 ISO 8601 格式。
- `buildStatus`、`buildCurrentStep` 返回文件最新构建任务的状态；目录或尚未创建构建任务的文件返回 `null`。
- `metadata` 始终返回对象。未传或传入空 `metadataFieldList` 时不查询元数据并返回 `{}`；传入时只返回列表中实际存在的字段，全部不存在时仍返回 `{}`。
- 元数据值保持统一的 `{valueType, value}` 结构，不平铺到条目顶层。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `directoryPath` 必须以 `/` 开头，不包含知识库名称，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
