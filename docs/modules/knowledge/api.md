# 知识模块接口说明

## 文档范围

本文档定义知识模块对外开放的接口。

- Base URL：`/api/v1`
- 协议：`HTTP`
- 默认返回：`application/json`
- 上传文档接口请求体：`multipart/form-data`
- 下载文件接口成功响应：`application/octet-stream`

## 通用约定

### 成功响应

除下载文件接口外，其余接口统一使用如下响应信封：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

说明：

- `resultCode`：接口状态码，`0` 表示成功，`-1` 表示失败
- `resultMsg`：接口返回说明
- `resultObject`：业务返回体；无额外返回时返回空对象

### 失败响应

失败时统一返回：

```json
{
  "resultCode": "-1",
  "resultMsg": "request validation failed",
  "resultObject": {}
}
```

### 路径字段约定

- `knCode`：知识库编码
- `directoryPath`：目录路径，以 `/` 开头，不包含知识库名称
- `filePath`：文件路径，以 `/` 开头，不包含知识库名称
- `sourcePath`：移动源路径列表，以 `/` 开头，不包含知识库名称，元素可指向文件或目录
- `targetDirectoryPath`：移动目标目录路径，以 `/` 开头，不包含知识库名称；不存在时自动创建
- `targetFilePath`：移动目标文件路径，以 `/` 开头，不包含知识库名称；仅单源文件移动时可用，父目录不存在时自动创建
- `name`：目录遍历或模式匹配结果中的完整路径

## 接口总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeBases/create` | 创建知识库 |
| `POST` | `/api/v1/knowledgeBases/update` | 修改知识库 |
| `POST` | `/api/v1/knowledgeBases/delete` | 删除知识库 |
| `POST` | `/api/v1/directories/create` | 创建目录 |
| `POST` | `/api/v1/directories/update` | 修改目录 |
| `POST` | `/api/v1/directories/delete` | 删除目录 |
| `POST` | `/api/v1/knowledgeItems/import` | 上传文档 |
| `POST` | `/api/v1/knowledgeItems/delete` | 删除文档 |
| `POST` | `/api/v1/knowledgeItems/move` | 移动文件或目录 |
| `POST` | `/api/v1/listDir` | 获取目录内容 |
| `POST` | `/api/v1/glob` | 按路径模式匹配 |
| `POST` | `/api/v1/readFile` | 读取文件内容 |
| `POST` | `/api/v1/downloadFile` | 下载文件 |
| `POST` | `/api/v1/fileToMarkdown` | 上传文件并同步转换为 Markdown 文件流 |
| `POST` | `/api/v1/fileToMarkdownIndex` | 异步触发知识构建 |
| `POST` | `/api/v1/fileBuildStatus` | 查询文档构建状态 |
| `POST` | `/api/v1/knowledgeItems/search` | 知识检索 |
| `GET` | `/health` | 探活 |

## 知识库管理

### `POST /api/v1/knowledgeBases/create`

创建知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knName` | string | 是 | 知识库名称 |
| `knDescription` | string | 否 | 知识库描述 |

请求示例：

```json
{
  "knName": "人力制度知识库",
  "knDescription": "公司人事制度与流程文档"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "1",
    "knName": "人力制度知识库",
    "knDescription": "公司人事制度与流程文档"
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "knowledge base name already exists: 人力制度知识库",
  "resultObject": {}
}
```

### `POST /api/v1/knowledgeBases/update`

修改知识库名称或描述。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `knName` | string | 否 | 新知识库名称 |
| `knDescription` | string | 否 | 新知识库描述 |

请求示例：

```json
{
  "knCode": "1",
  "knName": "人力制度知识库（新版）",
  "knDescription": "更新后的公司人事制度与流程文档"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "knowledge base not found: 1",
  "resultObject": {}
}
```

### `POST /api/v1/knowledgeBases/delete`

删除指定知识库。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |

请求示例：

```json
{
  "knCode": "1"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

## 目录管理

### `POST /api/v1/directories/create`

在指定知识库下面创建目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需创建的目录路径，以 `/` 开头，不包括知识库名称，支持递归创建 |
| `directoryDescription` | string | 否 | 目录描述 |

请求示例：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤",
  "directoryDescription": "考勤制度目录"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "directory path already exists: /制度/人事/考勤",
  "resultObject": {}
}
```

### `POST /api/v1/directories/update`

修改指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需要修改的目录路径，以 `/` 开头，不包括知识库名称 |
| `directoryName` | string | 是 | 新目录名称，仅修改 `directoryPath` 最后一个层级的名称 |

请求示例：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤",
  "directoryName": "考勤管理"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "directory name already exists under parent: 考勤管理",
  "resultObject": {}
}
```

