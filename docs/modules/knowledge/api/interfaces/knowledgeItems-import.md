# knowledgeItems-import

## 功能描述

向指定知识库目录导入文件或压缩包，并建立知识文件记录。接口负责文件落库、路径冲突校验和导入结果汇总，后续是否构建索引由相应流程决定。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/import` |
| 兼容路径 | `/api/v1/knowledge-items/import` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `multipart/form-data; boundary=...` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

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
| `skipIfDuplicate` | boolean | 否 | 是否检查同一知识库内的文件 checksum；默认 `false`。为 `true` 且已存在相同 checksum 时跳过导入，并在错误信息中返回已存在文件路径 |

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

zip 批量上传可能包含可选字段 `postProcessErrors`（string 数组），用于返回文件入库完成后的批后处理错误（例如 Markdown 引用批量补偿失败）。该字段不属于 `data` 文件结果列表，且不计入 `summary.total` / `summary.succeeded` / `summary.failed`；`data` 和 `summary` 只统计真实 zip entry / 知识库路径。

补偿失败时响应示例片段：

```json
{
  "resultObject": {
    "data": [
      { "filePath": "/制度/人事/考勤制度.pdf", "success": true, "error": null }
    ],
    "summary": { "total": 1, "succeeded": 1, "failed": 0 },
    "postProcessErrors": ["batch reference compensation failed: ..."]
  }
}
```

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

## 失败响应示例（整请求级别失败，`resultCode` 为 `-1`）

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

## 请求示例

```bash
curl -X POST \
  -H "Accept: application/json" \
  -F "knCode=1" \
  -F "filePath=/制度/考勤.md" \
  -F "fileContent=@attendance.md;type=text/markdown" \
  -F "processFrontMatter=true" \
  -F "skipIfDuplicate=false" \
  http://localhost:8000/api/v1/knowledgeItems/import
```

## 特殊逻辑

- 文件名以 `.zip` 结尾且内容为合法 ZIP 时，`filePath` 解释为目标目录并执行批量导入。
- ZIP 内的路径越界、重复路径和单文件失败按文件记录，不伪造整批成功。
- Markdown front matter 只在 `processFrontMatter=true` 时写入元数据。
- `skipIfDuplicate` 使用同库文件 checksum 判重。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
