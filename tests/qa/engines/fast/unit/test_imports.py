"""Import smoke tests for the fast QA package."""

import importlib


def test_can_import_fast_qa_modules():
    fast_module = importlib.import_module("by_qa.qa.engines.fast")
    assert hasattr(fast_module, "FastQAEngine")

    assert importlib.import_module("by_qa.qa.engines.fast.engine")
    assert importlib.import_module("by_qa.qa.engines.fast.graph")
    assert importlib.import_module("by_qa.qa.engines.fast.nodes.retrieve")
    assert importlib.import_module("by_qa.qa.engines.fast.state")
    assert importlib.import_module("by_qa.qa.engines.fast.types")
