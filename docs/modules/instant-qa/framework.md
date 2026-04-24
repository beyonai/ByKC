# 即时问答模块框架文档

## 模块目标

`by_qa` 的问答域模块按子模块组织，当前开源的是 `qa.instant`，用于提供即时问答能力。

当前范围：

- 保留即时问答核心引擎
- 保留多跳/单跳编排、节点、运行时和流式事件模型
- 保留代码级能力入口

当前不包含：

- 深度问答 `qa.deep`
- HTTP/SSE 对外接口
- worker 适配层

## 目录结构

```text
src/by_qa/qa/
├── __init__.py
├── common/
│   ├── config.py
│   ├── context.py
│   ├── context_manager.py
│   ├── exceptions.py
│   ├── models.py
│   ├── operation_registry.py
│   └── reducers.py
├── agents/
│   ├── answer_synthesizer.py
│   ├── query_decomposer.py
│   ├── standalone_question_rewriter.py
│   └── subanswer_aggregator.py
├── services/
│   ├── checkpointer_factory.py
│   └── llm_service.py
├── tools/
│   └── knowledge_tools.py
├── fast/
│   ├── engine.py
│   ├── graph.py
│   ├── nodes/
│   ├── state.py
│   └── types.py
└── instant/
    ├── engine.py
    ├── state.py
    ├── types.py
    ├── agents/
    ├── graphs/
    ├── nodes/
    └── runtime/
```

相关文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [process.md](./process.md)

## 分层职责

### `qa.common`

承载问答域共享的配置、运行时上下文、检索上下文管理、异常、输入输出模型和流式事件模型。

### `qa.agents`

承载问答域可复用的上层 LLM 助手，例如查询分解、独立问题改写、检索上下文回答合成和子答案聚合。

### `qa.services`

承载问答域运行所需的基础服务，包括 LLM 实例化和 LangGraph checkpointer 工厂。

### `qa.tools`

承载问答域工具构建与工具调用中间件，例如知识库工具集构建。

### `qa.fast`

承载快速问答能力，使用线性 LangGraph 完成问题改写、一次知识库检索和回答合成。

### `qa.instant`

承载即时问答核心能力：

- engine
- graph builder
- node
- runtime context
- retrieval adapter

## 安装方式

即时问答通过额外依赖安装：

```bash
pip install by-qa[qa]
```

安装全部模块：

```bash
pip install by-qa[all]
```
