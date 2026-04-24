"""Mock heavy optional dependencies so packaging tests run without qa extras."""

import sys
from unittest.mock import MagicMock

for _mod in [
    "langchain_core",
    "langchain_core.runnables",
    "by_qa.qa.services",
    "by_qa.qa.services.checkpointer_factory",
    "by_qa.qa.services.llm_service",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
