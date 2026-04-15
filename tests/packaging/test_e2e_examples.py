"""Tests for the packaged end-to-end knowledge base example."""

from pathlib import Path

EXAMPLE_ROOT = Path("examples/e2e_kb_qa")


def test_e2e_example_package_contains_expected_scripts():
    """The repository should expose the three documented example scripts."""
    assert (EXAMPLE_ROOT / "start_kb_service.sh").exists()
    assert (EXAMPLE_ROOT / "run_kb_flow.py").exists()
    assert (EXAMPLE_ROOT / "run_instant_qa.py").exists()


def test_e2e_example_readme_documents_end_to_end_flow():
    """The example README should explain the three-step workflow."""
    content = (EXAMPLE_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pip install by-qa[all]" in content
    assert "cd examples/e2e_kb_qa" in content
    assert "bash ./start_kb_service.sh" in content
    assert "python ./run_kb_flow.py" in content
    assert "python ./run_instant_qa.py" in content
    assert "--dir" in content
    assert "--query" in content
    assert "/api/v1/fileToMarkdownIndex" in content
    assert "/api/v1/knowledge-items/import" in content
    assert "/api/v1/listDir" in content
    assert "/api/v1/glob" in content


def test_root_readme_links_to_packaged_e2e_example():
    """Project README should point users to the repository-level example entrypoint."""
    content = Path("README.md").read_text(encoding="utf-8")

    assert "examples/e2e_kb_qa" in content


def test_e2e_example_script_does_not_reference_removed_build_routes():
    """The packaged KB flow script should not call removed knowledge_build routes."""
    content = (EXAMPLE_ROOT / "run_kb_flow.py").read_text(encoding="utf-8")

    assert "/api/v1/file-to-markdown-index" not in content
    assert "/api/v1/file-to-markdown" not in content
    assert "/api/v1/build-markdown-index" not in content


def test_e2e_example_scripts_use_current_knowledge_api_paths():
    """The packaged example scripts should point at the current camelCase API paths."""
    flow_script = (EXAMPLE_ROOT / "run_kb_flow.py").read_text(encoding="utf-8")
    instant_script = (EXAMPLE_ROOT / "run_instant_qa.py").read_text(encoding="utf-8")

    assert "/api/v1/knowledgeBases/create" in flow_script
    assert "/api/v1/knowledge-items/import" in flow_script
    assert "/api/v1/fileToMarkdownIndex" in flow_script
    assert "/api/v1/listDir" in flow_script
    assert "/api/v1/list_dir" not in flow_script

    assert "/api/v1/knowledge-items/search" in instant_script
