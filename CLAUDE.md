# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

by-qa is a modular knowledge base and QA service built with Python 3.12+, FastAPI, LangChain, and LangGraph. Two installable module groups:

- **knowledge** (`knowledge_base` + `knowledge_build`): Document management, file parsing/chunking/embedding, storage and retrieval (OpenGauss + MinIO + Redis)
- **qa** (`qa.engines.instant`, `qa.engines.fast`): Question answering engines using LangGraph state machines

Modules are dynamically registered at startup — only modules whose dependencies are installed get loaded. The `knowledge_build` code still exists as a separate package under `src/by_qa/knowledge_build/` but its extra has been merged into `knowledge`; it is invoked via `knowledge_base`'s `fileToMarkdownIndex` endpoint.

## Working Principles

- Do not use absolute filesystem paths when writing code, docs, or examples.
- Prefer repository-relative paths in prose, links, commands, and configuration examples.
- Do not couple `knowledge_build` back into `knowledge_base` — they share only neutral models in `knowledge_common`.
- Prefer targeted changes over broad refactors.
- **Feature branches required**: All new features and bug fixes must be developed on a feature branch created from `main`. The branch stays separate until development and self-testing are complete, and the user has verified the changes — only then merge back to `main`.

## Common Commands

```bash
# Install development environment
uv sync --extra dev --extra knowledge --extra qa

# Install everything
uv sync --all-extras

# Run the app
by-qa                                # CLI entry point
uv run python -m by_qa.main          # Via module

# Lint and format (pre-commit: isort, ruff, ruff-format)
uv run pre-commit run --all-files

# IMPORTANT: Before running the service or tests, disable proxy env vars:
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy=

# Run tests by module
bash scripts/knowledge_base/run_unit_tests.sh
bash scripts/knowledge_build/run_unit_tests.sh
bash scripts/qa/run_unit_tests.sh
bash scripts/knowledge_base/run_integration_tests.sh  # requires docker stack

# Run stateful API integration tests (in-process, no docker needed)
uv run python -m pytest tests/knowledge_base/integration/test_kb_api_stateful_integration.py -v

# Run a single test
uv run python -m pytest tests/path/to/test.py::test_name -v

# Build
uv build

# Docker stack (OpenGauss + MinIO + Redis)
make kb-stack-up
make kb-stack-down
```

## Architecture

```
src/by_qa/
├── config.py              # Pydantic Settings from env vars
├── main.py                # FastAPI app factory, dynamic module registration, lifespan
├── core/                  # Logging, shared exceptions, ModelConfigProvider protocol
├── knowledge_common/      # Shared schemas and exceptions across knowledge modules
├── knowledge_base/        # REST API + services + repos for document storage/retrieval
│   ├── api/               # FastAPI routes (register_routes pattern)
│   ├── services/          # KnowledgeBaseService, IngestionService, SearchService, BootstrapService
│   ├── repositories/      # DB access (KnowledgeBase, FsEntry, ItemChunk, BuildTask, etc.)
│   └── infrastructure/    # Database (OpenGauss), object storage (MinIO), runtime wiring
├── knowledge_build/       # File parsing → markdown → chunking → embedding
│   └── services/          # DocumentChunkingService, heading_patterns
└── qa/
    ├── common/            # BaseQAEngine, models, state types, config, context, middleware
    │   └── middleware/    # ToolCallGuardMiddleware (intercepts invalid tool calls)
    ├── agents/            # Reusable subgraphs: SingleHopReact, MultiHopReact, QueryDecomposer, Aggregator, etc.
    ├── services/          # LLMService (OpenAI-compatible), CheckpointerFactory
    ├── tools/             # ServiceToolDispatcher — remote knowledge-base tool calls
    └── engines/
        ├── fast/          # Fast QA engine (linear: rewrite → retrieve → answer)
        └── instant/       # Instant QA engine (multi-hop: decompose → parallel workers → aggregate → final answer)
```

### Key Design Patterns

- **Dynamic module loading**: `main.py` defines `ApiModuleDefinition` tuples; each module is loaded only if its `required_packages` are importable.
- **BaseQAEngine**: Abstract base in `qa/common/base_engine.py` — engines implement `_build_graph()` and `_do_stream_search()`. Manages checkpointer lifecycle via async context manager.
- **LangGraph state machines**: Each engine defines a `StateGraph` with TypedDict state. Instant engine uses `Send()` for parallel sub-query dispatch to single-hop/multi-hop workers.
- **ModelConfigProvider protocol**: Pluggable model configuration (`core/model_config.py`). Default reads from env vars; custom providers set via `BY_QA_MODEL_CONFIG_PROVIDER=module:attribute`.
- **QARuntimeContext**: Dataclass injected into graph nodes carrying `QARetrievalConfig` and `LLMService`.
- **OperationRegistry**: Maps `OperationType` enums to tool specs for remote knowledge-base operations (search, listDir, glob, readFile).
- **Service registry**: Uses `by-framework`'s `ServiceRegistry` backed by Redis for service discovery.

## Configuration

All configuration via environment variables (see `.env.example`). Key groups:
- Server: HOST, PORT, SERVICE_NAME
- Redis: REDIS_HOST, REDIS_PORT (required — `by-framework` service registry)
- Storage: DB_HOST/DB_USER/DB_PASS (OpenGauss), MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY
- Embedding: EMBEDDING_MODEL_NAME, EMBEDDING_BASE_URL, EMBEDDING_DIMENSION
- LLM: LLM_BASE_URL, LLM_API_KEY, plus per-role model vars (classifier, retrieval, generator, quality, decomposer, aggregator)
- QA Runtime: CHECKPOINTER_BACKEND (sqlite/postgres/opengauss)

Custom model config provider: set `BY_QA_MODEL_CONFIG_PROVIDER=my_package:MyProvider` implementing `ModelConfigProvider` protocol.

## Testing

- pytest with `asyncio_mode=auto` (configured in `pyproject.toml [tool.pytest.ini_options]`)
- `pythonpath=src` — imports use `by_qa.*`
- Tests mirror module structure: `tests/knowledge_base/`, `tests/knowledge_build/`, `tests/qa/`, `tests/packaging/`
- Integration tests for `knowledge_base` require the Docker middleware stack (`make kb-stack-up`)

## CI/CD

- GitHub Actions: lint → module tests (each module tested independently) → build
- Release: triggered by `v*` tags; tag version must match `pyproject.toml` version
- PyPI publishing via trusted publishing
