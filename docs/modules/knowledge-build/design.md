# 知识构建模块设计文档

## 设计目标

知识构建模块在当前开源仓库中的目标不是提供最终生产方案，而是提供一套可运行、可联调、可参考的示例实现。

设计原则：

- `knowledge_build` 与 `knowledge_base` 并列
- `knowledge_base` 不负责知识构建
- 构建相关代码全部收敛在 `knowledge_build`
- 公共类型和异常只保留极少共享层

相关文档：

- [api.md](./api.md)
- [framework.md](./framework.md)
- [design.md](./design.md)
- [process.md](./process.md)

## 接口设计

当前模块提供 3 个接口：

- `file-to-markdown`
- `build-markdown-index`
- `file-to-markdown-index`

它们分别覆盖：

1. 文件解析为 markdown
2. markdown 切片并生成 embedding
3. 文件解析后直接输出 markdown 与 chunks

这 3 个接口的目标更偏向构建前处理示例，而不是完整知识导入闭环。

## 运行时设计

知识构建模块自己的运行时只校验 embedding 相关配置：

- `EMBEDDING_MODEL_NAME`
- `EMBEDDING_BASE_URL`
- `EMBEDDING_DIMENSION`

它不要求：

- openGauss
- MinIO
- 知识库表结构

这样可以保证 `knowledge_build` 独立安装和独立启用。

## 构建设计

当前示例实现的处理链路是：

1. 文件解析
2. 文本切片
3. 行号和字符范围补全
4. 批量 embedding
5. 输出 chunk payload

支持的输入文件类型包括：

- `txt`
- `md`
- `csv`
- `pdf`
- `docx`
- `pptx`
- `xlsx`

其中 `markdown` 请求类型会在服务端统一映射为 `md`。

markdown 和纯文本切片统一由 `DocumentChunkingService` 完成。

## 生产限制

当前实现存在明确边界：

- 文档清洗策略较轻
- 表格和复杂排版解析能力有限
- 切片策略是通用型默认策略
- embedding 构建只走统一兼容接口
- 没有做针对不同行业文档的定制化优化

因此它更适合作为：

- 接口协议样例
- 最小可运行构建链路
- 与 `knowledge_base` 联调的参考实现

如果要追求生产检索效果，通常需要额外考虑：

- 更强的文档解析能力
- 面向业务语义的 chunking 策略
- 元数据补全
- 多模态或结构化抽取
- 更适合场景的 embedding 模型

## 推荐生产架构

更推荐的生产路径是：

1. 接入第三方知识构建平台或外部构建流水线
2. 完成解析、清洗、切片、embedding 和元数据增强
3. 将产出的 markdown 和 chunk 结果导入 `knowledge_base`

也就是说，`knowledge_build` 更适合作为开源参考实现，而 `knowledge_base` 更适合作为稳定的数据管理和检索承载层。
