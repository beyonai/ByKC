"""Import smoke tests for the QA instant package skeleton."""

import importlib


def test_can_import_qa_instant_modules():
    instant_module = importlib.import_module("by_qa.qa.instant")
    assert instant_module
    assert hasattr(instant_module, "InstantSearchState")
    assert hasattr(instant_module, "InstantQAState")

    assert importlib.import_module("by_qa.qa.instant.state")

    instant_types = importlib.import_module("by_qa.qa.instant.types")
    assert instant_types
    assert hasattr(instant_types, "NodeNames")
