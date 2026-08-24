# 文件与目录同步变更统一事件通知设计

## 1. 文档目标

本文定义文件、目录同步增删改接口的轻量事件通知方案。接口主业务仍在 HTTP 请求内同步完成；业务成功提交后，通过 FastAPI BackgroundTasks 在响应发送后调用统一的 KnowledgeEventPublisher，由部署方注入的 Publisher 实现向业务后端发送消息。

目标：

- 覆盖文件和目录的创建、修改、删除与移动；
- Callback 不增加主接口响应等待时间；
- Callback 失败不改变已经成功的资源变更；
- 核心项目只定义通知时机、统一事件信封和 Publisher Protocol，不承接业务逻辑；
- 与 KnowledgeEntity Discovery/Enrich 共用 Publisher、provider 加载、超时和异常隔离能力；
- 保留同步资源变更与异步语义任务各自的触发机制和领域 payload；
- 不新增表、不保存投递状态、不重试、不保证通知必达。

本设计与 [KnowledgeEntity 后台处理与 Callback 简化设计](./knowledge-entity-background-processing-callback-design.md) 的职责边界一致：核心只定义 Protocol，HTTP、MQ 等传输方式由注入实现决定。在此基础上，本文进一步把新资源变更通知与 Discovery/Enrich 的投递层统一为 KnowledgeEventPublisher；Discovery/Enrich 的任务、批次语义和后台 runner 不变。

## 2. 适用范围

### 2.1 首期接入接口

| 资源 | 操作 | API | eventType |
| --- | --- | --- | --- |
| 目录 | 创建 | POST /api/v1/directories/create | resource.directory.created |
| 目录 | 删除 | POST /api/v1/directories/delete | resource.directory.deleted |
| 目录 | 重命名 | POST /api/v1/directories/update | resource.directory.updated |
| 文件 | 导入 | POST /api/v1/knowledgeItems/import | resource.file.imported |
| 文件 | 内容更新 | POST /api/v1/knowledgeItems/update | resource.file.updated |
| 文件 | 删除 | POST /api/v1/knowledgeItems/delete | resource.file.deleted |
| 文件/目录 | 移动或重命名 | POST /api/v1/knowledgeItems/move | resource.moved |
| 文件 | 异步 Markdown/索引构建终态 | POST /api/v1/fileToMarkdownIndex | build.file.completed |

knowledge-items 的 kebab-case 兼容路由与 knowledgeItems 路由视为同一个 eventType。

### 2.2 首期不接入

- search、readFile、listDir、glob、downloadFile 等只读接口；
- Discovery/Enrich 的业务处理和调度机制；其事件投递层纳入本文的统一 Publisher；
- 知识库本身的增删改；
- 文件元数据更新、关系维护等非文件树主操作；
- 失败 HTTP 请求的审计通知。

后续接口应通过扩展 eventType 和事件构造器接入，不得在 Route 内直接编写请求业务后端的逻辑。

## 3. 核心决策

### 3.1 只通知已经提交的成功变更

Callback 的语义是“文件树变更已经成功提交”，不是“HTTP 请求已经结束”。

只有业务 Service 成功返回、内部事务已经 commit 后，Route 才注册 Background Callback。以下情况不发送：

- 请求参数校验失败；
- 知识库、文件或目录不存在；
- 业务冲突或其他业务校验失败；
- 数据库、对象存储或未预期异常导致主业务回滚；
- 请求没有产生实际资源变更。

业务后端因此可把收到的事件解释为已生效的资源变更。

### 3.2 每次 API 调用最多发送一个事件

事件粒度是一次成功 API 变更，而不是每一条数据库记录：

- 单文件、单目录操作产生一个事件；
- ZIP 导入产生一个批量事件，items 携带各条目结果；
- 批量 move 产生一个批量事件，items 携带各源路径结果；
- 删除或重命名目录只通知根目录，不枚举子树节点。

部分成功的 ZIP 或 move 只要至少一项实际成功，就发送一个事件；全部失败且无数据变更时不发送。

