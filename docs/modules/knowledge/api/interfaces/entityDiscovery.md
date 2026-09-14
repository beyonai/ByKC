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
| `filePath` | string | 否 | - | 原始文档路径；传入时优先于 `directoryPath` |
| `directoryPath` | string | 否 | - | 原始文档目录，递归处理子目录；未传 `filePath` 时生效 |
| `targetDirectoryPath` | string | 否 | - | KnowledgeEntity 输出目录；不传时新建实体默认写入 `/KnowledgeEntity`，已有实体保持原路径 |
| `maxEntities` | integer | 否 | `12` | 每个源文档的最大抽取实体数，不得超过 12 |
| `force` | boolean | 否 | `false` | 是否跳过 freshness 判断；不跳过资格和权限校验 |
| `tags` | array[string] | 否 | `null` | 追加到本次 Discovery 实际创建或锚定到的 KnowledgeEntity metadata |
| `extraParams` | object | 否 | `null` | **已弃用**，仅为历史请求兼容而接收；服务端不修改其内容，也不使用、持久化或传入 Callback |

HTTP 请求中不包含 `callback` 字段。历史客户端也可使用别名 `extra_params`；新接入不应再传入该字段。`extParams` 不是受支持的字段，传入时请求校验失败。

## 请求示例

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "targetDirectoryPath": "/领域知识/组织",
  "maxEntities": 12,
  "force": false,
  "tags": ["organization", "ai"]
}
```

`tags` 只写入当前任务实际创建或锚定到的 KnowledgeEntity，不写入被扫描的原始文档。已有标签顺序保持不变，请求中的新标签按传入顺序追加，重复字符串不会重复写入；空数组等价于不传。若源文件指纹未变，但输出目录或标签尚未满足，服务会复用上次成功的 Discovery 结果，只移动实体文件和/或追加标签，不再调用大模型。若同一源文档已有运行中的任务，只有其输出目录相同且 tags 已覆盖本次请求时才复用；否则请求失败，调用方应在该任务进入终态后重试。

兼容旧请求时可以携带 `extraParams`，但它不影响任务语义：

```json
{
  "knCode": "1",
  "filePath": "/原始文档/AI时代的组织革命.md",
  "extraParams": {"legacyRequestId": "req-1001"}
}
```

上述对象仅在请求模型中原样接收，不进入 batch/task 执行参数、状态响应或 Callback 事件。

全库触发时不传 `filePath` 和 `directoryPath`：

```json
{
  "knCode": "1",
  "maxEntities": 12,
  "force": false
}
```

按目录触发：

```json
{
  "knCode": "1",
  "directoryPath": "/原始文档/人力资源",
  "maxEntities": 12,
  "force": false
}
```

定位优先级为 `filePath > directoryPath > knCode`。同时传入文件和目录时只处理该文件。

## 成功响应示例（已受理）

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0001",
    "scope": "SINGLE_FILE",
    "targetPath": "/原始文档/AI时代的组织革命.md",
    "taskType": "ENTITY_DISCOVERY",
    "candidateCount": 1,
    "eligibleCount": 1,
    "acceptedCount": 1,
    "reusedCount": 0,
    "skippedCount": 0,
    "returnedTaskCount": 1,
    "tasksTruncated": false,
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

## 响应参数

| 字段路径 | 类型 | 必返 | 说明 |
| --- | --- | --- | --- |
| `resultCode` | string | 是 | 业务结果码；请求受理时为 `0` |
| `resultMsg` | string | 是 | 业务结果说明；受理时为 `accepted` |
| `resultObject` | object | 是 | 批次受理结果 |
| `resultObject.batchId` | string | 是 | 本次触发形成的批次 ID |
| `resultObject.scope` | string | 是 | 处理范围：`SINGLE_FILE`、`DIRECTORY` 或 `WHOLE_KB` |
| `resultObject.targetPath` | string | 否 | 单文件或目录目标路径；全库触发时省略 |
| `resultObject.taskType` | string | 是 | 固定为 `ENTITY_DISCOVERY` |
| `resultObject.candidateCount` | integer | 是 | 本次扫描到的候选文件数 |
| `resultObject.eligibleCount` | integer | 是 | 通过基础资格筛选的文件数 |
| `resultObject.acceptedCount` | integer | 是 | 本次新建任务数 |
| `resultObject.reusedCount` | integer | 是 | 复用已有任务数 |
| `resultObject.skippedCount` | integer | 是 | 受理前跳过的文件数 |
| `resultObject.returnedTaskCount` | integer | 是 | `tasks` 实际返回的任务数 |
| `resultObject.tasksTruncated` | boolean | 是 | 任务预览是否因 20 条上限被截断 |
| `resultObject.tasks` | array[object] | 是 | 新建或复用的任务预览 |
| `resultObject.tasks[].taskId` | string | 是 | 文件任务 ID |
| `resultObject.tasks[].status` | string | 是 | 任务当前状态 |
| `resultObject.tasks[].fileId` | string | 是 | 待处理文件 ID |
| `resultObject.tasks[].filePath` | string | 是 | 待处理文件路径 |
| `resultObject.tasks[].reused` | boolean | 是 | 是否复用了已有任务 |

目录请求返回 `scope=DIRECTORY`，全库请求返回 `scope=WHOLE_KB`。`candidateCount` 是本次扫描数，且始终满足 `candidateCount = acceptedCount + reusedCount + skippedCount`。`tasks` 只预览本次接受或复用的任务，最多返回 20 条；`tasksTruncated=true` 时表示已截断。本次新建任务的完整状态通过 `processingTaskStatus` 按 `batchId` 分页查询。

相同输入指纹已经成功且当前输出位置和标签已满足请求，或已有兼容的运行中任务时，不创建重复任务，计入 `reusedCount`：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "ed-20260817-0002",
    "scope": "SINGLE_FILE",
    "targetPath": "/原始文档/AI时代的组织革命.md",
    "taskType": "ENTITY_DISCOVERY",
    "candidateCount": 1,
    "eligibleCount": 1,
    "acceptedCount": 0,
    "reusedCount": 1,
    "skippedCount": 0,
    "returnedTaskCount": 1,
    "tasksTruncated": false,
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
- `filePath` 未传时，候选查询直接排除显式 `documentKind=knowledgeEntity` 的文件和 `/KnowledgeEntity` 整个子树；未配置 `documentKind` 的历史普通文件仍按 `original` 进入后续资格校验；
- 不传 `targetDirectoryPath` 时，新实体写入 `/KnowledgeEntity`，已有实体保持当前路径；传入时，新实体写入指定目录，已有实体移动到指定目录；目录不存在时由移动/导入服务按现有规则创建；
- 新实体路径为 `{targetDirectoryPath 或 /KnowledgeEntity}/{规范可读名称}.md`，不附加 MD5、哈希签名或数字序号；
- 同库规范路径已存在时直接锚定该文件，不创建副本：文件必须是 KnowledgeEntity，`entityName` 与候选相同或缺失，subject 身份一致；明显的元数据或文档类型冲突使任务失败；
- LLM 先从正文抽取最多 12 个显著候选；worker 不加载全库实体、不把全量词表放入 Prompt，也不按任务构建 AC；
- 抽取后先在当前知识库做规范名/alias 精确匹配；唯一兼容命中不调用额外 LLM；
- 精确未命中时按当前知识库的 `full` embedding 召回 Top 3；向量候选必须经过 `SAME/DIFFERENT/UNCERTAIN` 裁决，只有 `SAME` 才回写 alias；
- 名称、alias 和 embedding 候选查询都严格限定在当前知识库；本库没有兼容结果时直接在本库创建实体，不读取其他知识库的实体资产；
- 同一词面命中多个规范实体时记录 `AMBIGUOUS_SURFACE` 并保守新建，不按查询顺序选择；
- 新实体正文中的来源路径以普通文本展示，不生成指向原始文档的 Markdown 链接；只持久化原始文档到实体的单向 `MENTIONS`，反向视图由查询层派生；
- 实体名称、alias、Subject、类型和稳定实体 ID 以 `knowledge_entity` 为事实源；KnowledgeEntity Markdown 是可空的一一文件锚点；
- `maxEntities` 是每个源文件的结果上限，不是整个批次共享上限；不得通过截断隐藏已发生的写入；
- `force=true` 会跳过已成功任务的 freshness 复用并创建新任务；如同文件同类型仍有 `PENDING/RUNNING` 任务，则复用该活动任务，身份和关系写入仍保持幂等；
- 源文件指纹未变但需要移动或补标签时，新任务以 `REPLAY_RESULT` 模式执行；它校验历史 action 中的稳定文件 ID 和实体锚点，不读取源文件、不调用 LLM；历史结果不可回放时任务以 `DISCOVERY_RESULT_NOT_REPLAYABLE` 失败；
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
- 受理前已确定不合格的文件只计入 `skippedCount`，不写入 task 表；复用时直接返回原 task ID，不创建 `SKIPPED` task。
- 仅处理 `documentKind=original` 且已生成可读 Markdown 的文档。
- 目录/全库召回阶段不返回 `documentKind=knowledgeEntity` 或 `/KnowledgeEntity` 子树内的文件，这些文件不计入 `candidateCount` 和 `skippedCount`。
- 新实体只能写入当前知识库；`/KnowledgeEntity` 是未指定输出目录时的默认位置，不是实体身份边界。
- `force=true` 跳过已成功结果的 freshness 复用，但同文件仍有 `PENDING/RUNNING` 任务时复用活动任务。
- 文件失败、`TASK_TIMEOUT` 或 `WORKER_LOST` 都是终态，不自动重试。

## 路径与定位规则

- `knCode` 是知识库编码，HTTP 请求中使用字符串。
- `filePath` 必须以 `/` 开头，表示知识库内完整文件路径，不允许使用 `..` 越界。
- `directoryPath` 必须以 `/` 开头，允许根目录 `/`，不允许使用 `..`；目录必须存在。
- `targetDirectoryPath` 必须以 `/` 开头，允许根目录 `/`，不允许使用 `..`；它只影响输出实体位置，不改变 Discovery 输入范围。
- `/KnowledgeEntity` 及其子目录不能作为 Discovery 的 `directoryPath`。

---

[返回 API 导航](../README.md)
