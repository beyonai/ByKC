"""Integration tests for metadata properties, file metadata, and DSL search.

Scenario coverage maps to docs/modules/api-integration-test-plan.md M1-M17.

Requires:
  - `make kb-stack-up` (OpenGauss + MinIO + Redis)
  - Reachable embedding service via EMBEDDING_BASE_URL / EMBEDDING_API_KEY /
    EMBEDDING_MODEL_NAME / EMBEDDING_DIMENSION env vars.

Run:
  NO_PROXY=127.0.0.1,localhost HTTPS_PROXY= HTTP_PROXY= no_proxy=127.0.0.1,localhost http_proxy= https_proxy= \
    uv run python -m pytest tests/knowledge_base/integration/test_metadata_api_integration.py -v
"""

from __future__ import annotations
