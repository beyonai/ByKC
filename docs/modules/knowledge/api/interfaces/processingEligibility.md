# processingEligibility

## 功能描述

检查指定文件当前是否满足实体发现或实体补全的执行条件。接口只返回资格判断、跳过原因和相关状态，不创建后台任务。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/processingEligibility` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

判断文档对指定能力是否满足资格，以及最近成功任务之后输入是否发生变化。

请求字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `knCode` | string | 是 | 知识库编码 |
| `filePath` | string | 是 | 文档路径 |
| `capability` | string | 是 | `entityDiscovery` 或 `entityEnrich` |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "capability": "entityDiscovery"
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "fileId": "1024",
    "knCode": "1",
    "filePath": "/原始文档/AI时代的组织革命.md",
    "documentKind": "original",
    "capability": "entityDiscovery",
    "eligibility": "ELIGIBLE_AND_STALE",
    "reasonCode": "INPUT_CHANGED",
    "lastSuccessfulTaskId": "801",
    "lastSuccessfulAt": "2026-08-17T10:00:00+08:00"
  }
}
```

`reasonCode` 建议值：

```text
NEVER_PROCESSED
INPUT_CHANGED
METHOD_VERSION_CHANGED
INPUT_UNCHANGED
NEW_RELATION
NO_NEW_RELATIONS
CAPABILITY_DISABLED
DOCUMENT_KIND_MISMATCH
UNSUPPORTED_FILE_FORMAT
KNOWLEDGE_ENTITY_PATH_REQUIRED
UNSUPPORTED_CONTENT_TYPE
CONTENT_NOT_READY
IDENTITY_METADATA_INCOMPLETE
NO_EVIDENCE
PERMISSION_DENIED
```

判定原则：

- `original` 默认只允许 `entityDiscovery`；
- `knowledgeEntity` 默认只允许 `entityEnrich`；
- `processingCapabilities` 可以覆盖默认能力；
- 指纹相同返回 `ELIGIBLE_BUT_FRESH`，而不是接口错误；
- 不满足文档类型、权限、内容或证据条件返回 `INELIGIBLE`。

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

- 该接口只评估资格和 freshness，不创建 batch/task，也不执行模型调用。
- Discovery 和 Enrich 使用不同的文档类型、元数据和证据条件。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