### `POST /api/v1/directories/delete`

删除指定知识库的目录。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 需删除的目录路径，以 `/` 开头，不包括知识库名称 |

请求示例：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事/考勤"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "directory not found: /制度/人事/考勤",
  "resultObject": {}
}
```

## 文档管理

### `POST /api/v1/knowledgeItems/import`

将文档上传到指定知识库下面。支持单文件上传与 zip 包批量上传，入参不变，由 `fileContent` 文件名是否以 `.zip` 结尾且为合法 zip 自动判定。

行为描述：

- 文件类型：上传不再限制文件类型，任意类型文件均可入库。不可构建的文件类型（非 `txt`、`md`、`markdown`、`csv`、`pdf`、`docx`、`doc`、`pptx`、`ppt`、`xlsx`、`xls`）在后续 `POST /api/v1/fileToMarkdownIndex` 构建时会被标记为「不支持构建」状态，不影响入库。
- Markdown 引用处理（默认动作，无论是否 zip 包）：上传 Markdown 文件时，服务端解析其中的图片引用 `![]()` 与链接引用 `[]()`，将相对路径按当前文件所在目录解析为知识库绝对路径（消除 `.`、`..`；越过知识库根的引用保持不变），并为可管理的文件引用登记稳定引用关系。Markdown 入库内容会保存为内部 `byqa-ref://<id>` token；面向用户的读取、Markdown 下载和知识检索会在输出时解析为目标文件当前路径。未解析或目标已删除的引用回退为用户原始写法。URL（带协议头）与锚点（`#anchor`）保留不变。
- zip 包批量上传：当 `fileContent` 文件名以 `.zip` 结尾且为合法 zip 时，按批量导入处理：
  - 解压后并发上传：非 Markdown 文件先上传，Markdown 文件最后上传，保证 Markdown 引用登记时图片与被引用文档已就位；仍未就位的引用会保留为待解析状态，后续同路径文件上传后自动绑定。
  - zip 内文件上传到 `filePath` 指定的目标目录下，保留 zip 内相对目录结构。
  - 若 zip 内文件在知识库中已存在，则先软删除原文件再上传（覆盖语义）。
  - 自动跳过 macOS 元数据（`__MACOSX`、以 `.` 开头的隐藏条目）与目录条目。
  - 文件名编码兼容：自动识别 zip 条目的 UTF-8 标志位，未设置时按 GBK 还原中文文件名，兼容中文 Windows 资源管理器 / WinRAR / 好压 生成的 zip。
  - 安全限制：单条目解压上限 64 MiB、全部条目解压上限 256 MiB、条目数上限 10000，超出返回失败；越过目标目录或知识库根的路径（含 `..` 跨界）记为失败；解析后同路径的重复条目记为失败。
- 当上传文件为 Markdown 且 `processFrontMatter` 为 `true` 时，服务端会额外解析文档开头的 YAML front matter header。
- 若解析到合法的 YAML front matter header，则会将其中字段按同名 `propertyName` 自动录入为该文件的元数据。
- 该行为适用于类似 Obsidian 文档头的结构化元数据写法。
- 如果已有属性不存在于知识库系统，则该文件导入失败（`success=false`，`error` 含原因）。
- 当 `processFrontMatter` 为 `false` 时，跳过 YAML front matter 解析，不做元数据自动录入。

YAML front matter header 示例：

```yaml
---
title: LLM Wiki 中间层知识构建
aliases:
  - LLM Wiki Middle Layer Construction
  - Karpathy LLM Wiki 中间层设计
tags:
  - llm-wiki
  - knowledge-construction
  - obsidian/pkm
  - knowledge-base/research
doc_type: research
status: active
source: official-research
owner: by-qa
created: 2026-05-11
updated: 2026-05-11
module: karpathy
---
```

