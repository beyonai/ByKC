# 文件与目录后台构建设计

> 设计状态：本文记录已经确认的目标方案，当前阶段只交付文档，业务代码尚未实现。

## 1. 目标

本文定义 `POST /api/v1/fileToMarkdownIndex` 同时支持文件和目录后的批次、
文件任务、独立 Runner、一致性、复用、状态查询和 Callback 语义。

目标：

- 保持 `filePath` 入参兼容，由服务端判断目标是文件还是目录；
- 目录递归生成以文件为粒度的持久任务；
- Build 与 Entity 使用独立任务池，单 Build Worker 进程默认并发 16；
- 用户在构建期间仍可修改、删除、移动和重命名文件或目录；
- 只以稳定文件 ID、内容 checksum 和构建协议判断任务身份与复用；
- 每个新建文件任务和批次都能查询状态并在终态后发布 Callback；
- Discovery/Enrich 继续同步构建所需 Entity 文件，不进入外部 Build 队列。

非目标：

- 不新增主动取消接口；
- 不自动重试失败、超时或丢失 Worker 的任务；
- 不因 Build Profile 变化自动扫描或触发重建；
- 不限制单批次文件数量；
- 不清理历史 Build batch/task；
- 不增加 Build 与 Entity 共用的 Embedding 并发限制；
- 不保证 Callback 必达；
- 不使用版本化 Markdown object key。

## 2. 领域模型

### 2.1 File Build

`FILE_BUILD` 是完整构建流程：

```text
原文件 -> Markdown -> 分块 -> Embedding -> 检索投影
```

它唯一依赖的模型是 Embedding 模型，但还会使用解析 CPU、对象存储和
数据库。文件名和路径不是文件身份。

### 2.2 Build Batch 与 File Build Task

- 一次 API 受理形成一个 Build Batch，单文件请求也创建 batch；
- 一个本次需要执行的文件形成一个 File Build Task；
- 一个 task 只属于一个 batch；
- 命中已有任务是 `Reuse Hit`，只进入受理统计和预览，不在当前 batch
  创建 task；
- Entity 内部同步构建会创建 `INLINE` task，但不属于 Build Batch。

### 2.3 Build Profile

`build_profile` 是可读 JSONB，至少保存：

```json
{
  "profileVersion": 1,
  "parser": {"name": "default", "version": "2"},
  "chunking": {"size": 800, "overlap": 100},
  "embedding": {"model": "bge-m3", "dimension": 1024},
  "retrieval": {"schemaVersion": 1}
}
```

`build_profile_hash` 是规范化 `build_profile` 的 SHA-256，只用于索引、
去重和复用。规范化规则为：强类型模型补齐默认值、对象 key 递归排序、
固定 UTF-8 和分隔符、稳定数字表示，并拒绝 NaN 和无穷值。不得对请求或
配置中的原始 JSON 文本直接计算哈希。

Build Profile 变化不会自动触发重建。下次显式请求时，新 profile 无法
复用旧任务，因而创建新任务。

## 3. API 受理语义

### 3.1 请求

请求保持 `knCode + filePath`，并新增可选 `force=false`：

```json
{
  "knCode": "1",
  "filePath": "/制度/人事",
  "force": false
}
```

定位规则：

- `filePath` 必须定位一个当前存在的文件或目录；不存在时请求失败，不创建
  batch；
- 文件目标产生 `scope=SINGLE_FILE`；
- 目录目标产生 `scope=DIRECTORY`，递归包含全部子目录；
- `/` 表示知识库根目录；
- 服务端在受理事务内固定活跃文件 ID 清单；事务之后新建或移入的文件不
  加入批次，已纳入后移出或改名的文件仍按 ID 处理；
- 不设单批次文件数量上限。

### 3.2 集合化受理

目录不得先加载成完整 Python 列表再逐行写入。受理事务使用递归查询和
集合化 `INSERT ... SELECT` 完成快照、复用判断、旧任务取代、新任务创建
和计数；Python 端只读取聚合计数及最多 20 条预览。

接口必须在全部任务完成入库后才返回。超大目录可以导致较长的 HTTP
响应时间，这是不设置批次上限且要求响应携带精确计数的直接结果。

### 3.3 资格与计数

- 候选是快照内的活跃 FILE 节点；
- 缺少 checksum 或原始对象位置时为受理前跳过，原因
  `SOURCE_NOT_READY`，不创建 task；
