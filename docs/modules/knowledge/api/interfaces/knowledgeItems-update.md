# knowledgeItems-update

## 功能描述

更新知识库中已有文件的正文、属性或引用签名。接口通过并发校验防止覆盖较新的修改，并在成功后维护相关文件数据。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/update` |
| 兼容路径 | `/api/v1/knowledge-items/update` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `multipart/form-data; boundary=...` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

更新一个已经存在的文档内容。该接口只更新一个文件：不支持 zip、批量更新、移动、重命名或改变文件格式；更新成功后不会自动触发知识构建，调用方如需恢复 Markdown、分块和检索结果，须另行调用 `POST /api/v1/fileToMarkdownIndex`。

请求体：`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 已存在的目标文件完整路径，以 `/` 开头，不包括知识库名称 |
| `fileContent` | file | 是 | 更新后的文件内容；上传文件名扩展名必须与 `filePath` 的扩展名一致，且不能为 `.zip` |
| `fileDescription` | string | 否 | 文件描述；字段未传入时保留原描述，传入空字符串时清空原描述 |
| `processFrontMatter` | boolean | 否 | 是否解析 Markdown YAML front matter 并写入元数据，默认 `true`；非 Markdown 文件忽略该字段 |
| `skipIfDuplicate` | boolean | 否 | 是否检查同一知识库内除目标文件外的相同 checksum；默认 `false`，命中时拒绝更新 |
| `referSignature` | string | 否 | 乐观并发校验使用的目标文件 checksum；传入值与当前 `fileSignature` 不一致时拒绝更新 |
| `metadata` | string(JSON object) | 否 | 显式文件元数据；顶层必须是 JSON object，值使用与 YAML front matter 相同的类型推断规则 |

行为描述：

- 目标文件必须已经存在；接口不会把更新请求变成新文件导入。
- 对 Markdown，复用导入接口的稳定引用重写与 YAML front matter 解析。front matter 中出现的字段按现有 upsert 规则更新；数据表中已有、但新文件未出现的字段保留。
- 请求 `metadata` 与新文件的 YAML front matter 合并后再 upsert；同名冲突以 front matter 为准，未在两者中出现的既有字段继续保留。
- `processFrontMatter=false` 只关闭 front matter 解析，不影响显式 `metadata`；非 Markdown 文件同样支持显式 metadata。
- 成功更新会清理旧 Markdown sidecar、chunk、向量、检索投影、构建记录与抓取缓存；不会自动创建新的构建任务。
- 更新会同步写入一条文件更新时间线。Markdown 初始写入规则摘要，后台任务可在大模型摘要成功后原地更新该摘要；模型失败时保留规则摘要。非 Markdown 文件写入固定格式摘要且不调用大模型。
- 如果该文件存在运行中的构建任务，更新失败，避免旧构建结果覆盖新内容。
- 更新在目标文件行锁内执行；数据库步骤使用单事务，存储写入失败不提交数据库，数据库提交失败会恢复旧文件内容，保证文件级更新的原子语义。

表单示例：

```bash
curl -X POST http://localhost:8000/api/v1/knowledgeItems/update \
  -F "knCode=1" \
  -F "filePath=/制度/人事/请假制度.md" \
  -F 'metadata={"owner":"HR","status":"review"}' \
  -F "fileContent=@./请假制度.md" \
  -F "processFrontMatter=true"
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
        "filePath": "/制度/人事/请假制度.md",
        "success": true,
        "error": null
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 文件更新结果 |
| `resultObject.data` | array[object] | 是 | 逐文件结果；本接口成功时固定为单元素数组 |
| `resultObject.data[].knCode` | string | 是 | 知识库编码 |
| `resultObject.data[].filePath` | string | 是 | 被更新文件的完整路径 |
| `resultObject.data[].success` | boolean | 是 | 是否更新成功；成功响应中恒为 `true` |
| `resultObject.data[].error` | string \| null | 是 | 成功时为 `null` |

失败响应统一使用 HTTP 200 与 `resultCode: "-1"`：

```json
{
  "resultCode": "-1",
  "resultMsg": "build task already exists for file: /制度/人事/请假制度.md",
  "resultObject": {}
}
```

常见失败原因包括目标文件或知识库不存在、上传文件扩展名不匹配、上传 zip、路径非法，以及该文件存在运行中的构建任务。

`metadata` 和 YAML front matter 均不允许写入 `fileName`、`filePath`、`fileSize`、`fileType`、`mimeType`、`fileSignature`、`createdAt`、`updatedAt` 等只读系统字段；校验失败时文件内容和既有元数据均保持原值。

## 请求示例

```bash
curl -X POST \
  -H "Accept: application/json" \
  -F "knCode=1" \
  -F "filePath=/制度/考勤.md" \
  -F "fileContent=@attendance-v2.md;type=text/markdown" \
  -F "referSignature=sha256-before-update" \
  http://localhost:8000/api/v1/knowledgeItems/update
```

## 特殊逻辑

- 上传文件扩展名必须与 `filePath` 一致，且不允许 ZIP。
- `referSignature` 是乐观并发条件；与当前 checksum 不同时拒绝覆盖。
- 更新成功后会使旧构建结果失效，需要时重新触发知识构建。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
