# knowledgeItems-move

## 功能描述

批量移动或重命名知识库中的文件。接口逐项返回处理结果，并同步维护文件路径、引用关系及依赖路径的相关数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/move` |
| 兼容路径 | `/api/v1/knowledge-items/move` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

移动指定知识库下面的文件或目录。`sourcePath` 为一个或多个源路径，目标通过 `targetDirectoryPath` 或 `targetFilePath` 明确指定。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `sourcePath` | array[string] | 是 | 源路径列表，不能为空；每个路径以 `/` 开头，不包括知识库名称，可指向文件或目录 |
| `targetDirectoryPath` | string | 否 | 目标目录路径，以 `/` 开头，不包括知识库名称；不存在时自动创建。与 `targetFilePath` 二选一 |
| `targetFilePath` | string | 否 | 目标文件路径，以 `/` 开头，不包括知识库名称；仅 `sourcePath` 为单个文件时可用，父目录不存在时自动创建。与 `targetDirectoryPath` 二选一 |
| `overwrite` | boolean | 否 | 是否覆盖已存在目标。默认 `false`；当前版本仅支持 `false`，目标已存在时该移动项失败 |

行为说明：

- `targetDirectoryPath` 与 `targetFilePath` 必须且只能填写一个。
- 使用 `targetDirectoryPath` 时：
  - 目标目录不存在时，服务端自动创建。
  - 每个源移动到该目录下，保留各自名称。
  - 支持单源、多源、文件、目录。
- 使用 `targetFilePath` 时：
  - 仅允许 `sourcePath` 包含一个文件源。
  - 将源文件移动或重命名为 `targetFilePath`。
  - `targetFilePath` 的父目录不存在时，服务端自动创建。
- 目录移动时，目录下所有子目录和文件随目录一起移动。
- 同一请求内每个源路径独立执行；单个源移动失败不影响其它源，失败原因写入 `data[].error`。
- 结构性错误会导致整请求失败，包含：`sourcePath` 为空、路径不以 `/` 开头、路径含 `..` 跨界段、移动知识库根目录 `/`、同一批次内 `sourcePath` 重复、`targetDirectoryPath` 与 `targetFilePath` 同时填写或同时缺失、目录移动到自身或子目录下、`targetFilePath` 用于多源或目录源。
- 目标路径或最终落点已存在时，该源移动失败；当前版本不覆盖已有文件或目录。
- 移动源 Markdown 文件不会改变其中未解析引用的待匹配路径；未解析引用仍按导入时解析出的路径等待后续上传。
- Markdown 中已经解析成功的文件引用不会因移动失效；读取文件、下载 Markdown、知识检索返回内容时，会按目标文件当前路径输出引用。

## 请求示例（移动并重命名单个文件）

```json
{
  "knCode": "1",
  "sourcePath": ["/制度/人事/考勤制度.pdf"],
  "targetFilePath": "/归档/人事/考勤制度.pdf",
  "overwrite": false
}
```

## 请求示例（批量移动文件和目录到目录 `/归档/人事`，目录不存在时自动创建）

```json
{
  "knCode": "1",
  "sourcePath": [
    "/制度/人事/考勤制度.pdf",
    "/制度/人事/图片"
  ],
  "targetDirectoryPath": "/归档/人事"
}
```

成功响应：`resultObject` 为批量结果。

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "sourcePath": "/制度/人事/考勤制度.pdf",
        "targetPath": "/归档/人事/考勤制度.pdf",
        "success": true,
        "error": null
      }
    ],
    "summary": { "total": 1, "succeeded": 1, "failed": 0 }
  }
}
```

`data` 元素字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sourcePath` | string | 本次移动的源路径 |
| `targetPath` | string \| null | 该源路径实际移动后的目标路径；失败时可为 `null` |
| `success` | boolean | 是否移动成功 |
| `error` | string \| null | 失败原因；成功时为 `null` |

`summary` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `total` | integer | 本次请求处理的移动项总数 |
| `succeeded` | integer | 成功数 |
| `failed` | integer | 失败数 |

部分成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "sourcePath": "/制度/人事/考勤制度.pdf",
        "targetPath": "/归档/人事/考勤制度.pdf",
        "success": true,
        "error": null
      },
      {
        "sourcePath": "/制度/人事/不存在.pdf",
        "targetPath": null,
        "success": false,
        "error": "source path not found: /制度/人事/不存在.pdf"
      }
    ],
    "summary": { "total": 2, "succeeded": 1, "failed": 1 }
  }
}
```

整请求失败响应示例：

- `request validation failed`：请求体结构错误或 `sourcePath` 为空。
- `unsafe path`：路径含 `..` 跨界段。
- `cannot move root directory`：尝试移动知识库根目录 `/`。
- `exactly one of targetDirectoryPath or targetFilePath is required`：目标目录路径和目标文件路径必须且只能填写一个。
- `targetFilePath requires exactly one file source`：`targetFilePath` 只能用于单个文件源。
- `target path must not be inside source directory`：目录移动目标位于源目录内部。

```json
{
  "resultCode": "-1",
  "resultMsg": "target path must not be inside source directory",
  "resultObject": {}
}
```

## 特殊逻辑

- `targetDirectoryPath` 和 `targetFilePath` 必须二选一。
- 批量移动按源路径返回逐项成败，单项失败不伪造其他项结果。
- 目标父目录不存在时自动创建；当前不支持 `overwrite=true`。
- 移动 Markdown 文件或目标时会维护逻辑引用路径。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `sourcePath` 中每个元素都必须是以 `/` 开头的库内文件或目录路径。
- `targetDirectoryPath` 是库内目标目录；不存在时由服务递归创建。
- `targetFilePath` 只能用于单源文件移动，是包含文件名的完整库内路径。

---

[返回 API 导航](../README.md)
