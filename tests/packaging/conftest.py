"""Packaging test fixtures.

by_framework is not installed in the packaging test environment (uv sync --extra dev).
Mock it before any by_qa module is imported so the import chain
core/__init__ -> framework_client -> by_framework does not fail at collection time.
"""

import sys
from unittest.mock import MagicMock

for _mod in [
    "by_framework",
    "by_framework.common",
    "by_framework.common.redis_client",
    "by_framework.core",
    "by_framework.core.discovery",
    "by_framework.util",
    "by_framework.util.discovery_http_client",
    "by_framework.util.http_client",
]:
    sys.modules.setdefault(_mod, MagicMock())
