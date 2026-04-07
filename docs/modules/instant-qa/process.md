# 即时问答处理流程

## 总流程

```mermaid
flowchart TD
    A["CoreInput"] --> B["InstantQAEngine.stream_search"]
    B --> C["构造 InstantSearchState"]
    C --> D["decomposer"]
    D --> E["router"]
    E -->|单子查询| F["single_hop_worker / multi_hop_worker"]
    E -->|多子查询| G["按子查询并行分发 worker"]
    F --> H["final_answer"]
    G --> I["subanswer_aggregator"]
    I --> J["final_answer"]
    H --> K["StreamEvent 输出"]
    J --> K
```

说明：

- 入口是 `InstantQAEngine.stream_search()`
- 先分解问题，再决定走单 worker 还是并行 worker
- 单子查询直接出最终答案，多子查询先聚合再出最终答案
- LangGraph 内部事件最终会被转换成统一的 `StreamEvent`

## 分解与路由

```mermaid
flowchart TD
    A["original_query + conversation_history"] --> B["QueryDecomposerAgent"]
    B --> C["sub_queries"]
    C --> D{"子查询数量"}
    D -->|1 个| E{"query_type"}
    D -->|多个| F["subgraph_parallel_path"]
    E -->|single-hop| G["single_hop_worker"]
    E -->|multi-hop| H["multi_hop_worker"]
```

说明：

- `decomposer` 会输出 `sub_queries`、`hop_count`、`query_type`
- `router` 先按“子查询数量”分流
- 单个子查询时，再由 worker 内部处理 single-hop 或 multi-hop

## 单跳流程

```mermaid
flowchart TD
    A["single_hop_worker"] --> B["single_hop_react agent"]
    B --> C["parallel_retrieval"]
    C --> D["runtime.retrieval.search_knowledge_items"]
    D --> E["知识库搜索 API"]
    E --> F["检索结果"]
    F --> G["ToolMessage + artifact"]
    G --> B
    B --> H["final_answer_from_messages_node"]
    H --> I["final_answer"]
```

说明：

- 单跳问题通过 ReAct agent 驱动检索
- `parallel_retrieval` 负责调用知识库搜索接口
- 证据足够后，agent 直接生成答案

## 多跳流程

```mermaid
flowchart TD
    A["multi_hop_worker"] --> B["multi_hop_react agent"]
    B --> C["parallel_retrieval"]
    C --> D["知识库搜索 API"]
    D --> E["当前跳检索结果"]
    E --> B
    B -->|进入下一跳| F["next_hop"]
    F --> G["清理当前跳上下文 + current_step++"]
    G --> B
    B -->|结束| H["finalize"]
    H --> I["多跳总结节点"]
    I --> J["sub_answer"]
```

说明：

- 多跳问题会在同一子图里多次检索
- `next_hop` 用来推进步骤，不直接结束问题
- `finalize` 结束多跳推理，并产出该子查询的 `sub_answer`

## 上下文裁剪

```mermaid
flowchart TD
    A["retrieval_results"] --> B["按 sub_query_id / source_type 分组"]
    B --> C["Round-Robin 选取结果"]
    C --> D["按句边界截断"]
    D --> E["build_context_for_llm"]
```

说明：

- 使用 `CONTEXT_MAX_TOKENS`
- 使用 `INSTANT_SEARCH_MAX_CONTEXT_RATIO`
- 使用 `INSTANT_SEARCH_RESERVED_TOKENS`
- 使用 `INSTANT_SEARCH_MIN_SENTENCE_TOKENS`

## 多子查询聚合

```mermaid
flowchart TD
    A["多个 worker 产出的 sub_answers"] --> B["SubAnswerAggregatorAgent"]
    B --> C["final_answer"]
```

说明：

- 多子查询场景不会直接结束
- 会先聚合 `sub_answers`
- 聚合后再写入 `final_answer`

## 流式输出

```mermaid
flowchart TD
    A["LangGraph events"] --> B["InstantQAEngine"]
    B --> C["EventFilter"]
    C --> D["node_start / node_end"]
    C --> E["token"]
    C --> F["search_result_chunks"]
    C --> G["answer"]
```

说明：

- 对外暴露的是统一的 `StreamEvent`
- 检索结果优先从 `ToolMessage.artifact` 提取
- 不直接暴露 LangGraph 原始事件结构
