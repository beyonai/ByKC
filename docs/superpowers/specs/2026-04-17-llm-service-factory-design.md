# LLMService 工厂化改造设计

**日期：** 2026-04-17
**状态：** 待实现

## 背景

当前 `LLMService` 直接通过 `get_settings()` 读取环境变量来构建模型配置。外部系统（如使用 Nacos/Consul 等配置中心的服务）无法替换配置获取方式，只能依赖环境变量。

目标：将模型配置获取方式抽象为可插拔的 `ModelConfigProvider` 接口，环境变量方式作为内置默认实现，外部系统可注入自定义 provider。

## 范围

覆盖所有模型角色：LLM（classifier、retrieval、generator、quality、decomposer、aggregator）以及 embedding。rerank 暂无 Settings 字段，provider 对未知 model_type 抛 `ValueError`，后续按需扩展。

## 核心接口

### ModelConfig

单个模型的完整配置，放在 `src/by_qa/qa/common/model_config.py`：

```python
from dataclasses import dataclass

@dataclass
class ModelConfig:
    model_name: str
    temperature: float
    base_url: str
    api_key: str
```

### ModelConfigProvider

外部系统实现此 Protocol：

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ModelConfigProvider(Protocol):
    async def get_config(self, model_type: str) -> ModelConfig:
        ...
```

`model_type` 合法值：`"classifier"` / `"retrieval"` / `"generator"` / `"quality"` / `"decomposer"` / `"aggregator"` / `"embedding"`。未知值抛 `ValueError`。

### EnvModelConfigProvider

内置默认实现，行为与现有 `LLMService._get_model` 完全一致：

| model_type | model_name | temperature | base_url | api_key |
|---|---|---|---|---|
| classifier | classifier_model | classifier_temp | llm_base_url | llm_api_key |
| retrieval | retrieval_model | retrieval_temp | llm_base_url | llm_api_key |
| generator | generator_model | generator_temp | llm_base_url | llm_api_key |
| quality | quality_model | quality_temp | llm_base_url | llm_api_key |
| decomposer | decomposer_model | decomposer_temp | llm_base_url | llm_api_key |
| aggregator | aggregator_model | aggregator_temp | llm_base_url | llm_api_key |
| embedding | embedding_model_name | 0.0 | embedding_base_url | embedding_api_key |

## LLMService 改造

`__init__` 接受可选 provider，默认使用 `EnvModelConfigProvider`：

```python
class LLMService:
    def __init__(self, provider: ModelConfigProvider | None = None):
        self._provider = provider or EnvModelConfigProvider()
```

`_get_model` 改为 async，内部调用 `await self._provider.get_config(model_type)`，不再直接读 `self.settings`。所有调用方（`generate`、`generate_stream`、`bind_tools`、`ainvoke`）已是 async，改动透明。

`get_llm_service()` singleton 保持不变，继续使用环境变量默认行为。

## 文件变更

| 文件 | 操作 |
|---|---|
| `src/by_qa/qa/common/model_config.py` | 新增：ModelConfig + ModelConfigProvider + EnvModelConfigProvider |
| `src/by_qa/qa/services/llm_service.py` | 修改：__init__ 接受 provider，_get_model 改为 async |
| `src/by_qa/qa/common/__init__.py` | 修改：导出新增类型 |
| `src/by_qa/config.py` | 不改动 |
| 所有 agent 文件 | 不改动 |
| `instant/config.py` | 不改动 |

## 外部系统使用示例

```python
from by_qa.qa.common.model_config import ModelConfig, ModelConfigProvider
from by_qa.qa.services.llm_service import LLMService

class NacosModelConfigProvider:
    async def get_config(self, model_type: str) -> ModelConfig:
        config = await nacos_client.get(f"llm/{model_type}")
        return ModelConfig(
            model_name=config["model"],
            temperature=config["temperature"],
            base_url=config["base_url"],
            api_key=config["api_key"],
        )

llm_service = LLMService(provider=NacosModelConfigProvider())
```
