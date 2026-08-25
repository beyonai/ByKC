# entityEnrich

## 功能描述

异步补全 KnowledgeEntity 文档的实体信息、证据和允许的语义关系。接口可处理单个实体文件或整个知识库的待处理实体文件，并立即返回批次受理结果。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/entityEnrich` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

异步召回授权证据，生成并原子更新一个 KnowledgeEntity 文档，或批量处理知识库 `/KnowledgeEntity` 目录下全部符合条件的实体文档，同时提取允许的语义关系。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 目标实体文档所属知识库 |
| `filePath` | string | 否 | - | KnowledgeEntity 文档路径；不传表示处理本库 `/KnowledgeEntity` 下全部符合条件的实体文档 |
| `topK` | integer | 否 | `20` | 语义证据候选上限，建议最大 100 |
| `force` | boolean | 否 | `false` | 是否跳过 freshness 判断 |
| `extraParams` | object | 否 | `null` | **已弃用**，仅为历史请求兼容而接收；服务端不修改其内容，也不使用、持久化或传入 Callback |

HTTP 请求中不包含 `callback` 或模板正文。历史客户端也可使用别名 `extra_params`；新接入不应再传入该字段。`extParams` 不是受支持的字段，传入时请求校验失败。Enrich 只在当前知识库内收集证据，更新策略随服务代码发布。

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/KnowledgeEntity/OSOT.md",
  "topK": 20,
  "force": false
}
```

兼容旧请求时可以携带 `extraParams`，但它不影响任务语义：

```json
{
  "knCode": "1",
  "filePath": "/KnowledgeEntity/OSOT.md",
  "extraParams": {"legacyRequestId": "req-1001"}
}
```

上述对象仅在请求模型中原样接收，不进入 batch/task 执行参数、状态响应或 Callback 事件。

全库触发时不传 `filePath`。

## 成功响应示例（已受理）

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ee-20260817-0001",
    "scope": "SINGLE_FILE",
    "taskType": "DOCUMENT_ENRICH",
    "eligibleCount": 1,
    "acceptedCount": 1,
    "reusedCount": 0,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "9101",
        "status": "PENDING",
        "fileId": "2048",
        "filePath": "/KnowledgeEntity/OSOT.md",
        "reused": false
      }
    ]
  }
}
```

没有可用证据的文件不进入可执行任务，计入 `skippedCount`；如果证据在任务执行过程中失效，则该文件任务进入终态 `SKIPPED`：

```json
{
  "status": "SKIPPED",
  "skipReason": "NO_EVIDENCE"
}
```

## 6.1 Enrich 执行约束

- 只接受 `documentKind=knowledgeEntity` 且启用 `entityEnrich` 的文档；
- 传入 `filePath` 时，目标必须属于当前知识库的 `/KnowledgeEntity` 目录；不传时只枚举该固定目录；
- `entityName`、`aliases` 等身份 metadata 必须完整；
- 至少存在一份调用方有权访问的证据，否则任务进入 `SKIPPED`；
- evidence 范围只能收窄调用方权限，不能扩大权限；
- 模板章节缺失、顺序变化或占位符残留只产生 warning；
- 身份漂移、空正文、无权限引用和并发 checksum 冲突阻断写入；
- 非法关系被丢弃并记录，不阻断合法正文写入；
- Enrich 自己生成的新 checksum 不再次触发同一 Enrich；
- Enrich 文件任务进入终态并提交后才调用文件完成 Callback。

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

- 一次请求创建一个 batch，每个合格 KnowledgeEntity 文件对应一个 task。
- 目标限定为当前库 `/KnowledgeEntity` 中 `documentKind=knowledgeEntity` 的文档。
- 证据范围不能超过调用方权限；无可用证据时跳过该文件。
- 并发 checksum 冲突、身份漂移和空正文会阻断写入。
- 文件失败、`TASK_TIMEOUT` 或 `WORKER_LOST` 都是终态，不自动重试。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
