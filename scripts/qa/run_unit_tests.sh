#!/bin/bash

set -euo pipefail

uv run python -m pytest \
  tests/qa/common/test_agents.py \
  tests/qa/common/test_checkpointer_factory.py \
  tests/qa/common/test_llm_service.py \
  tests/qa/common/test_models.py \
  tests/qa/instant/unit \
  -q
