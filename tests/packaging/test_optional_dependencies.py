"""Tests for optional dependency groups."""

import tomllib
from pathlib import Path


def _load_pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_optional_dependency_groups_are_defined():
    project = _load_pyproject()["project"]
    optional = project["optional-dependencies"]

    assert "knowledge" in optional
    assert "qa" in optional
    assert "all" in optional
    assert "dev" in optional


def test_default_dependencies_do_not_include_capability_specific_packages():
    project = _load_pyproject()["project"]
    dependencies = project["dependencies"]

    assert all(not dep.startswith("fastapi") for dep in dependencies)
    assert all(not dep.startswith("minio") for dep in dependencies)
    assert all(not dep.startswith("aioboto3") for dep in dependencies)
    assert all(not dep.startswith("psycopg") for dep in dependencies)
    assert all(not dep.startswith("uvicorn") for dep in dependencies)
    assert all(not dep.startswith("langchain-openai") for dep in dependencies)


def test_all_group_contains_capability_packages():
    project = _load_pyproject()["project"]
    optional = project["optional-dependencies"]

    knowledge = set(optional["knowledge"])
    qa = set(optional["qa"])
    all_group = set(optional["all"])

    assert knowledge
    assert qa
    assert knowledge.issubset(all_group)
    assert qa.issubset(all_group)


def test_qa_group_includes_checkpoint_backends():
    project = _load_pyproject()["project"]
    qa = set(project["optional-dependencies"]["qa"])

    assert any(dep.startswith("langgraph-checkpoint-sqlite") for dep in qa)
    assert any(dep.startswith("langgraph-checkpoint-postgres") for dep in qa)
    assert any(dep.startswith("psycopg") for dep in qa)


def test_knowledge_group_includes_document_parsing_dependencies():
    project = _load_pyproject()["project"]
    knowledge = set(project["optional-dependencies"]["knowledge"])

    assert any(dep.startswith("fastapi") for dep in knowledge)
    assert any(dep.startswith("aioboto3") for dep in knowledge)
    assert any(dep.startswith("langchain-text-splitters") for dep in knowledge)
    assert any(dep.startswith("python-pptx") for dep in knowledge)