### 3.3 通知为 best-effort

本设计明确接受通知丢失，不新增：

- Callback/outbox 表；
- 投递状态和历史；
- 重试与补发；
- 重启恢复；
- Callback 管理 API。

进程在响应发送后、任务执行前或执行中退出，Callback 目标不可用，Callback 超时或抛异常时，通知均可丢失。文件、目录的数据库与对象存储状态仍是最终事实来源。

### 3.4 统一投递层，不统一领域事件和触发机制

资源变更、异步文件构建与 Discovery/Enrich 最终都向外部业务系统发送事件，因此统一以下基础设施：

- KnowledgeEvent 通用信封；
- KnowledgeEventPublisher Protocol 和 Noop 实现；
- provider 加载和环境变量；
- Invoker 的超时、异常隔离和日志；
- eventId、eventType、eventVersion、knCode 等公共字段；
- 部署方提供的 HTTP、MQ 或其他发送实现。

以下领域部分不强行统一：

- 资源变更使用 resource mutation payload；
- 异步文件构建使用 build terminal payload；
- Discovery/Enrich 使用 semantic task/batch payload；
- 资源变更由 FastAPI BackgroundTasks 在同步接口响应后发布；
- 异步文件构建由原 BackgroundTasks 执行，并在 build task 终态提交后直接发布；
- Discovery/Enrich 由后台 runner/reaper 在任务或批次终态提交后发布。

`build.file.completed` 仅对外部 `/api/v1/fileToMarkdownIndex` 调度的构建任务发布。Discovery/Enrich 内部复用构建 Service 时不额外发布 build 事件，仍以对应 semantic 终态事件表达整个接口结果。

统一后的 Publisher 只负责“发送一个事件”，不负责决定何时发送，也不解释 payload。禁止创建包含大量可空字段的万能领域模型。

### 3.5 核心不实现业务传输

核心只定义统一事件信封、Publisher Protocol 和 Noop 实现，也不规定：

- 业务后端 URL；
- HTTP、RPC、MQ 或本地函数等传输方式；
- 认证、签名、请求头；
- 用户、租户、订单或工作流模型；
- 业务后端收到通知后的动作。

部署方通过一个 Python provider 注入 Publisher 实现。请求调用方不能传入任意 Callback URL、header 或 secret。

## 4. 总体时序

~~~mermaid
sequenceDiagram
    participant Caller as 调用方
    participant API as FastAPI Route
    participant Service as Knowledge Service
    participant DB as DB / Object Storage
    participant BG as BackgroundTasks
    participant Invoker as Event Invoker
    participant Impl as 用户 EventPublisher
    participant Backend as 业务后端

    Caller->>API: 文件/目录增删改
    API->>Service: 执行同步变更
    Service->>DB: 写入
    DB-->>Service: commit 成功
    Service-->>API: 返回变更快照
    API->>API: 构造不可变事件
    API->>BG: add_task(invoker, event)
    API-->>Caller: HTTP 成功响应
    BG->>Invoker: publish(event)
    Invoker->>Impl: publish(event)
    Impl->>Backend: HTTP / MQ / 其他方式
~~~

约束：

1. Service 先完成主业务与事务提交；
2. Route 根据已提交结果构造事件；
3. 交给 BackgroundTasks 的必须是完整、不可变的内存快照；
4. 资源事件的 publish 在响应发送后执行；
5. Publisher 结果不影响响应和已提交数据。

统一投递关系：

~~~mermaid
flowchart LR
    Resource["文件/目录同步变更"] --> ResourceEvent["Resource Mutation Event"]
    Build["异步文件构建"] --> BuildEvent["Build File Completed Event"]
    Semantic["Discovery/Enrich Runner"] --> SemanticEvent["Semantic File/Batch Event"]
    ResourceEvent --> Publisher["KnowledgeEventPublisher"]
    BuildEvent --> Publisher
    SemanticEvent --> Publisher
    Publisher --> Target["业务后端 / MQ / 其他目标"]
~~~

