"""Import smoke tests for migrated instant QA runtime modules."""

import importlib


def test_can_import_runtime_modules():
    assert importlib.import_module("by_qa.qa.agents.single_hop_react")
    assert importlib.import_module("by_qa.qa.agents.multi_hop_react")
    assert importlib.import_module("by_qa.qa.instant.graph")
    assert importlib.import_module("by_qa.qa.instant.nodes.final_answer")
    assert importlib.import_module("by_qa.qa.instant.types")
    assert importlib.import_module("by_qa.qa.instant.nodes.router")
    assert importlib.import_module("by_qa.qa.common.middleware.tool_call_guard")


def test_can_import_shared_qa_runtime_modules():
    assert importlib.import_module("by_qa.qa.common.config")
    assert importlib.import_module("by_qa.qa.common.context")
    assert importlib.import_module("by_qa.qa.common.context_manager")
    assert importlib.import_module("by_qa.qa.common.messages")
    assert importlib.import_module("by_qa.qa.common.operation_registry")
    assert importlib.import_module("by_qa.qa.tools.knowledge_tools")
