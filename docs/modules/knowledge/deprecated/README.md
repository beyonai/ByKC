# 已废弃文档归档

> 本目录中的文档均已失效或退出当前实现范围，只用于追溯历史设计和决策，不得作为开发、联调或验收依据。

当前有效的知识模块接口契约统一从 [API 文档导航](../api/README.md) 进入。

## 归档清单

| 文档 | 废弃原因 | 当前替代文档 |
| --- | --- | --- |
| [api.md](api.md) | 历史聚合接口文档，已拆分为逐接口文档 | [API 文档导航](../api/README.md) |
| [metadata_api.md](metadata_api.md) | 历史元数据与检索聚合文档，内容不再单独维护 | [元数据与 Agent DSL](../api/metadata-and-dsl.md) |
| [knowledge-entity-api.md](knowledge-entity-api.md) | 历史 KnowledgeEntity 聚合接口设计，接口契约已拆分 | [API 文档导航](../api/README.md)、[异步处理约定](../api/entity-processing.md) |
| [metadata_business_api_deprecated.md](metadata_business_api_deprecated.md) | Business DSL 候选接口未进入当前实现 | [元数据与 Agent DSL](../api/metadata-and-dsl.md) |
| [refactor-cleanup.md](refactor-cleanup.md) | 基于早期接口范围的阶段性清理清单，已不代表当前代码 | [API 文档导航](../api/README.md) |

## 使用约束

- 不在本目录中新增当前接口或实现说明。
- 不从现行文档链接本目录作为有效规范；如需引用历史决策，必须明确标注“历史”或“已废弃”。
- 归档文档不随当前接口实现同步更新。