三条事件源共用 Publisher，但保留各自原有调度和事务边界。

## 5. 统一事件与 Publisher 协议

### 5.1 Event type

资源事件：

~~~python
class ResourceEventType(StrEnum):
    DIRECTORY_CREATED = "resource.directory.created"
    DIRECTORY_UPDATED = "resource.directory.updated"
    DIRECTORY_DELETED = "resource.directory.deleted"
    FILE_IMPORTED = "resource.file.imported"
    FILE_UPDATED = "resource.file.updated"
    FILE_DELETED = "resource.file.deleted"
    RESOURCE_MOVED = "resource.moved"
~~~

语义处理事件：

~~~python
class SemanticEventType(StrEnum):
    DISCOVERY_FILE_COMPLETED = "semantic.discovery.file.completed"
    DISCOVERY_BATCH_COMPLETED = "semantic.discovery.batch.completed"
    ENRICH_FILE_COMPLETED = "semantic.enrich.file.completed"
    ENRICH_BATCH_COMPLETED = "semantic.enrich.batch.completed"

class BuildEventType(StrEnum):
    FILE_COMPLETED = "build.file.completed"
~~~

eventType 是稳定外部合约。内部函数名、路由别名或 Service 结构变化不得改变已经发布的值。

### 5.2 通用事件信封

`KnowledgeEvent` 是以 `eventType` 为判别字段的 Pydantic 联合类型，不是 `payload: Mapping[str, Any]` 弱类型信封。每个具体 Event 都通过 `Literal[eventType]` 绑定唯一 payload 类型：

~~~python
class DirectoryCreatedEvent(KnowledgeEventBase):
    event_type: Literal["resource.directory.created"]
    payload: DirectoryCreatedPayload

class FileUpdatedEvent(KnowledgeEventBase):
    event_type: Literal["resource.file.updated"]
    payload: FileUpdatedPayload

KnowledgeEvent = DirectoryCreatedEvent | ... | BuildFileCompletedEvent
~~~

| 字段 | 说明 |
| --- | --- |
| event_id | 事件 UUID；业务后端可用于日志关联和幂等 |
| event_type | 稳定的领域事件类型 |
| event_version | 当前 eventType 的 payload schema 版本，初始为 1 |
| kb_code | 知识库编码 |
| occurred_at | 领域事实成功提交的 UTC 时间 |
| payload | 与 eventType 一对一绑定的严格 Pydantic 模型 |

通用信封只保存所有事件都有明确语义的公共字段。taskId、batchId、progress、sourcePath 等领域字段必须放在 payload 中。

### 5.3 资源变更 payload

资源事件不共用一个含大量可空字段的 payload。实现分别定义：

- `DirectoryCreatedPayload(resourceType="directory", targetPath)`；
- `DirectoryUpdatedPayload(resourceType="directory", sourcePath, targetPath)`；
- `DirectoryDeletedPayload(resourceType="directory", sourcePath)`；
- `FileImportedPayload(resourceType="file", targetPath, items, result: MutationSummary)`；
- `FileUpdatedPayload(resourceType="file", sourcePath, targetPath, result: MutationOperationResult)`；
- `FileDeletedPayload(resourceType="file", sourcePath)`；
- `ResourceMovedPayload(resourceType="mixed", sourcePath, targetPath, items, result: MutationSummary)`。

所有模型使用 `extra="forbid"`、`frozen=True`、`strict=True`。缺失必填字段、类型不正确或出现 `resourceId` 等未定义字段时立即校验失败。

### 5.4 语义处理 payload

现有 FileCompletedCallbackInput 映射为 semantic file payload：

~~~python
@dataclass(frozen=True, slots=True)
class SemanticFileCompletedPayload:
    batch_id: str
    task_id: str
    task_type: str
    status: str
    knowledge_base_id: str
    file_id: str | None
    file_path: str
    progress: SemanticBatchProgress
    result: dict[str, JsonValue] | None
    error: SemanticProcessingError | None
~~~

现有 BatchCompletedCallbackInput 映射为 semantic batch payload：

