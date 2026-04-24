"""Import smoke tests for migrated instant QA runtime modules."""

import importlib


def test_can_import_runtime_modules():
    assert importlib.import_module("by_qa.qa.instant.agents.decomposer")
    assert importlib.import_module("by_qa.qa.instant.agents.multi_hop_react")
    assert importlib.import_module("by_qa.qa.instant.agents.single_hop_react")
    assert importlib.import_module("by_qa.qa.instant.graphs.main")
    assert importlib.import_module("by_qa.qa.instant.graphs.multi_hop")
    assert importlib.import_module("by_qa.qa.instant.graphs.single_hop")
    assert importlib.import_module("by_qa.qa.instant.nodes.decomposer")
    assert importlib.import_module("by_qa.qa.instant.nodes.final_answer")
    assert importlib.import_module("by_qa.qa.instant.nodes.node_enum")
    assert importlib.import_module("by_qa.qa.instant.nodes.router")
    assert importlib.import_module("by_qa.qa.instant.nodes.subanswer_aggregator")
    assert importlib.import_module("by_qa.qa.instant.runtime.factories")
    assert importlib.import_module("by_qa.qa.instant.runtime.hooks")


def test_can_import_shared_qa_runtime_modules():
    assert importlib.import_module("by_qa.qa.common.config")
    assert importlib.import_module("by_qa.qa.common.context")
    assert importlib.import_module("by_qa.qa.common.context_manager")
    assert importlib.import_module("by_qa.qa.common.operation_registry")
    assert importlib.import_module("by_qa.qa.tools.knowledge_tools")
