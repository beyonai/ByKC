# 知识模块 API 文档

本目录是知识模块 HTTP API 的统一入口。当前共有 **31 个逻辑接口**；其中 6 个接口额外注册了 `knowledge-items` 兼容路径，因此 FastAPI 中共有 37 个路径（包含 `/health`）。

## 公共文档

- [通用约定](common.md)：Base URL、请求格式、响应信封、HTTP 状态码和兼容路径。
- [元数据与 Agent DSL](metadata-and-dsl.md)：元数据类型、系统字段、过滤表达式和检索模式。
- [异步实体处理](entity-processing.md)：batch/task、状态、Worker、超时和 Callback 公共语义。

## 接口导航

### 基础能力

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `GET` | `/health` | [health](interfaces/health.md) |
| `POST` | `/api/v1/fileToMarkdown` | [fileToMarkdown](interfaces/fileToMarkdown.md) |

### 知识库管理

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeBases/create` | [knowledgeBases-create](interfaces/knowledgeBases-create.md) |
| `POST` | `/api/v1/knowledgeBases/update` | [knowledgeBases-update](interfaces/knowledgeBases-update.md) |
| `POST` | `/api/v1/knowledgeBases/delete` | [knowledgeBases-delete](interfaces/knowledgeBases-delete.md) |

### 目录管理

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/directories/create` | [directories-create](interfaces/directories-create.md) |
| `POST` | `/api/v1/directories/update` | [directories-update](interfaces/directories-update.md) |
| `POST` | `/api/v1/directories/delete` | [directories-delete](interfaces/directories-delete.md) |

### 文档管理

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/import` | [knowledgeItems-import](interfaces/knowledgeItems-import.md) |
| `POST` | `/api/v1/knowledgeItems/update` | [knowledgeItems-update](interfaces/knowledgeItems-update.md) |
| `POST` | `/api/v1/knowledgeItems/delete` | [knowledgeItems-delete](interfaces/knowledgeItems-delete.md) |
| `POST` | `/api/v1/knowledgeItems/move` | [knowledgeItems-move](interfaces/knowledgeItems-move.md) |
| `POST` | `/api/v1/knowledgeItems/references` | [knowledgeItems-references](interfaces/knowledgeItems-references.md) |

### 目录与文件读取

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/listDir` | [listDir](interfaces/listDir.md) |
| `POST` | `/api/v1/glob` | [glob](interfaces/glob.md) |
| `POST` | `/api/v1/readFile` | [readFile](interfaces/readFile.md) |
| `POST` | `/api/v1/downloadFile` | [downloadFile](interfaces/downloadFile.md) |

### 知识构建

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/fileToMarkdownIndex` | [fileToMarkdownIndex](interfaces/fileToMarkdownIndex.md) |
| `POST` | `/api/v1/fileBuildStatus` | [fileBuildStatus](interfaces/fileBuildStatus.md) |
| `POST` | `/api/v1/buildResult` | [buildResult](interfaces/buildResult.md) |

### 检索与元数据

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/search` | [knowledgeItems-search](interfaces/knowledgeItems-search.md) |
| `POST` | `/api/v1/knowledgeItems/searchFile` | [knowledgeItems-searchFile](interfaces/knowledgeItems-searchFile.md) |
| `POST` | `/api/v1/knowledgeItems/metadataSearch` | [knowledgeItems-metadataSearch](interfaces/knowledgeItems-metadataSearch.md) |
| `POST` | `/api/v1/knowledgeItems/metadata/update` | [knowledgeItems-metadata-update](interfaces/knowledgeItems-metadata-update.md) |
| `POST` | `/api/v1/knowledgeItems/metadata/get` | [knowledgeItems-metadata-get](interfaces/knowledgeItems-metadata-get.md) |

### KnowledgeEntity

| 方法 | 路径 | 文档 |
| --- | --- | --- |
| `POST` | `/api/v1/knowledgeItems/processingEligibility` | [processingEligibility](interfaces/processingEligibility.md) |
| `POST` | `/api/v1/knowledgeItems/entityDiscovery` | [entityDiscovery](interfaces/entityDiscovery.md) |
| `POST` | `/api/v1/knowledgeItems/entityEnrich` | [entityEnrich](interfaces/entityEnrich.md) |
| `POST` | `/api/v1/knowledgeItems/processingTaskStatus` | [processingTaskStatus](interfaces/processingTaskStatus.md) |
| `POST` | `/api/v1/knowledgeItems/processingBatchStatus` | [processingBatchStatus](interfaces/processingBatchStatus.md) |
| `POST` | `/api/v1/knowledgeItems/semanticRelations` | [semanticRelations](interfaces/semanticRelations.md) |

## 文档维护规则

- 每个逻辑接口只在 `interfaces/` 中维护一份主文档。
- 每份接口文档必须包含独立的“功能描述”，明确接口用途、处理对象以及同步或异步语义。
- 兼容路径写在同一份接口文档中，不重复建文件。
- 通用响应、DSL 和异步任务语义由公共文档管理；路径规则直接写在每个相关接口文档中。
- 增删路由时同步更新本导航和对应接口文档。
