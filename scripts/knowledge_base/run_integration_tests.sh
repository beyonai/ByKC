#!/usr/bin/env bash

set -euo pipefail

uv run python -m pytest \
  tests/knowledge_base/integration \
  -m integration \
  -q
