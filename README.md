# by-qa

`by-qa` 是一个按模块组织的开源知识与问答服务仓库，当前包含：

- `knowledge_base`：知识库管理、文件解析/切片/embedding 构建、导入结果落库和检索
- `qa.instant`：即时问答核心编排能力

项目采用可选依赖安装，不同模块可以按需安装和启用。

## 模块概览

### Knowledge Base

`knowledge_base` 提供知识库管理、文档导入、`fileToMarkdownIndex` 知识构建、文件读取和检索能力，适合用作知识数据的存储与检索承载层。

相关文档：

- [模块框架](docs/modules/knowledge/framework.md)
- [模块设计](docs/modules/knowledge/design.md)
- [接口说明](docs/modules/knowledge/api.md)
- [MinIO 说明](docs/modules/knowledge/minio.md)

知识构建能力已经并入 `knowledge_base`，旧的 `file-to-markdown`、`build-markdown-index`、`file-to-markdown-index` 独立接口均已弃用并移除；当前统一通过 `POST /api/v1/fileToMarkdownIndex` 触发文件解析、切片、向量化和落库链路。

### Instant QA

`qa.instant` 提供即时问答的代码级能力入口，保留了单跳、多跳、上下文管理和流式事件模型。当前不包含深度问答、HTTP/SSE 对外接口和 worker 适配层。

相关文档：

- [模块框架](docs/modules/instant-qa/framework.md)
- [模块设计](docs/modules/instant-qa/design.md)
- [处理流程](docs/modules/instant-qa/process.md)

## 安装

只安装知识库：

```bash
pip install by-qa[knowledge]
```

只安装问答：

```bash
pip install by-qa[qa]
```

安装全部模块：

```bash
pip install by-qa[all]
```

如果使用 `uv`：

```bash
uv sync --extra knowledge
uv sync --extra qa
uv sync --all-extras
```

开发环境推荐：

```bash
uv sync --extra dev --extra knowledge --extra qa
```

## 启动

项目入口会根据当前已安装模块动态注册 API。

本地启动：

```bash
uv run python -m by_qa.main
```

或：

```bash
by-qa
```

默认健康检查接口：

```text
GET /health
```

健康检查响应中会包含当前已启用和被跳过的模块信息，便于确认模块是否按预期加载。

## End-to-End Example

如果你希望按 `pip install by-qa[all]` 的方式完整体验“服务拉起 -> 知识构建 -> 入库 -> `list_dir` / `glob` / 检索 -> 即时问答”，可以直接使用仓库根目录下的示例：

```bash
bash examples/e2e_kb_qa/start_kb_service.sh
python examples/e2e_kb_qa/run_kb_flow.py
python examples/e2e_kb_qa/run_instant_qa.py
```

详细说明见 [examples/e2e_kb_qa/README.md](./examples/e2e_kb_qa/README.md)。

## 配置

项目通过仓库根目录的 `.env` 文件读取配置，参考示例：

```bash
cp .env.example .env
```

常见配置分组包括：

- 服务启动配置
- 知识库存储配置
- embedding 配置
- 即时问答模型与运行时配置

默认情况下，模型配置来自 `.env` 中的 `LLM_*`、`EMBEDDING_*` 和各角色模型变量。pip 安装后如果需要接入自建配置中心或模型网关，可以将 provider 包安装到同一个 Python 环境，并设置：

```bash
BY_QA_MODEL_CONFIG_PROVIDER=my_provider.package:CustomModelConfigProvider
```

该 provider 需要实现 `by_qa.core.ModelConfigProvider` 协议，返回 `by_qa.core.ModelConfig`。未设置时服务继续使用内置的环境变量 provider。

如果使用 `knowledge_base`，需要同时准备 openGauss、MinIO、Redis 和 embedding 相关配置；如果运行完整的 `by-qa` 服务，`qa.instant` 也会复用同一套运行时基础设施。其中 Redis 是必需项，因为项目依赖 `by-framework` 提供运行时基础能力。

## 中间件依赖

不同模块依赖的中间件不同：

- `knowledge_base`：依赖 openGauss、MinIO、Redis 和 embedding 服务，并承载知识构建接口
- `qa.instant`：当前是代码级能力入口，本身不直接操作 openGauss 或 MinIO，但服务运行仍依赖 `by-framework`，因此 Redis 仍是必需中间件；如果结合知识检索使用，通常也会依赖 `knowledge_base`

### openGauss

`knowledge_base` 需要一份带扩展能力的 openGauss 环境。当前仓库默认使用自定义镜像，而不是直接使用原始官方镜像。

