"""Tests for GitHub Actions workflow structure."""

from pathlib import Path


def test_ci_workflow_installs_module_specific_dependency_groups():
    """CI should install the matching extra for each module test job."""
    content = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "uv sync --extra dev --extra knowledge --extra qa" in content
    assert "uv sync --extra dev --extra knowledge" in content
    assert "uv sync --extra dev --extra qa" in content
    assert "--extra dev --all-extras" not in content


def test_ci_workflow_runs_module_specific_test_scripts():
    """CI should run separate scripts for knowledge and QA plus packaging tests."""
    content = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "bash scripts/knowledge_base/run_unit_tests.sh" in content
    assert "bash scripts/qa/run_unit_tests.sh" in content
    assert "uv run python -m pytest tests/packaging -q" in content


def test_release_workflow_uses_named_pypi_environment():
    """Trusted publishing should use a stable GitHub environment name."""
    content = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "publish-pypi:" in content
    assert "environment: pypi" in content
    assert "uses: pypa/gh-action-pypi-publish@release/v1" in content
