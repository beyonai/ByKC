# LLMService 工厂化改造设计

**日期：** 2026-04-17
**状态：** 待实现

## 背景

当前 `LLMService` 直接通过 `get_settings()` 读取环境变量来构建模型配置。外部系统（如使用 Nacos/Consul 等配置中心的服务）无法替换配置获取方式，只能依赖环境变量。同样，`EmbeddingQueryService` 也在构建时从 `Settings` 注入配置，无法动态替换。

目标：将模型配置获取方式抽象为可插拔的 `ModelConfigProvider` 接口，放在 `core` 层供所有模块共用。环境变量方式作为内置默认实现，外部系统可注入自定义 provider 统一替换 LLM 和 embedding 的配置来源。

## 范围

覆盖所有模型角色：
- LLM：`classifier` / `retrieval` / `generator` / `quality` / `decomposer` / `aggregator`
- Embedding：`embedding`
- Rerank：暂无 Settings 字段，`EnvModelConfigProvider` 对未知 `model_type` 抛 `ValueError`，后续按需扩展

## 核心接口

新增文件 `src/by_qa/core/model_config.py`：

```python
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

@dataclass
class ModelConfig:
    model_name: str
    temperature: float
    base_url: str
    api_key: str

@runtime_checkable
class ModelConfigProvider(Protocol):
    async def get_config(self, model_type: str) -> ModelConfig:
        ...
```

### EnvModelConfigProvider

内置默认实现，行为与现有逻辑完全一致：

| model_type | model_name | temperature | base_url | api_key |
|---|---|---|---|---|
| classifier | classifier_model | classifier_temp | llm_base_url | llm_api_key |
| retrieval | retrieval_model | retrieval_temp | llm_base_url | llm_api_key |
| generator | generator_model | generator_temp | llm_base_url | llm_api_key |
| quality | quality_model | quality_temp | llm_base_url | llm_api_key |
| decomposer | decomposer_model | decomposer_temp | llm_base_url | llm_api_key |
| aggregator | aggregator_model | aggregator_temp | llm_base_url | llm_api_key |
| embedding | embedding_model_name | 0.0 | embedding_base_url | embedding_api_key |
| 其他 | — | — | — | — | 抛 ValueError |

## LLMService 改造

`__init__` 接受可选 provider，默认使用 `EnvModelConfigProvider`：

```python
from by_qa.core.model_config import ModelConfig, ModelConfigProvider, EnvModelConfigProvider

class LLMService:
    def __init__(self, provider: ModelConfigProvider | None = None):
        self._provider = provider or EnvModelConfigProvider()
```

`_get_model` 改为 async，内部调用 `await self._provider.get_config(model_type)`，不再直接读 `self.settings`。所有调用方（`generate`、`generate_stream`、`bind_tools`、`ainvoke`）已是 async，改动透明。

`get_llm_service()` singleton 保持不变，继续使用环境变量默认行为。

## EmbeddingQueryService 改造

`EmbeddingQueryService.__init__` 接受可选 provider，默认使用 `EnvModelConfigProvider`。`embed_query()` 调用前通过 `await self._provider.get_config("embedding")` 获取 base_url / api_key / model_name，不再依赖构造时注入的静态配置。

`build_knowledge_item_search_service()` 增加可选 `provider: ModelConfigProvider | None = None` 参数，透传给 `EmbeddingQueryService`。

## InstantQAConfig 改造

`InstantQAConfig` 新增 `llm_service: LLMService | None = None` 字段。`build_instant_search_graph` 把它透传给各 agent builder，agent 里优先级为：

```
model 参数 > llm_factory > llm_service > get_llm_service()
```

## 文件变更

| 文件 | 操作 |
|---|---|
| `src/by_qa/core/model_config.py` | 新增：ModelConfig + ModelConfigProvider + EnvModelConfigProvider |
| `src/by_qa/core/__init__.py` | 修改：导出新增类型 |
| `src/by_qa/qa/services/llm_service.py` | 修改：__init__ 接受 provider，_get_model 改为 async |
| `src/by_qa/qa/instant/config.py` | 修改：InstantQAConfig 新增 llm_service 字段 |
| `src/by_qa/qa/instant/graphs/main.py` 及各 agent builder | 修改：透传 llm_service，更新优先级逻辑 |
| `src/by_qa/knowledge_base/services/embedding_query_service.py` | 修改：__init__ 接受 provider，embed_query 动态获取配置 |
| `src/by_qa/knowledge_base/infrastructure/runtime.py` | 修改：build_knowledge_item_search_service 增加 provider 参数 |
| `src/by_qa/config.py` | 不改动 |
| 所有直接调用 get_llm_service() 的 agent 文件 | 不改动 |

## 外部系统使用示例

```python
from by_qa.core.model_config import ModelConfig, ModelConfigProvider
from by_qa.qa.services.llm_service import LLMService
from by_qa.qa.instant.config import InstantQAConfig, InstantQARetrievalConfig
from by_qa.qa.instant.engine import create_instant_search_agent
from by_qa.knowledge_base.infrastructure.runtime import build_knowledge_item_search_service

class NacosModelConfigProvider:
    async def get_config(self, model_type: str) -> ModelConfig:
        config = await nacos_client.get(f"llm/{model_type}")
        return ModelConfig(
            model_name=config["model"],
            temperature=config["temperature"],
            base_url=config["base_url"],
            api_key=config["api_key"],
        )

provider = NacosModelConfigProvider()

# QA 调用
llm_service = LLMService(provider=provider)
agent = create_instant_search_agent(
    InstantQAConfig(
        llm_service=llm_service,
        retrieval=InstantQARetrievalConfig(...)
    )
)

# Knowledge base embedding
search_service = build_knowledge_item_search_service(settings, ..., provider=provider)
```