需要满足的能力包括：

- `vector` 类型与 `ivfflat` 索引能力
- `age` 扩展
- `ltree` 扩展
- `pg_trgm` 扩展

其中：

- `vector` 与 `age` 依赖底层 openGauss / DataVec 能力
- `ltree` 和 `pg_trgm` 由仓库里的自定义镜像在构建时编译并安装

相关文件：

- 自定义镜像定义：`docker/opengauss/custom/Dockerfile`
- 初始化脚本：`docker/opengauss/init/init-opengauss.sh`
- 编排文件：`docker-compose.kb-stack.yml`

初始化脚本会在数据库可用后执行以下检查和准备：

- 校验 `ltree`、`pg_trgm`、`age` 是否可用
- 校验 `vector` 类型是否可用
- 创建扩展、图谱和 smoke test 表
- 验证 `ivfflat` 索引是否可正常创建

### MinIO

`knowledge_base` 用 MinIO 存放原始文件和读取链接。默认编排同时提供：

- MinIO 服务
- bucket 初始化容器

相关文件：

- MinIO 初始化脚本：`docker/minio/init/init-minio.sh`
- 编排文件：`docker-compose.kb-stack.yml`

### Redis

仓库默认编排提供 Redis。Redis 是必需中间件，因为项目依赖 `by-framework` 提供服务注册等运行时基础能力。

相关文件：

- 编排文件：`docker-compose.kb-stack.yml`
- 环境变量示例：`.env.example`

### 构建 openGauss 自定义镜像

如果你要本地运行知识库，推荐直接使用仓库提供的 compose 配置构建镜像：

```bash
docker compose -f docker-compose.kb-stack.yml build opengauss
```

如果希望单独构建镜像，也可以直接执行：

```bash
docker build \
  -f docker/opengauss/custom/Dockerfile \
  -t by_qa/opengauss-server-kb:7.0.0-RC1 \
  .
```

默认会基于 `opengauss/opengauss-server:7.0.0-RC1` 构建，并从 openGauss 对应源码中编译 `ltree` 与 `pg_trgm`。

### 启动知识库中间件

启动 openGauss、MinIO 和 Redis：

```bash
docker compose -f docker-compose.kb-stack.yml up -d opengauss minio redis
```

执行初始化：

```bash
docker compose -f docker-compose.kb-stack.yml --profile init up --abort-on-container-exit opengauss-init minio-init
```

如果需要重置或验证环境，也可以使用仓库脚本：

```bash
/bin/bash scripts/reset_kb_stack.sh
/bin/bash scripts/verify_kb_stack.sh
```

## 测试

知识库单元测试：

```bash
bash scripts/knowledge_base/run_unit_tests.sh
```

问答单元测试：

```bash
bash scripts/qa/run_unit_tests.sh
```

运行全部代码质量检查：

```bash
uv run pre-commit run --all-files
```

## 评估

`eval/` 提供标准化的 QA 准确率评估流程，按数据集组织，当前支持 FRAMES benchmark。

```bash
# 安装 eval 依赖
uv sync --extra eval --extra qa

# 1. 下载数据集（维基百科页面）
uv run python -m eval.cli download frames

# 2. 入库到知识库
uv run python -m eval.cli ingest frames --kb-base-url http://127.0.0.1:8000

# 3. 运行评估
uv run python -m eval.cli run frames --mode instant --sample 10

# 报告输出到 eval/reports/
```

三步流程分离执行，`kb_code` 由 ingest 步骤自动记录到 `.ingest_state.json`，评估时自动读取，无需手动指定。

添加新数据集：在 `eval/datasets/<name>/` 下实现 `DatasetSpec` 协议（`download`、`ingest`、`load_queries`），并在注册表中注册即可。

## CI 与发布

当前仓库已配置：

- GitHub Actions CI
- PyPI 发布
- GitHub Releases 发布

正式发布由 `v*` tag 触发，并会校验 tag 版本与 `pyproject.toml` 中的版本一致。

## 仓库结构

```text
eval/
├── cli.py
├── runner.py
├── judge.py
├── models.py
└── datasets/
    └── frames/
src/by_qa/
├── core/
├── knowledge_base/
├── knowledge_common/
└── qa/
```

## 当前边界

当前仓库已经开源并维护的是：

- `knowledge_base`
- `qa.instant`

当前还不在开源范围内或未恢复为对外能力的包括：

- `qa.deep`
- 即时问答对外 Web API
- 生产级知识构建流水线

## License

MIT