请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 单文件上传时为目标文件全路径；zip 上传时为目标目录路径（zip 内文件解压到该目录下），以 `/` 开头，不包括知识库名称 |
| `fileDescription` | string | 否 | 文件描述；zip 批量上传时对所有文件统一使用该描述 |
| `fileContent` | file | 是 | 文件二进制内容；文件名以 `.zip` 结尾且为合法 zip 时触发批量上传 |
| `processFrontMatter` | boolean | 否 | 是否解析 YAML front matter 并自动录入元数据，默认 `true` |

表单示例（单文件）：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/import \
  -F "knCode=1" \
  -F "filePath=/制度/人事/考勤制度.pdf" \
  -F "fileDescription=考勤制度原文" \
  -F "fileContent=@./考勤制度.pdf" \
  -F "processFrontMatter=true"
```

表单示例（zip 批量上传到目录 `/制度/人事`）：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/import \
  -F "knCode=1" \
  -F "filePath=/制度/人事" \
  -F "fileDescription=人事制度批量导入" \
  -F "fileContent=@./人事制度.zip" \
  -F "processFrontMatter=true"
```

成功响应：`resultObject` 为批量结果，单文件上传同样返回单元素列表：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      { "filePath": "/制度/人事/考勤制度.pdf", "success": true, "error": null }
    ],
    "summary": { "total": 1, "succeeded": 1, "failed": 0 }
  }
}
```

`data` 元素字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `filePath` | string | 该文件入库后的全路径 |
| `success` | boolean | 是否导入成功 |
| `error` | string \| null | 失败原因；成功时为 `null` |

`summary` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `total` | integer | 本次上传处理的文件总数 |
| `succeeded` | integer | 成功数 |
| `failed` | integer | 失败数 |

zip 批量上传响应示例（部分成功，含不安全路径）：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      { "filePath": "/制度/人事/考勤制度.pdf", "success": true, "error": null },
      { "filePath": "/制度/escape.md", "success": false, "error": "unsafe path" }
    ],
    "summary": { "total": 2, "succeeded": 1, "failed": 1 }
  }
}
```

失败响应示例（整请求级别失败，`resultCode` 为 `-1`）：

- `invalid zip file`（422）：`fileContent` 文件名以 `.zip` 结尾但不是合法 zip。
- `unsafe path`（422）：单文件上传的 `filePath` 含 `..` 跨界段。
- `file path already exists: /制度/人事/考勤制度.pdf`：单文件上传且目标路径已存在（zip 批量上传为覆盖语义，不会报此错）。

```json
{
  "resultCode": "-1",
  "resultMsg": "file path already exists: /制度/人事/考勤制度.pdf",
  "resultObject": {}
}
```

注：zip 批量上传时单个文件的导入失败不会终止整批，而是在 `data` 中以 `success=false` 体现，`resultCode` 仍为 `0`。

### `POST /api/v1/knowledgeItems/delete`

删除指定知识库下面的文档。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需删除的文档全路径，以 `/` 开头，不包括知识库名称 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/考勤制度.pdf"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

### `POST /api/v1/knowledgeItems/move`

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

请求示例（移动并重命名单个文件）：

```json
{
  "knCode": "1",
  "sourcePath": ["/制度/人事/考勤制度.pdf"],
  "targetFilePath": "/归档/人事/考勤制度.pdf",
  "overwrite": false
}
```

请求示例（批量移动文件和目录到目录 `/归档/人事`，目录不存在时自动创建）：

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
| `targetPath` | string | 该源路径实际移动后的目标路径 |
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
        "targetPath": "/归档/人事/不存在.pdf",
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

## 目录与文件读取

### `POST /api/v1/listDir`

获取指定知识库目录下的所有文件和文件夹。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `directoryPath` | string | 是 | 目录路径，以 `/` 开头，不包括知识库名称 |

请求示例：

```json
{
  "knCode": "1",
  "directoryPath": "/制度/人事"
}
```

成功响应示例：

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
        "size": 0
      },
      {
        "knCode": "1",
        "name": "/制度/人事/请假制度.pdf",
        "type": "file",
        "size": 245760
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "directory not found: /制度/人事",
  "resultObject": {}
}
```

### `POST /api/v1/glob`

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

请求示例：

```json
{
  "knCode": "1",
  "pathRule": "/制度/*/*.pdf"
}
```

成功响应示例：

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
        "size": 245760
      },
      {
        "knCode": "1",
        "name": "/制度/法务/合同规范.pdf",
        "type": "file",
        "size": 327680
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "pathRule must not be empty",
  "resultObject": {}
}
```

