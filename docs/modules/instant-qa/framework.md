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
│   ├── exceptions.py
│   ├── models.py
│   └── reducers.py
├── agents/
│   ├── query_decomposer.py
│   ├── result_aggregator.py
│   └── subanswer_aggregator.py
├── services/
│   ├── checkpointer_factory.py
│   └── llm_service.py
└── instant/
    ├── engine.py
    ├── config.py
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

## 分层职责

### `qa.common`

承载问答域共享的异常、输入输出模型和流式事件模型。

### `qa.agents`

承载即时问答仍然依赖的上层 LLM 助手，例如查询分解、子答案聚合和结果聚合。

### `qa.services`

承载问答域运行所需的基础服务，包括 LLM 实例化和 LangGraph checkpointer 工厂。

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