~~~python
@dataclass(frozen=True, slots=True)
class SemanticBatchCompletedPayload:
    batch_id: str
    task_type: str
    knowledge_base_id: str
    progress: SemanticBatchProgress
~~~

knCode 和 completedAt 分别映射到通用信封的 kb_code 和 occurred_at，不在 payload 中重复。Discovery 和 Enrich 根据 taskType 选择各自的具体 Event 类型。

异步文件构建终态使用 `BuildFileCompletedPayload`，固定包含 `taskId/status/filePath/currentStep/result/error`。`complete` 必须有严格的 `chunkCount/lineCount` result 且不能有 error；`failed` 和 `unsupported` 必须有 `code/message` error 且不能有 result。该约束由 Pydantic 模型校验，不是任意 Mapping。

`KnowledgeEvent` 联合使用 Pydantic `TypeAdapter` 以 `event_type`/`eventType` 作为 discriminator。Publisher 对外发送时统一使用 `serialize_knowledge_event(event)` 得到 camelCase JSON；接收或测试外部事件时使用 `parse_knowledge_event(value)` 完成 discriminator 和 payload 联合校验。不允许 Publisher 自行拼接 payload 字段。

### 5.5 Publisher 与 Noop

~~~python
@runtime_checkable
class KnowledgeEventPublisher(Protocol):
    async def publish(self, event: KnowledgeEvent) -> None: ...


class NoopKnowledgeEventPublisher:
    async def publish(self, event: KnowledgeEvent) -> None:
        del event
~~~

未配置 provider 时使用 Noop，资源接口和语义任务行为均与当前版本一致。

### 5.6 统一 Invoker

~~~python
@dataclass(slots=True)
class KnowledgeEventPublisherInvoker:
    publisher: KnowledgeEventPublisher = field(
        default_factory=NoopKnowledgeEventPublisher
    )
    timeout_seconds: float = 5.0

    async def publish(self, event: KnowledgeEvent) -> None:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "knowledge event publish failed: "
                "event_id=%s event_type=%s error_type=%s",
                event.event_id,
                event.event_type,
                type(exc).__name__,
            )
~~~

Invoker 统一负责有界超时、异常隔离和结构化日志。第一版不重试；用户 Publisher 如果做进程内瞬时重试，仍必须受 Invoker 总超时约束。

事件构造器应对 payload 和 result 做 JSON 规范化或等价的深复制，确保后台执行不与请求对象共享可变容器。事件禁止保存 Request、UploadFile、文件二进制、数据库连接或不可 JSON 序列化对象。

本文中的 camelCase JSON 是推荐的 HTTP Publisher wire format；核心传递 KnowledgeEvent Python 类型，不绑定 HTTP。

## 6. 请求参数边界

资源变更、异步文件构建、Discovery 和 Enrich 请求都不定义 `extraParams`/`extra_params`。Callback 事件也不包含该字段。核心项目只发布自身拥有的严格领域事实，不承载调用方自定义关联数据。

核心同样不定义 `requestId` 或 `X-Request-Id` 协议。`eventId` 是通知的唯一标识。

## 7. 各接口事件映射

### 7.1 目录创建

~~~json
{
  "eventId": "82bbbd31-8d03-47af-bd4c-aec70062318f",
  "eventType": "resource.directory.created",
  "eventVersion": 1,
  "knCode": "1001",
  "occurredAt": "2026-08-24T10:30:00Z",
  "payload": {
    "resourceType": "directory",
    "sourcePath": null,
    "targetPath": "reports/2026",
    "items": [],
    "result": {}
  }
}
~~~

### 7.2 目录更新

sourcePath 是原目录路径，targetPath 是原父目录与 directoryName 计算出的新路径。事件表示整个子树已随根目录完成路径更新，不枚举子节点。

### 7.3 目录删除

sourcePath 是删除的目录根路径，targetPath 为空。事件只表达公开文件树路径语义，不暴露内部数据库主键。

### 7.4 单文件导入

