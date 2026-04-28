"""Import smoke tests for the fast QA package."""

import importlib


def test_can_import_fast_qa_modules():
    fast_module = importlib.import_module("by_qa.qa.fast")
    assert hasattr(fast_module, "FastQAEngine")

    assert importlib.import_module("by_qa.qa.fast.engine")
    assert importlib.import_module("by_qa.qa.fast.graph")
    assert importlib.import_module("by_qa.qa.fast.nodes.retrieve")
    assert importlib.import_module("by_qa.qa.fast.state")
    assert importlib.import_module("by_qa.qa.fast.types")
