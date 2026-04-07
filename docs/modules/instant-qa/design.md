# 即时问答模块设计文档

## 设计目标

即时问答模块在当前开源仓库中的目标是保留源项目中已经验证过的即时问答核心编排能力，同时把它从旧主程序、旧 adapter 和深度问答实现中拆出来。

设计原则：

- 问答域按 `qa.instant` 和 `qa.deep` 分层
- 当前只迁移 `qa.instant`
- 深度问答保留为未来重设计的预留子模块
- 即时问答保持代码级能力入口，不先固定对外 HTTP 协议

相关文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [process.md](./process.md)

## 编排设计

即时问答沿用源项目的 capability-local 结构：

- `graphs/` 负责组装 LangGraph
- `nodes/` 负责分解、路由、上下文管理和最终聚合
- `agents/` 负责单跳、多跳和分解相关的 agent 组装
- `runtime/` 负责运行时上下文、hooks 和检索适配
- `engine.py` 负责统一入口和流式事件转换

这样迁移后，问答链路仍然保留：

1. 查询分解
2. 路由单跳或多跳处理
3. 调用知识库检索
4. 子答案聚合
5. 产出最终回答和流式事件

## 检索设计

当前即时问答不再依赖旧项目里的历史检索入口，而是统一走当前开源仓库可用的知识库搜索接口。

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

## 当前限制

这次迁移刻意没有包含：

- `qa.deep`
- 对外 Web API
- worker 网关接入
- 旧 openGauss checkpointer 适配

这些能力后续如果需要恢复，会以问答域下的独立子设计继续演进，而不会重新把即时问答绑回旧架构。