- 不支持的文件类型仍创建 task，由 Worker 进入 `UNSUPPORTED`，从而有
  状态和文件 Callback；
- 空目录创建立即完成的空 batch；
- 单文件内容未就绪也创建立即完成的 batch，计数为 1 个候选、0 个新建、
  1 个跳过。

计数不变量：

```text
candidateCount = acceptedCount + reusedCount + skippedCount
eligibleCount = acceptedCount + reusedCount
batch.totalCount = acceptedCount
```

`tasks` 预览包含本次新建或命中的已有任务，最多 20 条。`tasksTruncated`
只表示响应预览截断，不表示任务被截断。

### 3.4 受理响应

单文件和目录使用同一响应结构：

```json
{
  "resultCode": "0",
  "resultMsg": "accepted",
  "resultObject": {
    "batchId": "fb-20260903-0001",
    "scope": "DIRECTORY",
    "targetPath": "/制度/人事",
    "taskType": "FILE_BUILD",
    "candidateCount": 80,
    "eligibleCount": 78,
    "acceptedCount": 60,
    "reusedCount": 18,
    "skippedCount": 2,
    "returnedTaskCount": 20,
    "tasksTruncated": true,
    "tasks": []
  }
}
```

## 4. 任务复用与取代

### 4.1 复用键

等价输入由以下字段共同确定：

```text
file_id + input_checksum + input_is_deleted + build_profile_hash
```

路径和文件名不参与比较。`input_is_deleted` 在正常受理时为 false，并作为
最终提交保护条件。

### 4.2 可复用状态

相同输入与配置下：

- `PENDING/RUNNING`：命中活动任务；
- `SUCCEEDED`：仅在 Built Content 完整性检查仍通过时命中已有成功产物；
- `UNSUPPORTED`：命中当前协议下的不支持结论；
- `FAILED/SKIPPED`：不可复用。

`force=true` 只绕过 `SUCCEEDED/UNSUPPORTED`，不能并行创建相同输入的
活动任务。

### 4.3 Reuse Hit 不属于当前批次

命中已有任务时，当前 batch 不新增文件任务：

- 只增加 `reusedCount`，并在受理预览返回已有 `taskId`；
- 不等待命中的 `PENDING/RUNNING` 任务；
- 不为当前 batch 重发该任务的文件 Callback；
- 若 batch 没有新建任务，它立即 `COMPLETED` 并发送 batch Callback。

### 4.4 每文件只允许一个活动任务

每个文件最多存在一个 `PENDING/RUNNING` Build task：

- 新请求与活动任务输入相同：Reuse Hit；
- 输入 checksum 或 Build Profile 不同：在同一事务内将旧任务置为
  `SKIPPED/SUPERSEDED`、撤销 lease、推进旧 batch，再创建新任务；
- 旧 Worker 即使继续运行也不能提交结果；
- 被取代任务在终态提交后发送其原 batch 的文件 Callback，必要时发送原
  batch 的完成 Callback。

## 5. 数据模型

### 5.1 `knowledge_build_batch`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `batch_id` | varchar(64) PK | 对外批次 ID |
| `knowledge_base_id` | bigint FK | 所属知识库 |
| `task_type` | varchar(32) | 固定 `FILE_BUILD` |
| `scope` | varchar(16) | `SINGLE_FILE` 或 `DIRECTORY` |
| `target_path_snapshot` | text | 请求目标，仅供审计 |
| `status` | varchar(16) | `pending/processing/completed` |
| `candidate_count` | bigint | 快照候选数 |
| `eligible_count` | bigint | 新建加复用数 |
| `accepted_count` | bigint | 本 batch 新建任务数 |
| `reused_count` | bigint | 命中已有任务数 |
| `acceptance_skipped_count` | bigint | 受理前跳过数；与 task 终态聚合区分 |
| `completed_count` | bigint | 已终态的新建任务数 |
| `version` | bigint | 每次终态推进递增 |
| `created_at` | timestamptz | 创建时间 |
| `completed_at` | timestamptz | 完成时间 |
| `updated_at` | timestamptz | 更新时间 |

Batch 不使用 `FAILED`。全部新建任务进入终态后为 `COMPLETED`；空目录或纯
复用批次直接完成。`succeeded/failed/skipped/unsupported` 细分数量从 task
表按 batch 聚合。

