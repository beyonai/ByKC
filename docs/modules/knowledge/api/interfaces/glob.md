# glob

## 功能描述

在指定知识库目录范围内按 Glob 模式匹配文件和目录。该接口适合 Agent 或批处理调用方快速筛选符合路径模式的知识条目。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/glob` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

基于路径模式匹配查找指定知识库下面的文件或目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `pathRule` | string | 是 | 匹配模式规则，以 `/` 开头，不包括知识库名称；`*` 仅匹配单层路径段，不支持 `**` 多层目录匹配 |

匹配规则：

- `*` 只匹配一层目录或文件名中的任意字符，不跨 `/`。
- 不支持 `**` 语法匹配多层目录。
- 如需匹配两层目录，需要显式写成类似 `/制度/*/*.pdf`。

## 请求示例

```json
{
  "knCode": "1",
  "pathRule": "/制度/*/*.pdf"
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
        "name": "/制度/人事/请假制度.pdf",
        "type": "file",
        "size": 245760,
        "updatedAt": "2026-08-30T10:20:30+08:00",
        "buildStatus": "complete",
        "buildCurrentStep": "complete",
        "metadata": {}
      },
      {
        "knCode": "1",
        "name": "/制度/法务/合同规范.pdf",
        "type": "file",
        "size": 327680,
        "updatedAt": "2026-08-30T10:21:00+08:00",
        "buildStatus": null,
        "buildCurrentStep": null,
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
  "resultMsg": "pathRule must not be empty",
  "resultObject": {}
}
```

## 特殊逻辑

- `*` 仅匹配单层路径段，当前不支持 `**`。
- 返回项的 `name` 是知识库内完整路径。
- `updatedAt` 使用 ISO 8601 格式。
- `buildStatus`、`buildCurrentStep` 返回文件最新构建任务的状态；目录或尚未创建构建任务的文件返回 `null`。
- `metadata` 始终返回对象；当前未指定元数据字段时返回空对象 `{}`。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `pathRule` 以 `/` 开头；`*` 只匹配单层路径段，不支持 `**` 跨层匹配。

---

[返回 API 导航](../README.md)