### `POST /api/v1/readFile`

根据文件路径读取指定知识库下的文件内容，并以 Markdown 文本形式返回。若文件内容包含内部 Markdown 引用 token，响应会解析为用户可见路径；未解析或目标已删除的引用回退为用户原始写法。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需读取的文件全路径，以 `/` 开头，不包括知识库名称 |
| `startLine` | integer | 否 | Markdown 起始行，默认不填表示全部读取 |
| `endLine` | integer | 否 | Markdown 结束行，默认不填表示全部读取 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf",
  "startLine": 1,
  "endLine": 20
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "knCode": "1",
    "filePath": "/制度/人事/请假制度.pdf",
    "startLine": 1,
    "endLine": 20,
    "data": "# 请假制度\n\n第一条 适用范围\n...",
    "reachedEof": false
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

### `POST /api/v1/downloadFile`

根据文件路径下载指定知识库下的文件。非 Markdown 文件返回入库字节；Markdown 文件入库时会被 token 化，系统不保留用户最初上传的原始 Markdown 字节，因此下载时返回已解析为用户可见路径的 Markdown 内容。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需下载的文件全路径，以 `/` 开头，不包括知识库名称 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf"
}
```

成功响应：

- `200 OK`
- `Content-Type: application/octet-stream`
- `Content-Disposition: attachment; filename="..."` 或带 `filename*`
- 响应体为文件字节流；Markdown 文件为解析后的 Markdown 字节流

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 知识构建

### `POST /api/v1/fileToMarkdown`

上传一个原始文件，同步执行原始文件转 Markdown 流程，并以 Markdown 文件流形式返回转换结果。

该接口只执行文件转 Markdown，不会创建知识库文件、不创建构建任务、不执行文档切片、不执行向量化，也不会写入知识库索引。

请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `fileContent` | file | 是 | 需转换的原始文件二进制内容 |

文件类型检查规则：

- 服务端根据上传文件名的扩展名识别文件类型。
- 文件名为空、缺少扩展名或扩展名不受支持时，返回失败响应。
- 当前支持的文件类型：`txt`、`md`、`markdown`、`csv`、`pdf`、`docx`、`doc`、`pptx`、`ppt`、`xlsx`、`xls`。

表单示例：

```bash
curl -X POST http://localhost:8000/api/v1/fileToMarkdown \
  -F "fileContent=@./考勤制度.pdf" \
  -o 考勤制度.md
```

成功响应：

- 响应体为 Markdown 文件流
- `Content-Type`：`application/octet-stream`
- `Content-Disposition`：`attachment; filename="<原文件名去扩展名>.md"`

成功响应示例：

```http
HTTP/1.1 200 OK
Content-Type: application/octet-stream
Content-Disposition: attachment; filename="考勤制度.md"

# 考勤制度