### 5.2 `knowledge_build_task`

现有表增量扩展为：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kid` | bigserial PK | task ID |
| `knowledge_base_id` | bigint FK | 所属知识库 |
| `fs_entry_id` | bigint FK | 稳定文件 ID |
| `batch_id` | varchar(64) NULL FK | 外部 batch；INLINE 为空 |
| `origin` | varchar(32) | `API`、`ENTITY_DISCOVERY`、`ENTITY_ENRICH` |
| `execution_mode` | varchar(16) | `BACKGROUND` 或 `INLINE` |
| `parent_semantic_task_id` | bigint NULL FK | Entity 父任务 |
| `file_path_snapshot` | text | 受理路径，仅供审计 |
| `input_checksum` | varchar(128) | 数据库 checksum 快照 |
| `input_is_deleted` | boolean | 删除状态快照 |
| `build_profile` | jsonb | 可读构建配置快照 |
| `build_profile_hash` | varchar(64) | 规范化 profile SHA-256 |
| `status` | varchar(32) | 任务状态 |
| `current_stage` | varchar(32) | 当前阶段 |
| `progress` | smallint | 阶段里程碑 |
| `priority` | integer | 服务端调度优先级 |
| `result_payload` | jsonb | 终态结果 |
| `error_code` | varchar(64) | 终态原因码 |
| `error_message` | text | 截断、脱敏后的错误信息 |
| `failure_kind` | varchar(32) | 失败分类 |
| `outcome_uncertain` | boolean | 中断前是否可能有副作用 |
| `worker_id` | varchar(160) | 当前 Worker |
| `lease_token` | varchar(64) | 当前领取的 fencing token |
| `heartbeat_at` | timestamptz | 最近心跳时间 |
| `lease_expires_at` | timestamptz | 租约到期时间 |
| `started_at` | timestamptz | 开始时间 |
| `finished_at` | timestamptz | 结束时间 |
| `created_at` | timestamptz | 创建时间 |
| `updated_at` | timestamptz | 更新时间 |

约束和索引至少包括：

```sql
CREATE UNIQUE INDEX uq_knowledge_build_task_active_per_file
    ON knowledge_build_task (fs_entry_id)
    WHERE status IN ('pending', 'running');
```

还需为 claim、batch/status 查询、按文件查最新任务，以及按
`fs_entry_id + input_checksum + input_is_deleted + build_profile_hash + status`
查复用任务建立索引。

任务历史暂不清理。文件更新不得再删除 `knowledge_build_task` 历史。

## 6. 状态机

### 6.1 Task 状态

```text
PENDING -> RUNNING -> SUCCEEDED
                   -> FAILED
                   -> SKIPPED
                   -> UNSUPPORTED
