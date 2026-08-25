# entityDiscovery

## 功能描述

异步发现知识文件中的实体，并为符合条件的文件生成或更新 KnowledgeEntity 文档。接口只负责创建批次和文件任务，实际处理由后台 Worker 执行。

## 接口信息

| 项目 | 值 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/api/v1/knowledgeItems/entityDiscovery` |

## 请求 Header

| Header | 必填 | 值 | 说明 |
| --- | --- | --- | --- |
| `Content-Type` | 是 | `application/json` | 请求体类型 |
| `Accept` | 否 | `application/json` | 期望的成功响应类型 |

> 服务本身未定义额外的业务认证 Header；如由网关统一认证，按部署环境要求携带。

异步扫描一个原始文档或知识库内全部符合条件的原始文档，锚定已有 KnowledgeEntity，发现并创建新的 KnowledgeEntity，建立 `MENTIONS` 关系。

请求字段：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `knCode` | string | 是 | - | 原始文档所属知识库 |
| `filePath` | string | 否 | - | 原始文档路径；不传表示处理该知识库下全部符合条件的原始文档 |
| `maxEntities` | integer | 否 | `12` | 每个源文档的最大抽取实体数，不得超过 12 |
| `force` | boolean | 否 | `false` | 是否跳过 freshness 判断；不跳过资格和权限校验 |
| `extraParams` | object | 否 | `null` | **已弃用**，仅为历史请求兼容而接收；服务端不修改其内容，也不使用、持久化或传入 Callback |

HTTP 请求中不包含 `callback` 字段。历史客户端也可使用别名 `extra_params`；新接入不应再传入该字段。`extParams` 不是受支持的字段，传入时请求校验失败。

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "maxEntities": 12,
  "force": false
}
```

兼容旧请求时可以携带 `extraParams`，但它不影响任务语义：

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "extraParams": {"legacyRequestId": "req-1001"}
}
```

上述对象仅在请求模型中原样接收，不进入 batch/task 执行参数、状态响应或 Callback 事件。

全库触发时不传 `filePath`：

```json
{
  "knCode": "1",
  "maxEntities": 12,
  "force": false
}
```

## 成功响应示例（已受理）

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0001",
    "scope": "SINGLE_FILE",
    "taskType": "ENTITY_DISCOVERY",
    "eligibleCount": 1,
    "acceptedCount": 1,
    "reusedCount": 0,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "9001",
        "status": "PENDING",
        "fileId": "1024",
        "filePath": "/原始文档/AI时代的组织革命.md",
        "reused": false
      }
    ]
  }
}
```

全库请求的响应结构相同，`scope=WHOLE_KB`，计数为本次资格筛选和幂等判断的汇总。`tasks` 只返回本次接受或复用的任务摘要；当数量超过服务端上限时可以截断，完整状态通过 `processingTaskStatus` 按 `batchId` 查询。

相同输入指纹已经成功或已有运行中任务时不创建重复任务，计入 `reusedCount`：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0002",
    "scope": "SINGLE_FILE",
    "taskType": "ENTITY_DISCOVERY",
    "eligibleCount": 1,
    "acceptedCount": 0,
    "reusedCount": 1,
    "skippedCount": 0,
    "tasks": [
      {
        "taskId": "801",
        "status": "SUCCEEDED",
        "fileId": "1024",
        "filePath": "/原始文档/AI时代的组织革命.md",
        "reused": true
      }
    ]
  }
}
```

Discovery 结果项语义：

| `action` | 含义 |
| --- | --- |
| `ANCHORED` | 当前知识库内通过精确名称/alias 或同义裁决复用实体资产 |
| `CREATED` | 创建新的最小有效 KnowledgeEntity 文档 |
| `DROPPED` | 候选不满足实体定义或身份无法可信确认 |

每个非丢弃 action 还返回 `canonicalEntityId`、`resolutionMethod`、`candidateCount` 和可空的 `aliasAdded`。`resolutionMethod` 可能为 `EXACT_CANONICAL`、`EXACT_ALIAS`、`SYNONYM_ADJUDICATED`、`CREATED_NEW` 或 `AMBIGUOUS_UNMERGED`。

Discovery 成功任务结果示例见任务状态接口。

## 5.1 Discovery 执行约束

- 只接受 `documentKind=original` 且启用 `entityDiscovery` 的文档；
- 文档必须已经生成可读 Markdown 正文；
- `filePath` 未传时，枚举当前知识库中所有符合上述条件的文件，并排除 `/KnowledgeEntity` 目录；
- 新实体只写入源文档所在知识库的固定 `/KnowledgeEntity` 目录，目录不存在时自动创建；接口不允许调用方指定其他知识库或目录；
- 新实体路径固定为 `/KnowledgeEntity/{规范可读名称}.md`，不附加 MD5、哈希签名或数字序号；
- 同库规范路径已存在时直接锚定该文件，不创建副本：文件必须是 KnowledgeEntity，`entityName` 与候选相同或缺失，subject 身份一致；明显的元数据或文档类型冲突使任务失败；
- LLM 先从正文抽取最多 12 个显著候选；worker 不加载全库实体、不把全量词表放入 Prompt，也不按任务构建 AC；
- 抽取后先在当前知识库做规范名/alias 精确匹配；唯一兼容命中不调用额外 LLM；
- 精确未命中时按当前知识库、Subject 和类型过滤，并使用 `full`、`local_name` 双视角 embedding 召回 Top 3；向量候选必须经过 `SAME/DIFFERENT/UNCERTAIN` 裁决，只有 `SAME` 才回写 alias；
- 名称、alias 和 embedding 候选查询都严格限定在当前知识库；本库没有兼容结果时直接在本库创建实体，不读取其他知识库的实体资产；
- 同一词面命中多个规范实体时记录 `AMBIGUOUS_SURFACE` 并保守新建，不按查询顺序选择；
- 新实体正文中的来源路径以普通文本展示，不生成指向原始文档的 Markdown 链接；只持久化原始文档到实体的单向 `MENTIONS`，反向视图由查询层派生；
- 实体名称、alias、Subject、类型和稳定实体 ID 以 `knowledge_entity` 为事实源；KnowledgeEntity Markdown 是可空的一一文件锚点；
- `maxEntities` 是每个源文件的结果上限，不是整个批次共享上限；不得通过截断隐藏已发生的写入；
- `force=true` 会跳过已成功任务的 freshness 复用并创建新任务；如同文件同类型仍有 `PENDING/RUNNING` 任务，则复用该活动任务，身份和关系写入仍保持幂等；
- Discovery 文件任务进入终态并提交后才调用文件完成 Callback。

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

- 一次请求创建一个 batch，每个合格文件对应一个独立 task。
- 仅处理 `documentKind=original` 且已生成可读 Markdown 的文档。
- 新实体只能写入当前知识库固定 `/KnowledgeEntity` 目录，不建立跨库关系。
- `force=true` 跳过已成功结果的 freshness 复用，但同文件仍有 `PENDING/RUNNING` 任务时复用活动任务。
- 文件失败、`TASK_TIMEOUT` 或 `WORKER_LOST` 都是终态，不自动重试。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。

---

[返回 API 导航](../README.md)
