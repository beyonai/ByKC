#!/usr/bin/env bash

set -euo pipefail

uv run python -m pytest \
  tests/knowledge_base/unit \
  tests/knowledge_base/scripts \
  tests/knowledge_common \
  -q
