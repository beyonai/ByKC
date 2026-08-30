# semanticRelations

## 功能描述

查询 KnowledgeEntity 之间已经抽取并持久化的语义关系。接口按知识库、实体或关系条件筛选逻辑边，并返回去重后的关系及断言统计。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/semanticRelations` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

查询指定文档的 `MENTIONS`、`PART_OF`、`IS_A` 和 `DEPENDS_ON` 逻辑关系。Markdown 引用作为 `MARKDOWN_PARSER` 生产的 `MENTIONS` 参与逻辑去重；现有 `knowledgeItems/references` 仍作为“物理 Markdown 引用出现”的兼容视图。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 文档所属知识库 |
| `filePath` | string | 是 | - | 文档路径 |
| `direction` | string | 否 | `BOTH` | `OUTGOING`、`INCOMING`、`BOTH` |
| `relationCodeList` | string[] | 否 | 全部 v1 关系 | 关系类型过滤 |
| `pageNum` | integer | 否 | `1` | 页码 |
| `pageSize` | integer | 否 | `50` | 每页数量，建议最大 500 |

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/KnowledgeEntity/OSOT.md",
  "direction": "BOTH",
  "relationCodeList": ["MENTIONS", "PART_OF"],
  "pageNum": 1,
  "pageSize": 50
}
```

## 成功响应示例

```json
{
  "resultCode": "0",
  "resultMsg": "success",
  "resultObject": {
    "fileId": "2048",
    "total": 2,
    "pageNum": 1,
    "pageSize": 50,
    "data": [
      {
        "relationId": "lr_e83691f6c1ef4d5288e652a0",
        "relationCode": "MENTIONS",
        "direction": "INCOMING",
        "source": {
          "fileId": "1024",
          "knCode": "1",
          "filePath": "/原始文档/AI时代的组织革命.md",
          "documentKind": "original"
        },
        "target": {
          "fileId": "2048",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT.md",
          "documentKind": "knowledgeEntity"
        },
        "assertionCount": 2,
        "confidence": 1.0,
        "discoveredBy": "MARKDOWN_PARSER",
        "representativeEvidence": {
          "producerRunId": "markdown-update:9001",
          "evidenceFingerprint": "f4a3...",
          "sourceHeadingPath": "组织模式 / OSOT",
          "startLine": 30,
          "endLine": 30,
          "startOffset": 816,
          "endOffset": 842
        }
      },
      {
        "relationId": "lr_b4750ce075076a141a0a5470",
        "relationCode": "PART_OF",
        "direction": "INCOMING",
        "source": {
          "fileId": "2051",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT-OCG.md",
          "documentKind": "knowledgeEntity"
        },
        "target": {
          "fileId": "2048",
          "knCode": "1",
          "filePath": "/KnowledgeEntity/OSOT.md",
          "documentKind": "knowledgeEntity"
        },
        "assertionCount": 1,
        "confidence": 0.96,
        "discoveredBy": "ENTITY_ENRICH",
        "sourceTaskId": "9101",
        "representativeEvidence": {
          "producerRunId": "entity-enrich:9101",
          "evidenceFingerprint": "9ba1..."
        }
      }
    ]
  }
}
```

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；`0` 表示查询成功 |
| `resultMsg` | string | 是 | 业务结果说明 |
| `resultObject` | object | 是 | 语义关系分页结果 |
| `resultObject.fileId` | string | 是 | 查询目标文件 ID |
| `resultObject.total` | integer | 是 | 权限过滤后的逻辑关系总数 |
| `resultObject.pageNum` | integer | 是 | 当前页码 |
| `resultObject.pageSize` | integer | 是 | 每页关系数 |
| `resultObject.data` | array[object] | 是 | 当前页逻辑关系；无结果时为空数组 |
| `resultObject.data[].relationId` | string | 是 | 由 source、关系类型和 target 派生的稳定逻辑 ID |
| `resultObject.data[].relationCode` | string | 是 | 关系类型代码 |
| `resultObject.data[].direction` | string | 是 | 相对查询文件的方向：`OUTGOING` 或 `INCOMING` |
| `resultObject.data[].source` | object | 是 | 关系起点文档 |
| `resultObject.data[].source.fileId` | string | 是 | 起点文件 ID |
| `resultObject.data[].source.knCode` | string | 是 | 起点知识库编码 |
| `resultObject.data[].source.filePath` | string | 是 | 起点文件路径 |
| `resultObject.data[].source.documentKind` | string | 是 | 起点文档类型 |
| `resultObject.data[].target` | object | 是 | 关系终点文档 |
| `resultObject.data[].target.fileId` | string | 是 | 终点文件 ID |
| `resultObject.data[].target.knCode` | string | 是 | 终点知识库编码 |
| `resultObject.data[].target.filePath` | string | 是 | 终点文件路径 |
| `resultObject.data[].target.documentKind` | string | 是 | 终点文档类型 |
| `resultObject.data[].assertionCount` | integer | 是 | 聚合到该逻辑关系的物理断言数 |
| `resultObject.data[].confidence` | number | 是 | 代表断言置信度，范围 0～1 |
| `resultObject.data[].discoveredBy` | string | 是 | 关系发现来源 |
| `resultObject.data[].sourceTaskId` | string | 否 | 产生关系的文件任务 ID |
| `resultObject.data[].representativeEvidence` | object \| null | 是 | 一条代表性轻量证据；无证据位置时为 `null` |
| `resultObject.data[].representativeEvidence.producerRunId` | string | 否 | 证据生产运行 ID |
| `resultObject.data[].representativeEvidence.evidenceFingerprint` | string | 否 | 证据指纹 |
| `resultObject.data[].representativeEvidence.sourceHeadingPath` | string | 否 | 来源章节路径 |
| `resultObject.data[].representativeEvidence.startLine` | integer | 否 | 来源起始行 |
| `resultObject.data[].representativeEvidence.endLine` | integer | 否 | 来源结束行 |
| `resultObject.data[].representativeEvidence.startOffset` | integer | 否 | 来源起始字符偏移 |
| `resultObject.data[].representativeEvidence.endOffset` | integer | 否 | 来源结束字符偏移 |

关系查询执行目标文档和相邻文档的权限过滤。调用方无权访问的边不返回，也不以数量暴露。`relationId` 是由 source/relation/target 派生的稳定逻辑 ID，不随某条物理断言重建而变化；`assertionCount` 是当前聚合的物理断言数。如果存在 Markdown 位置断言，`representativeEvidence` 优先返回其章节、行和偏移。

v1 不提供独立证据正文查询，也不实现 `knowledge_document_relation_evidence`。当前返回的是聚合数量和一条代表性轻量位置；若后续需要展开所有证据出现、证据 checksum 失效检测或长期关系审计，再增加独立证据层。

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

- 返回的是去重后逻辑边，`assertionCount` 表示合并的物理断言数。
- 查询结果执行文档权限过滤，不应暴露无权访问的关系端点。
- Markdown 引用以 `MARKDOWN_PARSER` 生产的 `MENTIONS` 参与逻辑去重。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