targetPath 是规范化文件路径。事件不包含上传字节、Markdown 内容或文件摘要。

### 7.5 ZIP 导入

至少一项成功落库时发送一个 resource.file.imported 事件，payload.items 携带全部条目结果，payload.result 携带 total、succeeded、failed 汇总。全部失败且无数据变更时不发送。

### 7.6 文件更新

sourcePath 和 targetPath 都是当前文件路径。事件表示同步文件更新已经完成，不等待后续 Markdown timeline LLM summary。

更新路由已有 timeline summary Background Task。两项任务相互独立，不依赖完成顺序；任一失败不影响另一项。

### 7.7 文件删除

sourcePath 是删除文件路径，targetPath 为空。

### 7.8 文件或目录移动

事件复用 MoveKnowledgeItemsResponse.data：

- sourcePath 映射为 item.source_path；
- targetPath 映射为 item.target_path；
- success 和 error 原样保留；
- item 只保留路径、类型、成功状态和错误信息。

至少一项成功时发送；result 携带现有 summary。

## 8. Route 接入

### 8.1 依赖注入

register_routes 增加可选依赖：

~~~python
def register_routes(
    app,
    *,
    ...,
    get_knowledge_event_publisher_invoker=None,
): ...
~~~

为 None 时使用 Noop Invoker，保持现有测试和独立模块加载兼容。

### 8.2 成功路径注册任务

七类目标 Route 增加 BackgroundTasks 参数。统一辅助函数：

~~~python
def try_schedule_resource_mutation_event(
    background_tasks: BackgroundTasks,
    invoker: KnowledgeEventPublisherInvoker,
    event_factory: Callable[[], KnowledgeEvent],
) -> None:
    try:
        event = event_factory()
        background_tasks.add_task(invoker.publish, event)
    except Exception as exc:
        logger.warning(
            "resource mutation event scheduling failed: error_type=%s",
            type(exc).__name__,
        )
~~~

事件构造和任务注册同样属于附属通知逻辑。此时主业务已经提交，因此构造器或任务注册失败也不能把接口响应改成失败。辅助函数必须隔离这部分异常。

Route 示意：

~~~python
@app.post("/api/v1/directories/create")
async def create_directory(
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] = Body(...),
):
    command = CreateDirectoryRequest.model_validate(body)
    mutation = await service.create_directory(command)

    try_schedule_resource_mutation_event(
        background_tasks,
        event_publisher_invoker,
        lambda: build_directory_created_event(
            request=request,
            command=command,
            mutation=mutation,
        ),
    )
    return _documented_success_response(result_object={})
~~~

注册必须位于主业务成功路径、最终成功响应之前，不能放在 finally 中。注册完成后除构造成功响应外不再执行可能改变响应分支的业务步骤。

### 8.3 事件构造器

提供显式构造器，避免 Route 重复拼装字典：

~~~text
build_directory_created_event
build_directory_updated_event
build_directory_deleted_event
build_file_imported_event
build_file_updated_event
build_file_deleted_event
build_resource_moved_event
~~~

构造器负责路径规范化、结果转换、UUID、时间和 JSON 安全化。

### 8.4 不采用全局 HTTP Middleware

本设计不使用通用 Middleware 根据 HTTP status 自动发布事件，原因是：

- 当前部分接口使用 HTTP 200 配合 resultCode 表达业务失败，仅看 HTTP status 不可靠；
- Middleware 不知道 Service 事务是否已提交以及是否发生实际变更；
- Middleware 无法稳定获得删除前路径、批量条目结果等领域信息；
- 解析完整响应体会增加对响应封装和流式响应的耦合；
- 本需求只覆盖明确的写接口，不需要为全部 API 增加全局拦截成本。

因此采用“Route 成功分支显式选择 eventType，公共构造器和调度器消除重复”的方式。

## 9. Service 返回值

