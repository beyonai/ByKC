# 即时问答模块设计文档

## 设计目标

即时问答模块的目标是提供一个独立、清晰、可扩展的即时问答编排能力层，用于承接查询分解、检索调度、子答案聚合和最终回答输出。

设计原则：

- 当前实现聚焦 `qa.instant`
- 即时问答保持代码级能力入口，不对外 HTTP 协议

相关文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [process.md](./process.md)

## 编排设计

即时问答采用 capability-local 结构：

- `graphs/` 负责组装 LangGraph
- `nodes/` 负责分解、路由、上下文管理和最终聚合
- `agents/` 负责单跳、多跳和分解相关的 agent 组装
- `runtime/` 负责运行时上下文、hooks 和检索适配
- `engine.py` 负责统一入口和流式事件转换

当前问答链路包括：

1. 查询分解
2. 路由单跳或多跳处理
3. 调用知识库检索
4. 子答案聚合
5. 产出最终回答和流式事件

## 检索设计

当前即时问答统一走当前仓库可用的知识库搜索接口。

具体做法：

- `qa.instant.runtime.retrieval` 仍然使用 HTTP 调用知识库搜索 API
- 运行时通过 `knowledge_bases` 配置多个知识库端点
- 检索结果被规范化成问答 agent 可消费的统一结构

这样做的好处是：

- 问答模块与知识库模块保持解耦
- 不要求问答直接导入知识库内部服务
- 以后可以替换知识库服务部署方式而不改问答主编排

## 依赖设计

包安装采用模块化 extras：

- `by-qa[knowledge]`
- `by-qa[qa]`
- `by-qa[all]`

其中：

- `knowledge` 依赖知识库服务运行所需库
- `qa` 依赖 LangChain、LangGraph 和即时问答运行所需库
- 默认安装不承诺任一能力模块可直接运行

## StreamEvent 设计

即时问答的流式输出统一使用 `qa.common.models.StreamEvent`。这个模型既是引擎对外暴露的事件协议，也是 CLI、上层服务或前端消费流式结果时的统一数据结构。

### 核心字段

`StreamEvent` 包含以下核心字段：

- `type`：事件类型，对应 `StreamEventType`
- `data`：事件负载，具体结构随事件类型变化
- `timestamp`：事件产生时间
- `role`：当前事件所属节点、agent 或执行角色
- `parent_ids`：父运行链路 ID，用于表示事件层级
- `instance_id`：当前运行实例 ID
- `sub_query_id`：子问题 ID
- `query_type`：查询类型，例如单跳或多跳
- `hop_number`：多跳链路中的 hop 序号
- `routing_path`：路由决策结果

其中：

- `type`、`data`、`timestamp` 是最基础的通用字段
- 其余字段主要用于节点关联、可视化和调试追踪

### 事件类型

当前 `StreamEventType` 定义了以下事件类型：

- `node_start`
- `node_end`
- `token`
- `tool_call`
- `tool_response`
- `answer`
- `done`
- `error`
- `search_result_chunks`
- `decomposition_complete`
- `routing_decision`
- `subgraph_start`
- `subgraph_end`
- `sub_answer_generated`
- `hop_start`
- `hop_end`
- `intermediate_answer`

这些事件大致可以分成几类：

- 执行过程事件：`node_start`、`node_end`、`subgraph_start`、`subgraph_end`、`hop_start`、`hop_end`
- 模型输出事件：`token`、`answer`、`intermediate_answer`
- 工具与检索事件：`tool_call`、`tool_response`、`search_result_chunks`
- 编排决策事件：`decomposition_complete`、`routing_decision`、`sub_answer_generated`
- 收尾事件：`done`、`error`

### 常用事件负载约定

虽然 `data` 是开放结构，但当前实现里有一些相对稳定的约定：

- `token`：`data.content` 表示单次 token 或文本片段
- `answer`：`data.content` 表示最终答案，`data.citations` 表示引用信息
- `search_result_chunks`：`data.chunks` 表示检索命中的 chunk 列表
- `done`：`data.sessionId` 表示会话 ID
- `error`：`data.error` 或其他错误上下文字段表示异常信息

### 设计意图

`StreamEvent` 采用统一事件模型有几个目的：

- 让图编排内部事件可以被外部消费，而不直接暴露 LangGraph 原始事件
- 让不同输出端统一消费同一种结构，例如 CLI、HTTP/SSE 和前端 UI
- 保留足够多的运行时上下文字段，便于定位问题和做可视化追踪
- 在不破坏基础协议的前提下，允许未来新增事件类型或补充 `data` 字段
