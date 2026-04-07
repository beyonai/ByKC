# by-qa

`by-qa` 是一个按模块组织的开源知识与问答服务仓库，当前包含：

- `knowledge_base`：知识库管理、导入结果落库和检索
- `knowledge_build`：文件解析、切片和 embedding 构建
- `qa.instant`：即时问答核心编排能力

项目采用可选依赖安装，不同模块可以按需安装和启用。

## 模块概览

### Knowledge Base

`knowledge_base` 提供知识库管理、文档导入、文件读取和检索能力，适合用作知识数据的存储与检索承载层。

相关文档：

- [模块框架](docs/modules/knowledge-base/framework.md)
- [模块设计](docs/modules/knowledge-base/design.md)
- [接口说明](docs/modules/knowledge-base/api.md)
- [MinIO 说明](docs/modules/knowledge-base/minio.md)

### Knowledge Build

`knowledge_build` 提供 3 个知识构建相关接口：

- `file-to-markdown`
- `build-markdown-index`
- `file-to-markdown-index`

需要特别说明的是，当前 `knowledge_build` 更偏向示例实现，适合本地联调、协议参考和轻量验证。如果要保证生产检索效果，更推荐接入第三方知识构建能力，再把 markdown、chunk 和 embedding 结果导入 `knowledge_base`。

相关文档：

- [模块框架](docs/modules/knowledge-build/framework.md)
- [模块设计](docs/modules/knowledge-build/design.md)
- [处理流程](docs/modules/knowledge-build/process.md)

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

只安装知识构建：

```bash
pip install by-qa[knowledge-build]
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
uv sync --extra knowledge-build
uv sync --extra qa
uv sync --all-extras
```

开发环境推荐：

```bash
uv sync --extra dev --extra knowledge --extra knowledge-build --extra qa
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

如果只使用 `knowledge_build`，通常只需要 embedding 相关配置；如果使用 `knowledge_base`，还需要 openGauss、MinIO 等运行配置。

## 测试

知识库单元测试：

```bash
bash scripts/knowledge_base/run_unit_tests.sh
```

知识构建单元测试：

```bash
bash scripts/knowledge_build/run_unit_tests.sh
```

问答单元测试：

```bash
bash scripts/qa/run_unit_tests.sh
```

运行全部代码质量检查：

```bash
uv run pre-commit run --all-files
```

## CI 与发布

当前仓库已配置：

- GitHub Actions CI
- PyPI 发布
- GitHub Releases 发布

正式发布由 `v*` tag 触发，并会校验 tag 版本与 `pyproject.toml` 中的版本一致。

## 仓库结构

```text
src/by_qa/
├── core/
├── knowledge_base/
├── knowledge_build/
├── knowledge_common/
└── qa/
```

## 当前边界

当前仓库已经开源并维护的是：

- `knowledge_base`
- `knowledge_build`
- `qa.instant`

当前还不在开源范围内或未恢复为对外能力的包括：

- `qa.deep`
- 即时问答对外 Web API
- 生产级知识构建流水线

## License

MIT