...
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "unsupported file type: exe. Supported types: csv, doc, docx, markdown, md, pdf, ppt, pptx, txt, xls, xlsx",
  "resultObject": {}
}
```

### `POST /api/v1/fileToMarkdownIndex`

异步触发指定知识库下文件的构建任务。接口会先检查当前文件是否已存在构建中的任务，再决定是否受理新的构建请求。

后台处理规则：

1. 如果对应文件已存在未完成的构建任务，则不再重复触发构建，直接返回失败响应，`resultCode` 为 `"-1"`，`resultMsg` 返回错误提示。
2. 如果对应文件上一次构建失败，则重新触发构建。
3. 如果对应文件不存在未完成的构建任务，则触发构建流程，自动完成原始文件转 Markdown、切片和切片向量化处理。

构建进度和当前处理环节需要通过 `POST /api/v1/fileBuildStatus` 查询。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 需构建的文档全路径，以 `/` 开头，不包括知识库名称 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {}
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "build task already exists for file: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

### `POST /api/v1/fileBuildStatus`

文档构建状态查询。复用 `FileController.fileListByUser` 对应的状态查询链路。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码，对应 `agt_resource.resource_id` |
| `filePath` | string | 是 | 文件全路径，最后一级为文件名 |

请求示例：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事/请假制度.pdf"
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "status": "processing",
    "currentStep": "vectorizing",
    "currentStepStatus": "running",
    "statusDict": [
      {
        "standDisplayValue": "处理中",
        "standCode": "processing",
        "standDisplayValueEn": "Processing"
      },
      {
        "standDisplayValue": "成功",
        "standCode": "success",
        "standDisplayValueEn": "Success"
      },
      {
        "standDisplayValue": "失败",
        "standCode": "failed",
        "standDisplayValueEn": "Failed"
      }
    ],
    "stepDict": [
      {
        "standDisplayValue": "原始文件转 Markdown",
        "standCode": "markdown",
        "standDisplayValueEn": "Markdown"
      },
      {
        "standDisplayValue": "文档切片",
        "standCode": "chunking",
        "standDisplayValueEn": "Chunking"
      },
      {
        "standDisplayValue": "切片向量化",
        "standCode": "vectorizing",
        "standDisplayValueEn": "Vectorizing"
      }
    ]
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 构建状态 |
| `currentStep` | string | 当前环节 |
| `currentStepStatus` | string | 当前环节状态 |
| `statusDict` | array[object] | 状态字典 |
| `stepDict` | array[object] | 环节字典 |

`statusDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `processing` | 处理中 | Processing |
| `success` | 成功 | Success |
| `failed` | 失败 | Failed |
| `unsupported` | 不支持构建 | Unsupported |

`unsupported` 表示该文件类型不在可构建类型范围内（见 `POST /api/v1/knowledgeItems/import` 的文件类型说明），构建在「原始文件转 Markdown」环节即结束，不会进入切片与向量化。

`stepDict` 当前支持取值：

| `standCode` | `standDisplayValue` | `standDisplayValueEn` |
| --- | --- | --- |
| `markdown` | 原始文件转 Markdown | Markdown |
| `chunking` | 文档切片 | Chunking |
| `vectorizing` | 切片向量化 | Vectorizing |
| `complete` | 已完成 | complete |

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "file not found: /制度/人事/请假制度.pdf",
  "resultObject": {}
}
```

## 知识检索

### `POST /api/v1/knowledgeItems/search`

根据用户提问召回对应的知识切片。

请求体：`application/json`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `query` | string | 是 | 需要检索的内容 |
| `knCodeList` | array[string] | 是 | 知识库编码列表 |
| `topK` | integer | 是 | 最终返回条数，必须大于 0 |
| `searchMode` | string | 是 | 检索模式：`fullTextRecall`、`embedding`、`mixedRecall` |
| `where` | object | 否 | Agent DSL 过滤 AST，详见 metadata_api.md |
| `metadataFieldList` | array[string] | 否 | 需要返回的元数据字段 |
| `fileTypeList` | array[string] | 否 | 按文件类型过滤；向下兼容字段，与 `where` 同时存在时合取 |

请求示例：

```json
{
  "query": "员工请假流程是什么",
  "knCodeList": ["1"],
  "topK": 5,
  "searchMode": "mixedRecall",
  "where": {"in": {"fieldName": "fileType", "value": ["pdf"]}}
}
```

成功响应示例：

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "data": [
      {
        "knCode": "1",
        "filePath": "/制度/人事/请假制度.pdf",
        "chunkNo": 3,
        "chunkId": 10023,
        "chunkText": "员工请假需在 OA 系统发起申请，经部门负责人审批后生效。",
        "score": 92,
        "imagePath": "/images/10023.png",
        "startLine": 18,
        "endLine": 26
      },
      {
        "knCode": "1",
        "filePath": "/制度/人事/考勤制度.pdf",
        "chunkNo": 5,
        "chunkId": 10087,
        "chunkText": "病假需提供医院证明材料。",
        "score": 81,
        "imagePath": "",
        "startLine": 42,
        "endLine": 46
      }
    ]
  }
}
```

失败响应示例：

```json
{
  "resultCode": "-1",
  "resultMsg": "topK must be greater than 0",
  "resultObject": {}
}
```

## 探活

### `GET /health`

服务探活接口，用于检测服务进程是否正常运行。

成功响应示例：

```json
{
  "status": "ok"
}
```
