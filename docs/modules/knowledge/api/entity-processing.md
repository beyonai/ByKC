# KnowledgeEntity 异步处理约定

## 任务模型

- 一次 `entityDiscovery` 或 `entityEnrich` 请求形成一个 `batchId`。
- 每个实际处理的文件形成一个 `taskId`。
- 单文件失败不会阻止同批次其他文件。
- `extraParams` 保存在 batch/task 中，并原样传递给 Callback。

Discovery 和 Enrich 共用同一套持久化 batch/task 调度，但任务类型、资格检查和文件副作用相互独立。受理接口不在 HTTP 请求内执行模型推理。

## 状态

task 状态：`PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`SKIPPED`、`CANCELLED`。

batch 状态：`PENDING`、`PROCESSING`、`COMPLETED`。batch 中可以同时包含成功和失败的文件任务。

### task 终态语义

| 状态 | 含义 |
| --- | --- |
| `SUCCEEDED` | 文件处理和结果提交成功 |
| `FAILED` | 输入、模型、存储、超时或 Worker 丢失导致失败 |
| `SKIPPED` | 文件无需执行，例如 Enrich 无授权证据 |
| `CANCELLED` | 任务被显式取消；当前 HTTP API 未提供取消接口 |

batch 不使用 `FAILED` 表示“包含失败文件”。当所有文件进入终态时，batch 为 `COMPLETED`，具体成败通过各计数字段表达。

## Worker 默认行为

| 配置 | 默认值 | 语义 |
| --- | --- | --- |
| `KNOWLEDGE_ENTITY_WORKER_CONCURRENCY` | `4` | 单进程文件并发数 |
| `KNOWLEDGE_ENTITY_WORKER_POLL_SECONDS` | `3` | pending 任务轮询周期 |
| `KNOWLEDGE_ENTITY_TASK_TIMEOUT_SECONDS` | `1200` | 单文件总执行时限 |
| `KNOWLEDGE_ENTITY_LEASE_SECONDS` | `180` | running 任务租约时长 |
| `KNOWLEDGE_ENTITY_HEARTBEAT_SECONDS` | `30` | 租约续期周期 |
| `KNOWLEDGE_ENTITY_REAPER_SECONDS` | `30` | 过期 running 任务扫描周期 |
| `KNOWLEDGE_ENTITY_WORKER_STATUS_LOG_SECONDS` | `60` | Worker 存活、活动任务和空闲槽位日志周期 |
| `KNOWLEDGE_ENTITY_SHUTDOWN_GRACE_SECONDS` | `60` | 服务关闭时等待活动任务的宽限时间 |

每个启用 Worker 的 API 进程启动一个 Runner：

```text
总文件并发数 = 启用 Worker 的进程数 × KNOWLEDGE_ENTITY_WORKER_CONCURRENCY
```

多 Runner 通过数据库 `FOR UPDATE SKIP LOCKED` 竞争文件任务，租约 token 防止已丢失所有权的旧 Worker 覆盖新状态。

### 超时与租约的区别

- 任务超时表示当前 Runner 仍存活，但单文件执行超过上限。
- 租约过期表示没有 Worker 继续为该 running 任务续租。
- `TASK_TIMEOUT` 和 `WORKER_LOST` 都是终态，当前不重新放回 pending。
- `outcomeUncertain=true` 表示任务被中断时可能已产生部分外部副作用，调用方应查询当前文件状态后决定是否手工重发。

超时任务进入 `FAILED/TASK_TIMEOUT`，不自动重试，`outcomeUncertain=true`。租约过期任务进入 `FAILED/WORKER_LOST`，同样不自动重试。

## Callback

Callback 由 `KnowledgeEntityProcessingCallback` Protocol 定义：

- `on_file_completed`：每个文件进入终态后调用。
- `on_batch_completed`：整个批次完成后调用。

Callback 失败不改变已提交的任务状态。当前不保证重试或必达。

Callback 在任务终态事务提交后调用，因此实现方不能依赖“抛异常回滚任务”。当前 Callback 未设置独立超时；实现方应自行使用有界 HTTP/队列超时，避免长时间占用 Worker 并发槽位。

## 常见任务错误

| `errorCode` | 含义 | 自动重试 |
| --- | --- | --- |
| `TASK_INPUT_UNAVAILABLE` | 领取后源文件已删除或输入无法读取 | 否 |
| `PROCESSING_FAILED` | 模型、存储或其他处理阶段失败 | 否 |
| `TASK_TIMEOUT` | 超过单文件执行时限 | 否 |
| `WORKER_LOST` | Worker 租约过期 | 否 |

## 相关接口

- [entityDiscovery](interfaces/entityDiscovery.md)
- [entityEnrich](interfaces/entityEnrich.md)
- [processingTaskStatus](interfaces/processingTaskStatus.md)
- [processingBatchStatus](interfaces/processingBatchStatus.md)
