# 知识库模块设计文档

## 设计目标

知识库模块采用“对象存储保存文件内容，openGauss 保存结构化元数据与检索索引”的双存储方案。

相关文档：

- [framework.md](/Users/jialangli/code/workspace/by-qa/docs/modules/knowledge-base/framework.md)
- [design.md](/Users/jialangli/code/workspace/by-qa/docs/modules/knowledge-base/design.md)
- [api.md](/Users/jialangli/code/workspace/by-qa/docs/modules/knowledge-base/api.md)

设计目标如下：

- 支持知识库级和文档级的业务管理
- 支持文档版本化与当前版本切换
- 支持 chunk 级文本和向量检索
- 支持原始文件和 Markdown sidecar 的分桶存储
- 为后续 RAG/Agent 调用提供稳定的技术接口

## 核心模型

模块围绕以下核心对象工作：

- `knowledge_base`：知识库主实体
- `knowledge_fs_entry`：虚拟文件树节点
- `knowledge_item`：文档主实体
- `knowledge_item_version`：文档版本实体
- `knowledge_item_chunk`：chunk 实体
- `knowledge_item_chunk_retrieval_mv`：当前版本检索投影

设计上采用“文件树身份”和“文档业务元数据”分离：

- 文件路径与目录层级由 `knowledge_fs_entry` 表达
- 文档业务属性由 `knowledge_item` 表达
- 具体版本内容由 `knowledge_item_version` 表达

## 存储设计

### 对象存储

对象存储由 `KnowledgeBaseObjectStorage` 负责，分别管理：

- 原始内容 bucket
- Markdown sidecar bucket

对象键生成遵循：

- 临时对象先写入 `tmp/`
- 导入事务成功后晋升为正式对象键
- 正式对象按知识库、路径、版本组织

这样可以保证导入失败时不留下半完成对象。

### 数据库存储

openGauss 保存以下信息：

- 知识库元数据
- 文件树结构
- 文档版本状态
- chunk 内容与位置信息
- 当前版本检索投影
- embedding 对应的动态向量表

检索时只面向“当前版本”工作，避免历史版本直接进入主召回链路。

## 导入链路设计

文档导入由 `KnowledgeItemIngestionService` 负责，主流程如下：

1. 校验知识库和文档请求参数
2. 将原始文件与 Markdown 内容写入临时对象
3. 写入文档主记录、版本记录和 chunk 记录
4. 写入当前 embedding 模型对应的向量表
5. 刷新检索投影
6. 将临时对象晋升为正式对象

该流程要求以事务方式完成，确保对象存储与数据库状态一致。

## 检索链路设计

检索由 `KnowledgeItemSearchService` 负责，采用“数据库召回 + 服务层融合排序”的设计。

主要步骤如下：

1. 接收原始查询文本和过滤条件
2. 服务端生成 query embedding
3. 执行文本召回
4. 执行向量召回
5. 在服务层做分数融合、去重和截断
6. 返回 chunk 级结果列表

当前接口返回的是偏技术侧结果，适合直接作为上层 RAG 上下文候选。

## API 设计原则

知识库 API 以稳定、明确为原则：

- 成功响应统一返回 `code/message/data`
- 失败响应统一返回 `code/message/error`
- 参数错误、业务校验错误、配置错误和内部错误分开表达
- 检索返回以 chunk 为中心，不做文档级聚合

## 配置设计

配置集中在 `src/by_qa/config.py`，只保留知识库模块实际需要的运行时参数：

- Web 服务配置
- openGauss 配置
- MinIO 配置
- embedding 配置
- 缓存与清理配置

配置通过环境变量注入，便于本地开发、容器部署和 CI 环境统一管理。

## 基础设施设计

本模块保留一套完整的本地基础设施栈：

- `docker-compose.kb-stack.yml`
- `docker/opengauss/`
- `docker/minio/`
- `sql/knowledge_base/`
- `scripts/reset_kb_stack.sh`
- `scripts/verify_kb_stack.sh`

目标是让知识库模块能以仓库自身的资源完成本地开发和集成验证，而不依赖源项目其它模块。
