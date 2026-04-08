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
    assert "/api/v1/file-to-markdown-index" in content
    assert "/api/v1/knowledge-items/import" in content
    assert "/api/v1/list_dir" in content
    assert "/api/v1/glob" in content


def test_root_readme_links_to_packaged_e2e_example():
    """Project README should point users to the repository-level example entrypoint."""
    content = Path("README.md").read_text(encoding="utf-8")

    assert "examples/e2e_kb_qa" in content
