# 知识库模块 MinIO 设计文档

## 背景

知识库模块采用 “openGauss 管结构化元数据，MinIO 管文件内容” 的双存储架构。数据库负责维护知识库、文件树、文档版本、chunk 与检索投影；MinIO 负责保存原始文件内容和 Markdown sidecar。

相关文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [api.md](./api.md)

## 设计目标

- 将原始文件与 Markdown sidecar 从数据库中解耦，避免大文本直接落表
- 为文档版本化提供稳定的对象键组织方式
- 让导入事务具备“先写临时对象，成功后再晋升”的可恢复能力
- 为读取接口提供原文件直链访问和 Markdown 内容下载来源
- 与本地缓存索引协同，降低重复下载成本

## 存储职责边界

MinIO 只负责对象内容本身，不承担知识库结构化状态管理。

它保存的内容包括：

- 原始文件对象
- Markdown sidecar 对象
- 导入中的临时对象

它不保存的内容包括：

- 知识库主数据
- 文件树层级关系
- 当前版本指针
- chunk 与 embedding
- 本地缓存生命周期

这些状态统一由 openGauss 维护，再通过表字段反向指向对应的 bucket 和 object key。

## Bucket 设计

当前实现固定使用两个业务 bucket：

- 原始文件 bucket：默认 `knowledge-base`
- Markdown bucket：默认 `knowledge-base-markdown`

对应配置项位于 `src/by_qa/config.py`：

- `KB_MINIO_BUCKET`
- `KB_MINIO_MARKDOWN_BUCKET`
- `KB_MINIO_ENDPOINT`
- `KB_MINIO_ACCESS_KEY`
- `KB_MINIO_SECRET_KEY`
- `KB_MINIO_SECURE`

分桶的原因如下：

- 原始文件和 Markdown sidecar 的访问模式不同
- Markdown 更适合被读取接口与本地缓存频繁拉取
- 分桶后可独立配置配额、生命周期和运维策略
- 避免不同内容类型在对象管理和排障时混淆

## Object Key 设计

### 临时对象键

导入阶段先写临时对象，键格式为：

```text
tmp/{import_request_id}/content.md
```

说明：

- 临时对象按一次导入请求隔离
- 当前实现原始文件与 Markdown sidecar 都复用这一模式，但分别写入各自 bucket
- 临时对象不作为稳定引用返回给调用方

### 原始文件对象键

正式原始文件对象键格式为：

```text
{knowledge_base_id}/{full_path}/{version}/{file_name}
```

示例：

```text
12/hr/policy/leave.md/v2026-04-07/leave.md
```

设计意图：

- 以 `knowledge_base_id` 作为逻辑租户边界
- 以 `full_path` 保留业务目录语义
- 以 `version` 显式区分不同文档版本
- 以最终文件名保留对象可读性和排障便利性

### Markdown 对象键

正式 Markdown 对象键格式为：

```text
{knowledge_base_id}/{full_path}/{version}/{stem}.md
```

示例：

```text
12/hr/policy/leave.md/v2026-04-07/leave.md
```

若原始文件不是 Markdown，sidecar 仍会统一落为 `.md` 文件，便于后续 chunk 读取与文本缓存处理。

## 导入链路设计

MinIO 在导入链路中的职责由 `src/by_qa/knowledge_base/infrastructure/object_storage.py` 封装。

主流程如下：

1. 服务层生成 `import_request_id`
2. 原始文件上传到原始 bucket 的 `tmp/` 前缀
3. Markdown sidecar 上传到 Markdown bucket 的 `tmp/` 前缀
4. 数据库事务内写入 `knowledge_item`、`knowledge_item_version`、`knowledge_item_chunk` 等记录
5. 事务成功后，将临时对象 copy 到正式 object key
6. copy 成功后删除对应临时对象

该设计的关键点：

- 数据库提交前，不暴露正式对象键
- 避免导入失败时留下看似可用但未入库的正式对象
- 正式对象键一旦写入 `knowledge_item_version`，即可作为后续读取依据

## 读取链路设计

读取接口存在两种主要模式：

- 原始文件读取：返回 MinIO 预签名访问 URL
- Markdown 读取：优先走本地缓存，缓存未命中时从 MinIO 下载

### 原始文件读取

原始文件由版本记录中的 `bucket_name` 与 `object_key` 定位，服务端通过 MinIO 生成短期预签名 URL 返回给调用方。这样可以避免服务端中转大文件流量。

### Markdown 读取

Markdown 不直接返回 MinIO URL，而是优先下载到本地缓存目录后再按全文或行窗口读取。

原因如下：

- `read-file` 支持按行区间读取，需要服务端可控地切片
- Markdown 内容会被重复读取，适合做本地文件缓存
- 本地缓存可以减少同一版本的重复对象下载

## MinIO 与本地缓存协同

MinIO 保存的是远端对象，真正的读取热点则落在本地缓存目录 `agent_data/kb_cache`。

二者通过数据库表 `knowledge_fetch_cache_index` 协同：

- 表中保存 Markdown 对象所在的 `bucket_name`、`object_key`
- 表中保存下载后的 `cache_file_path`
- 表中保存 `checksum`、`expires_at` 与 `cache_status`

读取时的规则如下：

1. 若缓存索引存在，且校验和一致、未过期、本地文件仍存在，则直接复用本地文件
2. 否则重新从 MinIO 下载 Markdown 对象
3. 下载完成后覆盖本地文件，并刷新缓存索引
4. 后台清理任务周期性删除已过期缓存文件和对应索引

因此，MinIO 是内容权威来源，本地缓存只是性能优化层。

## 一致性设计

### 导入一致性

- 先写临时对象，后写数据库，再晋升正式对象
- 导入事务失败时，不应将临时对象视为业务可见对象
- 正式对象键只应来自已成功入库的版本记录

### 读取一致性

- 缓存命中前需要校验 `checksum`
- 若缓存文件丢失或过期，即使索引仍在，也必须重新从 MinIO 下载
- 缓存层不改变版本归属，版本切换仍以数据库 `current_version_id` 为准

### 删除一致性

当前知识库模块以数据库软删除为主，MinIO 对象删除应视为派生清理动作，不应反向驱动业务状态变更。也就是说，业务可见性先由数据库决定，对象存储清理由后台任务或后续治理策略跟进。

## 运维与配置约束

- 本地开发通过 `docker-compose.kb-stack.yml` 拉起 openGauss 与 MinIO
- MinIO bucket 名称应通过环境变量注入，避免写死到业务逻辑
- 若需要区分环境，建议通过不同 endpoint、access key 或 bucket 前缀隔离
- 读取链路对 Markdown bucket 的可用性更敏感，因为它影响 `read-file` 与上下文抽取
- 原始文件 bucket 更偏向下载与回溯用途，访问频率通常低于 Markdown bucket

## 风险与权衡

- 采用双 bucket 设计会增加配置项，但能换来更清晰的职责边界
- 采用本地缓存会引入额外状态管理，因此需要 `knowledge_fetch_cache_index` 和清理线程配合
- 预签名 URL 适合原始文件直出，但不适合需要服务端按行裁切的 Markdown 读取
- 当前对象键中直接包含 `full_path`，排障更直观，但路径重命名时需要依赖新版本重写对象
