# 知识模块框架文档

## 模块目标

知识模块负责提供一套完整的知识库管理、文档导入、知识构建、对象存储、版本管理、文件读取和混合检索能力，供上层问答、RAG 或其它检索编排系统复用。

模块重点解决以下问题：

- 用统一 API 管理知识库、目录与文档
- 将原始文件与 Markdown sidecar 写入对象存储
- 将文档版本、chunk 和检索投影写入 openGauss
- 提供文件读取、文件下载和 chunk 级检索能力
- 将知识构建链路纳入同一知识模块视角统一描述

## 目录结构

```text
src/by_qa/
├── config.py
├── main.py
├── core/
│   ├── __init__.py
│   ├── exceptions.py
│   └── logger.py
├── knowledge_common/
│   ├── exceptions.py
│   └── schemas.py
├── knowledge_base/
│   ├── api/
│   ├── infrastructure/
│   ├── repositories/
│   └── services/
└── knowledge_build/
    ├── api/
    ├── services/
    └── runtime.py
```

配套资源：

```text
src/by_qa/knowledge_base/sql/
docker/
scripts/
tests/
docs/modules/knowledge/
```

模块文档：

- [framework.md](./framework.md)
- [design.md](./design.md)
- [api.md](./api.md)
- [process.md](./process.md)
- [minio.md](./minio.md)

## 分层职责

### API 层

目录：

- `src/by_qa/knowledge_base/api/`

职责：

- 定义知识模块对外 API 路由
- 定义请求/响应 schema
- 统一成功和失败响应结构
- 将业务异常映射为稳定的 HTTP 语义

### 服务层

目录：

- `src/by_qa/knowledge_base/services/`
- `src/by_qa/knowledge_build/services/`

职责：

- 编排知识库创建、目录管理、文档导入、删除、检索、缓存清理
- 通过 `knowledge_build/services/` 承载文件解析、文本切片和 embedding 构建能力
- 定义业务校验与事务边界
- 对接对象存储、仓储和 embedding 查询服务

### 仓储层

目录：`src/by_qa/knowledge_base/repositories/`

职责：

- 封装 openGauss 主表、版本表、chunk 表和检索投影表读写
- 承担 SQL 细节和数据访问边界
- 为服务层提供稳定的数据操作接口

### 基础设施层

目录：`src/by_qa/knowledge_base/infrastructure/`

职责：

- 提供数据库连接工厂
- 提供 MinIO 对象存储适配
- 负责知识模块运行时装配
- 负责 schema bootstrap

### 共享层

目录：`src/by_qa/knowledge_common/`

职责：

- 承载知识域共享异常
- 承载 chunk payload 等共享模型

## 运行时边界

当前仓库中的 `src/by_qa/main.py` 是知识模块的运行入口。

它负责：

- FastAPI 应用创建
- 生命周期初始化
- 知识模块 API 路由挂载
- 标准化异常处理

它不负责：

- 聊天接口
- Agent 图编排
- 非知识域业务能力

## 核心依赖

- Web 框架：FastAPI
- 数据库：openGauss
- 对象存储：MinIO
- 数据访问：psycopg
- 配置：pydantic-settings
- 构建能力：文档解析库与 OpenAI 兼容 embedding 服务

## 测试与运维资产

知识模块相关验证资产包括：

- `tests/test_kb_*.py`
- `tests/test_object_storage_service.py`
- `tests/test_opengauss_dockerfile.py`
- `tests/scripts/`
- `docker-compose.kb-stack.yml`
- `scripts/reset_kb_stack.sh`
- `scripts/verify_kb_stack.sh`
- `scripts/reset_kb_data.py`

这些文件共同组成知识模块的开发、测试和运维支撑面。