```

状态集合：

- `PENDING`；
- `RUNNING`；
- `SUCCEEDED`；
- `FAILED`；
- `SKIPPED`；
- `UNSUPPORTED`。

`INPUT_STALE`、`SOURCE_DELETED`、`SUPERSEDED`、
`KNOWLEDGE_BASE_DELETED` 是 `SKIPPED` 原因；`TASK_TIMEOUT`、
`WORKER_LOST` 和执行异常属于 `FAILED`。本期没有 `CANCELLED`。

### 6.2 Stage 与 progress

| Stage | progress | 含义 |
| --- | ---: | --- |
| `ACCEPTED` | 0 | 已入库 |
| `EXTRACTING` | 10 | 原文件转 Markdown |
| `CHUNKING` | 35 | 正文分块 |
| `EMBEDDING` | 55 | 生成向量 |
| `COMMITTING` | 90 | 最终校验和提交 |
| 任意终态 | 100 | 已结束 |

阶段是里程碑，不表示预计剩余时间。每次阶段变化独立提交，避免长事务使
中间状态不可见。

### 6.3 Batch 状态

Batch 状态为 `PENDING/PROCESSING/COMPLETED`，没有 `FAILED`。所有本批次
新建任务进入终态后完成，即使含失败、跳过或不支持任务。

聚合字段包括：`pendingCount`、`runningCount`、`succeededCount`、
`failedCount`、`skippedCount`、`unsupportedCount`。其中 `SUPERSEDED`、
`INPUT_STALE`、`SOURCE_DELETED` 和 `KNOWLEDGE_BASE_DELETED` 都计入
`skippedCount`。

## 7. 独立 Build Runner

### 7.1 隔离和并发

Build Runner 只领取 `knowledge_build_task` 的 `BACKGROUND/PENDING` 任务；
Entity Runner 只领取 semantic task。

```text
单进程默认 Build 并发 = 16
部署总 Build 并发 = 启用 Build Worker 的进程数 * 16
```

Build 与 Entity 不共享应用层 Embedding 并发限制。两者仍可能在外部
Embedding 服务、数据库、对象存储和默认线程池层面争用资源，部署必须按
二者总负载配置容量。

### 7.2 调度

- 单文件 batch 的任务优先于目录 batch；
- 同优先级按创建时间 FIFO；
- 优先级由服务端根据 scope 决定，调用方不能传入；
- 等待时间提升优先级，避免目录任务永久饥饿。

### 7.3 领取、租约和失败

Runner 使用 `FOR UPDATE SKIP LOCKED` 原子领取任务，设置 worker ID、唯一
lease token、心跳和到期时间。旧 Worker 的阶段更新和终态提交都必须带
状态与 lease token 条件。

默认配置：

| 配置 | 默认值 |
| --- | ---: |
| `KNOWLEDGE_BUILD_WORKER_CONCURRENCY` | 16 |
| `KNOWLEDGE_BUILD_WORKER_POLL_SECONDS` | 3 |
| `KNOWLEDGE_BUILD_TASK_TIMEOUT_SECONDS` | 1200 |
| `KNOWLEDGE_BUILD_LEASE_SECONDS` | 180 |
| `KNOWLEDGE_BUILD_HEARTBEAT_SECONDS` | 30 |
| `KNOWLEDGE_BUILD_REAPER_SECONDS` | 30 |
| `KNOWLEDGE_BUILD_WORKER_STATUS_LOG_SECONDS` | 60 |
| `KNOWLEDGE_BUILD_SHUTDOWN_GRACE_SECONDS` | 60 |

不自动重试：

- 超时直接 `FAILED/TASK_TIMEOUT`；
- 租约过期直接 `FAILED/WORKER_LOST`；
- 二者均设置 `outcomeUncertain=true`；
- 调用方需要重建时重新发起请求。

## 8. 执行和一致性

### 8.1 只信任数据库 checksum

Worker 只比较数据库 `knowledge_fs_entry.checksum` 与 task 的
`input_checksum`，不重新计算对象存储字节 checksum。

### 8.2 开始执行前失效派生数据

Worker 取得有效 lease 后，在短事务内锁定文件和 task，并确认：

- 文件 ID 仍存在；
- `is_deleted` 和 checksum 与输入快照相同；
- task 仍为该文件唯一的 `RUNNING` 任务；
- worker ID、lease token 和 lease 到期时间仍有效；
- 尚未超过 task timeout。

检查失败时任务进入对应终态，不清理数据。检查通过后清理：

- Markdown 存储引用和 line count；
- chunks 及其 embeddings；
- 检索投影；
- 文件读取/检索缓存。

数据库清理先提交，再 best-effort 删除固定 Markdown 对象。删除失败只记
日志；后续成功构建会覆盖固定 key。

不得清理原始文件、文件 metadata、文件引用关系、历史 Build task/batch。

文件内容更新接口本身也负责清理派生数据，但不得再删除 Build 历史。

### 8.3 重计算与提交

清理提交后不持有长事务。Worker 依次读取原文件、转换 Markdown、分块和
调用 Embedding。

最终提交时重新锁定文件和 task，校验：

```text
file_id
current checksum == input_checksum
current is_deleted == input_is_deleted == false
task status == RUNNING
worker_id and lease_token still match
lease_expires_at > now()
task timeout has not elapsed
```

校验成功后进入 `COMMITTING`，写入固定 Markdown key，替换 chunks、
embeddings 和检索投影，更新 Markdown 元数据，并在同一数据库事务内将
task 和 batch 推进到终态。对象写入成功但数据库回滚时，该固定对象没有
数据库引用；任务进入 `FAILED`，下次构建会清理或覆盖它。

校验失败时不得写入对象或数据库产物：

- checksum 改变：`SKIPPED/INPUT_STALE`；
- 文件删除：`SKIPPED/SOURCE_DELETED`；
- 被新请求取代：`SKIPPED/SUPERSEDED`；
- lease 丢失：由当前 Worker 放弃提交，Reaper 负责终态。
- task 超时：由当前 Worker 放弃提交，Reaper 负责终态。

### 8.4 用户变更

- 内容更新：允许执行；在更新事务内清理派生数据，将活动 task 置为
  `SKIPPED/INPUT_STALE`，撤销 lease 并推进 batch；
- 文件或目录删除：允许执行；相关活动 task 置为
  `SKIPPED/SOURCE_DELETED`；
- 移动和重命名：允许执行，不改变任务；
- 新建和移入目录：不改变已经固定的 batch 文件清单。

上述事务提交后再发布被终止任务的文件 Callback 和可能的 batch Callback。

### 8.5 已构建判断

文件只有同时满足以下条件才是 Built Content：

- 最新 Build task 为 `SUCCEEDED`；
- 该 task 的 `input_checksum` 等于文件当前 checksum；
- 文件仍有关联 Markdown、chunks、embeddings 和检索投影。

`UNSUPPORTED` 单独展示，不视为 `SUCCEEDED`。Build Profile 用于下一次
受理时判断任务能否复用，不自动改变或触发构建。

## 9. Entity 内部构建

Discovery/Enrich 不进入外部 Build 队列，也不等待目录任务：

- 抽取无调度副作用的 `BuildExecutionService`，供两条路径复用；
- Entity worker 同步等待执行服务完成，之后 semantic task 才能成功；
- 内部构建仍写 `knowledge_build_task`，记录 `origin`、
  `execution_mode=INLINE` 和 `parent_semantic_task_id`；
- INLINE task 没有 `batch_id`，不发布 Build 文件或批次 Callback；
- Entity 自身仍按原 semantic task 发布 Callback；
- 本次设计不改变 Discovery/Enrich 的既有业务行为。

## 10. Callback

Callback 继续使用服务端注入的 `KnowledgeEventPublisher`，请求不得传 URL、
header 或 secret。投递为 best-effort：终态事务提交后发布，超时或失败只记
日志，不改变任务结果，不重试、不保存 outbox。状态查询是事实来源。

事件版本为 2：

- `build.file.completed`；
- `build.batch.completed`。

文件事件包含 batch ID、task ID、`FILE_BUILD`、文件 ID、非权威路径快照、
status、stage、progress，以及互斥的 result/error。批次事件包含 scope、目标
路径快照、受理计数和各终态计数；其中 `acceptanceSkippedCount` 是受理前
跳过数，`skippedCount` 是已创建 task 的终态跳过数。

Reuse Hit 没有当前 batch 的文件 Callback。空目录、全部受理前跳过或纯复用
batch 在受理事务内直接完成，由 API 在事务提交后调度 batch Callback。正常
执行、mutation 终止或任务取代则由提交终态的组件在事务提交后发布对应文件
和 batch Callback。`SUCCEEDED/FAILED/SKIPPED/UNSUPPORTED` 都是文件事件
终态。

## 11. 状态查询和兼容接口

### 11.1 统一状态接口

- `processingBatchStatus` 支持 `taskType=FILE_BUILD`；
- `processingTaskStatus` 支持 `taskType=FILE_BUILD`，并支持按 batch ID、
  file ID 或 `taskType + taskId` 精确查询；Semantic 与 Build 的 task ID
  命名空间独立，task ID 不得脱离 taskType 使用；
- 任务返回稳定 file ID 和非权威 `filePathSnapshot`；
- 精确关联必须使用 batch ID、task ID 或 file ID，不得使用路径。

Batch 的 `unsupportedCount` 是 Build 专用新增计数；其他任务类型返回 0。

### 11.2 `fileBuildStatus`

旧接口继续按 `filePath` 定位当前文件并查询其最新 task，状态映射：

| 新状态 | 旧状态 |
| --- | --- |
| `PENDING/RUNNING` | `running` |
| `SUCCEEDED` | `complete` |
| `FAILED` | `failed` |
| `SKIPPED` | `skipped`（新增） |
| `UNSUPPORTED` | `unsupported` |

阶段映射：

| 新阶段 | 旧 currentStep |
| --- | --- |
| `ACCEPTED/EXTRACTING` | `markdown` |
| `CHUNKING` | `chunking` |
| `EMBEDDING/COMMITTING` | `vectorizing` |
| `SUCCEEDED` | `complete` |

接口增加 `errorCode`，区分 `INPUT_STALE`、`SOURCE_DELETED`、
`SUPERSEDED` 等终态。

### 11.3 `buildResult`

`buildResult` 继续按当前路径定位 file ID，再返回最新任务与当前派生数据。
响应必须依据第 8.5 节判定文件是否已构建，不能仅凭“存在历史成功任务”。

## 12. 删除知识库

知识库删除不等待后台任务：

- 删除事务先使知识库不可再受理新任务；
- 该库所有 `PENDING/RUNNING` Build task 进入
  `SKIPPED/KNOWLEDGE_BASE_DELETED` 并撤销 lease；
- 同时终止活动 Discovery/Enrich task，避免删除后继续产生文件副作用；
- 批量推进相关 batch；
- 事务提交后发布文件和批次终态事件；
- 软删除保留历史；未来物理删除才由外键级联清理。

## 13. 故障和终态矩阵

| 场景 | Task 状态 | errorCode | 自动重试 | 提交产物 |
| --- | --- | --- | --- | --- |
| 成功 | `SUCCEEDED` | - | 否 | 是 |
| 不支持类型 | `UNSUPPORTED` | `UNSUPPORTED_FILE_TYPE` | 否 | 否 |
| 内容改变 | `SKIPPED` | `INPUT_STALE` | 否 | 否 |
| 文件删除 | `SKIPPED` | `SOURCE_DELETED` | 否 | 否 |
| 新任务取代 | `SKIPPED` | `SUPERSEDED` | 否 | 否 |
| 知识库删除 | `SKIPPED` | `KNOWLEDGE_BASE_DELETED` | 否 | 否 |
| 执行超时 | `FAILED` | `TASK_TIMEOUT` | 否 | 否 |
| Worker 丢失 | `FAILED` | `WORKER_LOST` | 否 | 不确定 |
| 其他执行错误 | `FAILED` | `BUILD_FAILED` | 否 | 否或不确定 |

## 14. 数据迁移与上线顺序

现有 `knowledge_build_task` 没有 batch、输入 checksum、Build Profile 和租约
字段，不能假定旧任务符合新复用协议。迁移遵循扩展优先、历史保留和不伪造
配置三个原则。

### 14.1 Schema 扩展

本功能的全部 DDL 和数据回填都必须以新增的增量 SQL migration 交付：

- 脚本放入 `src/by_qa/knowledge_base/sql/`，使用下一个可用数字前缀按执行
  顺序追加；当前设计基线的最高版本是 `037`，实现时从当时的下一个可用
  版本开始；
- 禁止修改 `006_knowledge_build_task.sql`、
  `013_knowledge_build_task_indexes.sql` 或任何其他历史 migration；
- 已由 `knowledge_schema_migration` 记录的脚本视为不可变，修正也必须新增
  更高版本脚本，不能修改已记录脚本以避免 checksum drift；
- 新装环境同样按完整 migration 序列建库，不额外维护一份会与增量脚本
  漂移的“最新全量 schema”；
- 每个脚本由现有 bootstrap migration ledger 独立记录并在事务内执行，不以
  `IF NOT EXISTS` 代替版本管理。

建议拆成四个连续的增量脚本，实际编号以实现时下一个可用版本为准：

1. `*_knowledge_build_batch.sql`：创建 `knowledge_build_batch`；
2. `*_knowledge_build_task_background_extension.sql`：只为现有
   `knowledge_build_task` 增加第 5.2 节字段和暂时宽松的约束；
3. `*_knowledge_build_task_legacy_backfill.sql`：完成第 14.2 节历史数据回填
   和旧活动任务收口；
4. `*_knowledge_build_task_active_constraints.sql`：在数据满足条件后增加新任务
   约束、查询索引和“每文件一个活动任务”的部分唯一索引。

迁移不得删除旧 task，也不得改用版本化 Markdown object key。外部
`BACKGROUND` task 必须有 batch、所有新 task 必须有输入快照和 Build
Profile 的强约束，只能在回填完成后通过最后一个增量脚本收紧。

### 14.2 旧任务回填

- 旧任务统一回填 `origin=API`、`execution_mode=BACKGROUND`，`batch_id=NULL`；
- 旧记录的真实解析、分块和 Embedding 配置不可证明，使用明确的
  `{"profileVersion": 0, "legacy": true}` 标记及其规范 hash，不伪造成当前
  Build Profile；因此旧任务不会命中新协议的复用键；
- 只有旧任务为成功、当前数据库 checksum 存在且 Markdown、chunks、
  embeddings、检索投影完整时，才把当前 checksum 回填为该任务的
  `input_checksum`，使 `isBuilt` 可继续为真；
- 其他旧记录允许输入快照为空，只用于历史展示，不能参与复用或 Built
  Content 判定；
- 历史任务暂不清理。

### 14.3 活动任务与发布顺序

上线前先停止新的旧式 `fileToMarkdownIndex` 受理并等待当前 FastAPI
BackgroundTasks 退出。宽限期后仍为活动状态的旧任务直接进入
`FAILED/MIGRATION_INTERRUPTED`，不自动重试；随后再建立活动任务唯一索引。

推荐发布顺序：

1. 停止旧受理并排空或终结旧活动任务；
2. 按数字顺序执行新增的扩展、回填和约束增量 SQL；
3. 部署支持新表结构的 API、状态查询和 mutation 协作逻辑；
4. 启动独立 Build Runner；
5. 开放 `fileToMarkdownIndex` 受理并观察 pending age、失败率和 Callback
   日志。

回退只能停止新受理和 Build Runner，不能让旧代码继续写已经进入新状态机
的任务。数据库不执行破坏性 down migration；需要修正或回退 schema 时新增
更高版本的前向增量 SQL，并保留扩展字段和历史数据。

## 15. 实现拆分

建议按以下边界实现：

1. `BuildProfileProvider`：产生强类型 profile、规范 JSON 和 hash；
2. 增量 SQL migrations：建 batch 表、扩展 task 表、回填历史、最后收紧
   约束与索引；
3. `FileBuildAcceptanceService`：目标解析、目录快照、复用/取代和 batch
   受理；
4. `FileBuildBackgroundRunner`：领取、并发、心跳、超时和 Reaper；
5. `BuildExecutionService`：清理、解析、分块、Embedding、CAS 和提交；
6. Build batch/task repositories；
7. 状态查询 facade：聚合 semantic 与 Build 两套独立存储；
8. Build Callback 事件构造与统一 Publisher；
9. 文件、目录和知识库 mutation 的活动任务终止协作器。

`knowledge_build` 不反向依赖 `knowledge_base`；共享输入模型继续放在
`knowledge_common` 或由 `knowledge_base` 编排注入。

## 16. 测试矩阵

至少覆盖：

- 单文件、空目录、嵌套目录和 `/` 快照；
- 快照后新建、移入、移出、重命名；
- `SOURCE_NOT_READY` 和 `UNSUPPORTED`；
- active/succeeded/unsupported 复用及 `force=true`；
- 不同 checksum/profile 取代活动任务；
- 内容更新、文件删除、目录删除和知识库删除；
- lease fencing、超时、Worker 丢失且不重试；
- 单进程并发 16、多 Runner 的 `SKIP LOCKED`；
- 单文件优先级及目录 aging；
- fixed key 写入成功但数据库回滚；
- batch 含成功、失败、跳过、不支持的计数不变量；
- 纯复用和空 batch 的立即 Callback；
- supersede、文件更新、删除和知识库删除提交后的终态 Callback；
- Callback 失败不改变终态；
- Entity INLINE 构建不被 Build Runner 领取且不发 Build Callback；
- `fileBuildStatus` 兼容映射和 `processing*Status` 的 FILE_BUILD 查询。
- legacy task 回填、不可复用、Built Content 保留和活动任务迁移终结。
- migration 只新增更高版本 SQL、历史脚本 checksum 不变、重复启动不重复
  执行已登记脚本。

## 17. 可观测性

建议记录 pending/running 数、oldest pending age、各 stage 耗时、Embedding
延迟与限流、reuse 命中/未命中原因、superseded/input-stale 数、lease 丢失、
Callback 失败、每 batch 文件数及受理耗时。日志关联 `batch_id`、`task_id`、
`fs_entry_id`、checksum 摘要、profile hash、worker ID 和 lease token 摘要，
不得记录正文、Embedding 或未脱敏异常堆栈。