Callback 改造不改变原有 Service 返回契约。Route 使用已校验的请求路径和现有批量响应构造事件，不为通知查询或暴露内部 `kid`、`fs_entry_id` 等持久化标识。
| KnowledgeItemIngestionService.delete_knowledge_item | 删除文件 ID、路径快照 |
| KnowledgeBaseService.move_knowledge_items | 现有批量结果增量补充类型和 ID |

这些返回值只是已提交事实的内存快照，不新增持久化。对外 API 响应可以保持现状。

## 10. Provider 与配置

环境变量：

~~~text
BY_QA_EVENT_PUBLISHER_PROVIDER=my_package.events:build_publisher
BY_QA_EVENT_PUBLISH_TIMEOUT_SECONDS=5
~~~

加载规则：

1. 未配置时返回 Noop；
2. provider 使用 module:attribute；
3. attribute 是 factory 时调用，否则直接作为实例；
4. 使用 runtime-checkable Protocol 校验；
5. 已配置但无效时在服务启动阶段快速失败；
6. timeout 必须大于 0，默认 5 秒。

部署方示例：

~~~python
class BusinessBackendEventPublisher:
    async def publish(
        self,
        event: KnowledgeEvent,
    ) -> None:
        await self._client.post(
            self._endpoint,
            json=serialize_knowledge_event(event),
        )


def build_publisher() -> KnowledgeEventPublisher:
    return BusinessBackendEventPublisher(
        client=build_business_http_client(),
        endpoint=load_business_event_endpoint(),
    )
~~~

业务 URL、认证和发送逻辑属于部署方集成包，不进入核心知识库模块。多租户路由由注入实现依据受控 tenant/subscription 标识处理，不允许通过请求传任意 URL。

### 10.1 一次性切换

本次改造不提供渐进迁移或旧协议适配器：

1. Discovery/Enrich 与资源变更同时切换到 `KnowledgeEventPublisher`；
2. 只读取 `BY_QA_EVENT_PUBLISHER_PROVIDER`；
3. 删除旧 `KnowledgeEntityProcessingCallback`、双方法 Protocol 和 provider loader；
4. 不识别旧 Callback provider 环境变量，不做回退和双写；
5. 部署方必须一次性将实现更换为单方法 `publish(KnowledgeEvent)` Publisher。

这样可以避免长期存在两套协议、配置优先级和重复通知语义。

### 10.2 不统一调度机制

- 资源变更：Route 注册 BackgroundTasks，响应后调用统一 Invoker；
- 异步文件构建：原 BackgroundTasks 内执行构建，终态事务提交后调用统一 Invoker；
- Discovery/Enrich：runner/reaper 在终态提交后直接调用统一 Invoker；
- Discovery/Enrich 不迁移到 BackgroundTasks；
- 统一 Invoker 的超时只限制投递耗时，不改变语义任务状态和批次进度。

## 11. BackgroundTasks 运行边界

FastAPI BackgroundTasks 在响应发送后由当前 API 进程执行，不是独立队列或 worker：

- Publisher 必须使用异步 I/O；
- 不得持有 request-scoped 资源；
- 不得在事件循环中执行阻塞网络或重 CPU 工作；
- 慢 Publisher 仍会消耗 API 进程的连接池、内存和协程调度能力；
- 服务关停宽限期结束后，未完成 publish 可以被取消；
- 同一响应的多个 Background Task 不得互相依赖完成顺序。

## 12. 异常、日志与安全

Publisher 异常不得修改响应、回滚主业务、改变资源状态、改变语义任务终态或触发主业务重试。

建议日志：

- 成功：event_id、event_type、kb_code、elapsed_ms；
- 失败：上述字段、error_type、截断后的错误摘要；
- 超时：上述字段、timeout_seconds。

日志禁止记录：

- 文件内容；
- Callback secret/token；
- 用户自定义 header；
- 业务后端完整响应体。

业务后端仍建议按 eventId 幂等消费，以容忍用户 Publisher 实现自行做瞬时重试。

## 13. 测试设计

### 13.1 Protocol

