# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

by-qa is a modular knowledge base and QA service built with Python 3.12+, FastAPI, LangChain, and LangGraph. It has three optional modules that can be installed independently:

- **knowledge_base**: Document management and retrieval (OpenGauss + MinIO)
- **knowledge_build**: Document processing pipeline (PDF/DOCX/XLSX/PPTX → markdown → chunks → embeddings)
- **qa.instant**: Multi-hop question answering using LangGraph state machines

Modules are dynamically registered at startup — only modules whose dependencies are installed get loaded.

## Documentation and Code Style

- Do not use absolute filesystem paths when writing code, docs, or examples.
- Prefer repository-relative paths in prose, links, commands, and configuration examples.

## Common Commands

```bash
# Install all dependencies
uv sync --all-extras

# Install specific module extras
uv sync --extra dev --extra knowledge --extra knowledge-build --extra qa

# Run the app
by-qa                                # CLI entry point
uv run python -m by_qa.main          # Via module
uvicorn by_qa.main:app --reload      # Direct uvicorn

# Lint and format (pre-commit runs ruff, isort, pylint, pyink)
uv run pre-commit run --all-files

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

# Docker stack (OpenGauss + MinIO)
make kb-stack-up
make kb-stack-down
```

## Architecture

```
src/by_qa/
├── core/              # Logging, shared exceptions
├── knowledge_common/  # Shared schemas and exceptions across knowledge modules
├── knowledge_base/    # REST API + services + repos for document storage/retrieval
│   ├── api/           # FastAPI routes
│   ├── services/      # KnowledgeBaseService, ImportService, SearchService
│   ├── repositories/  # DB access (KnowledgeBase, KnowledgeItem, KnowledgeItemChunk, etc.)
│   └── infra/         # Database (OpenGauss) and object storage (MinIO) connections
├── knowledge_build/   # File parsing → markdown → chunking → embedding
│   ├── api/
│   └── services/      # DocumentChunkingService
└── qa/
    ├── common/        # Shared models, LLMService, CheckpointerFactory
    ├── agents/        # QueryDecomposer, SingleHopReact, MultiHopReact, aggregators
    ├── services/      # LLM calls with retry/fallback
    └── instant/       # Main QA orchestration
        ├── graphs/    # LangGraph state machines (single-hop, multi-hop, main)
        ├── nodes/     # Graph nodes: Decomposer, Router, Retriever, ContextManager, FinalAnswer
        ├── runtime/   # Factories, context management, retrieval logic
        └── engine.py  # Main orchestration engine
```

**Key design patterns:**
- Service-oriented with dependency injection
- Pydantic models for all validation and data transfer
- LangGraph TypedDict states for QA graph orchestration
- Optional dependency groups — each module declares its own extras in `pyproject.toml`
- Bootstrap functions initialize DB schemas and services per module

## Configuration

All configuration is via environment variables (see `.env.example`). Key groups:
- Server: HOST, PORT
- Storage: OpenGauss DSN, MinIO endpoint/credentials
- Embedding: model name, base URL, dimension
- LLM: base URL, API key, model names for different QA roles (classifier, retrieval, generator, decomposer, aggregator)
- QA Runtime: context token limits, checkpointer backend (sqlite/postgres)

## Testing

- pytest with `asyncio_mode=auto` (see `pytest.ini`)
- `pythonpath=src` — imports use `by_qa.*`
- Marker `integration` for tests requiring live services
- Tests mirror module structure: `tests/knowledge_base/`, `tests/knowledge_build/`, `tests/qa/`, `tests/packaging/`

## CI/CD

- GitHub Actions: lint → module tests (each module tested independently) → build
- Release: triggered by `v*` tags, publishes to PyPI via trusted publishing
