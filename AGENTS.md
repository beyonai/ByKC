# AGENT.md

This file provides guidance to coding agents working with code in this repository.

## Project Overview

`by-qa` is a modular knowledge base and QA service built with Python 3.12+.

The repository currently contains three optional modules:

- `knowledge_base`: document management, import, storage, and retrieval
- `knowledge_build`: document parsing, markdown conversion, chunking, and embedding generation
- `qa.instant`: instant QA orchestration built on LangGraph state machines

Modules are loaded dynamically at startup. A module is only registered when its required dependencies are installed.

## Working Principles

- Preserve the modular structure of the repository.
- Do not couple `knowledge_build` back into `knowledge_base`.
- Treat `knowledge_build` as a reference implementation, not a production-grade ingestion pipeline.
- Keep `qa.deep` out of scope unless explicitly requested.
- Prefer targeted changes over broad refactors.
- Keep docs and examples aligned with the actual shipped behavior.
- Do not use absolute filesystem paths when writing code, docs, or examples.
- Prefer repository-relative paths in prose, links, commands, and configuration examples.

## Common Commands

```bash
# Install development environment
uv sync --extra dev --extra knowledge --extra knowledge-build --extra qa

# Install a single module
uv sync --extra knowledge
uv sync --extra knowledge-build
uv sync --extra qa

# Install everything
uv sync --all-extras

# BeBefore running the service, unit tests, and integration tests, use the following configuration to disable the proxy to avoid failures caused by reverse proxies and other similar issues.
NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy=

# Run the application
by-qa
uv run python -m by_qa.main

# Lint / format
uv run pre-commit run --all-files

# Run tests by module
bash scripts/knowledge_base/run_unit_tests.sh
bash scripts/knowledge_build/run_unit_tests.sh
bash scripts/qa/run_unit_tests.sh

# Run stateful API integration tests (in-process, no docker needed)
uv run python -m pytest tests/knowledge_base/integration/test_kb_api_stateful_integration.py -v


# Run a single test
uv run python -m pytest tests/path/to/test.py::test_name -v

# Build package artifacts
uv build

# Docker stack (OpenGauss + MinIO)
make kb-stack-up
make kb-stack-down
```

## Repository Structure

```text
src/by_qa/
├── core/
├── knowledge_common/
├── knowledge_base/
├── knowledge_build/
└── qa/
```

Key module responsibilities:

- `src/by_qa/core/`: shared logging and basic cross-cutting code
- `src/by_qa/knowledge_common/`: shared schemas and exceptions for knowledge modules
- `src/by_qa/knowledge_base/`: REST API, services, repositories, and runtime wiring for storage and retrieval
- `src/by_qa/knowledge_build/`: file parsing and chunk/embedding generation
- `src/by_qa/qa/`: shared QA services plus `instant` orchestration

## Architecture Notes

### knowledge_base

- Depends on openGauss, MinIO, and embedding service configuration
- Owns management, import, fetch, and retrieval behavior
- Uses repositories and service-layer orchestration

### knowledge_build

- Owns file parsing, markdown conversion, chunking, and embedding generation
- Exposes build-oriented APIs
- Should remain independent from `knowledge_base` except for shared neutral models in `knowledge_common`

### qa.instant

- Owns instant QA orchestration only
- Preserves single-hop, multi-hop, context management, and streaming event flow
- Does not currently include deep QA, HTTP/SSE exposure, or worker integration

## Configuration

All runtime configuration comes from environment variables. Use `.env.example` as the source of truth.

Important configuration groups:

- server: host and port
- knowledge base: openGauss, MinIO, bootstrap settings
- knowledge build: embedding configuration
- QA: model endpoints, API keys, runtime limits, checkpointer backend

## Middleware and Infrastructure

### knowledge_base requirements

`knowledge_base` depends on:

- openGauss with `vector`, `age`, `ltree`, and `pg_trgm`
- MinIO for object storage
- an OpenAI-compatible embedding endpoint

Relevant files:

- `docker-compose.kb-stack.yml`
- `docker/opengauss/custom/Dockerfile`
- `docker/opengauss/init/init-opengauss.sh`
- `docker/minio/init/init-minio.sh`

### knowledge_build requirements

`knowledge_build` does not require openGauss or MinIO by itself. It only requires the document-processing dependencies plus embedding configuration.

## Testing Guidance

- Use module-specific test scripts whenever possible.
- Keep tests aligned with the modular extras design.
- Integration tests for `knowledge_base` require the Docker middleware stack.
- Packaging tests live under `tests/packaging/` and should remain in sync with CI and release workflows.

## CI/CD

- CI runs lint, module-specific tests, packaging tests, and build validation.
- Release is triggered by `v*` tags.
- Tag version must match `project.version` in `pyproject.toml`.
- PyPI publishing uses trusted publishing via GitHub Actions.

## Documentation Expectations

When updating behavior, keep the corresponding module docs in sync:

- `docs/modules/knowledge/`
- `docs/modules/instant-qa/`

Prefer concise, user-facing documentation. For process docs, use diagrams plus short explanations instead of long prose.