- 未配置 provider 时加载 Noop；
- 正确加载 module:attribute；
- 不满足 Protocol 时启动失败；
- Invoker 原样传递 KnowledgeEvent；
- Publisher 异常和超时被隔离；
- resource 与 semantic 事件共用同一个 Publisher 实例；
- 旧 provider 配置不再生效；
- resource 与 semantic 事件共用同一 Publisher 实例且不重复发送。

### 13.2 Route

每个目标接口覆盖：

- 主业务成功时注册并执行一次 Background Publish；
- 校验、业务和未预期异常时不调用；
- Publisher 异常时 API 仍保持原成功响应；
- 请求和事件均不包含 extraParams；
- 事件不含 requestId、resourceId 或内部数据库主键；
- 事件不包含文件二进制；
- Publisher 执行时查询资源可见已提交的新状态。

### 13.3 批量场景

- ZIP 全成功只产生一个事件；
- ZIP 部分成功产生一个包含全部 item 结果的事件；
- ZIP 全失败且无变更时不产生事件；
- move 部分成功时事件与 API 响应一致；
- 目录删除和重命名只产生一个根目录事件。

### 13.4 语义事件回归

- Discovery 文件终态映射为 semantic.discovery.file.completed；
- Enrich 文件终态映射为 semantic.enrich.file.completed；
- 两类批次完成分别映射为对应 batch.completed；
- 原有 task、batch、progress、result 和 error 信息无损进入信封与 payload；
- Publisher 异常不改变已经提交的任务和批次状态；
- runner/reaper 不使用 FastAPI BackgroundTasks。

### 13.5 异步文件构建事件回归

- `complete`、`failed`、`unsupported` 每个终态只发布一个 `build.file.completed`；
- 事件发布时 `knowledge_build_task` 终态已经提交；
- Publisher 超时或异常不改变构建结果，不触发重建；
- 事件不包含 `extraParams`、`resourceId` 或 requestId。

## 14. 实施拆分

### 一次性交付内容

- 新增 KnowledgeEvent、KnowledgeEventPublisher、Noop 和统一 Invoker；
- 新增 provider loader 和超时配置；
- 在 knowledge runtime 创建进程级 Invoker；
- 完成基础单元测试。

### 单资源接口

- 为 Route 增加 BackgroundTasks；
- 增加事件构造器；
- 完成 Route 与提交时序测试。

### 批量接口

- 接入 ZIP import 和批量 move；
- 对齐 API 响应与事件 payload.items/payload.result；
- 覆盖部分成功和全部失败。

### 集成文档

- 在 .env.example 增加 provider 和 timeout；
- 增加 Recording EventPublisher 测试实现；
- 增加部署方 provider 示例；
- 验证未配置 provider 时现有 API 回归行为不变。

## 15. 验收标准

1. 资源变更与 Discovery/Enrich 共用一个 KnowledgeEventPublisher、provider 和 Invoker。
2. 七类目标接口在至少一项资源变更成功提交后注册一个 Background Publish。
3. Publisher 只能观察到已提交状态。
4. HTTP 响应不等待 Publisher 结果。
5. Publisher 超时、异常或目标不可用不影响主业务和语义任务终态。
6. 事件不包含文件内容、请求对象或数据库连接。
7. 请求与事件不包含 extraParams；资源接口不落通知表，语义任务沿用现有持久化。
8. 未配置 provider 时使用 Noop，现有 API 行为不变。
9. 旧 KnowledgeEntity Callback Protocol 和 provider 不再存在，只有一套统一发布协议。
10. 进程崩溃、重启和目标不可用导致通知丢失符合预期，不做补偿。

## 16. 后续扩展

首期不包括：

- 通知必达和持久化重试；
- 每请求自定义 Callback URL；
- Callback 订阅与历史查询；
- 失败 HTTP 请求审计；
- 文件元数据变更事件；
- 把资源变更与语义任务的调度机制合并；
- 把不同领域 payload 压缩成一个包含大量可空字段的通用模型。

未来如果通知不再允许丢失，应另行引入 outbox 或可靠消息中间件，不能依靠扩展 FastAPI BackgroundTasks 获得可靠投递保证。
