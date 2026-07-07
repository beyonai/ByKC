# tests/knowledge_common/test_kb_path_utils.py
from by_qa.knowledge_common.kb_path_utils import normalize_kb_path


def test_normalize_simple_relative():
    assert normalize_kb_path("/docs/proj", "images/x.png") == "/docs/proj/images/x.png"


def test_normalize_dot_segments():
    assert normalize_kb_path("/docs/proj/sub", "./x.md") == "/docs/proj/sub/x.md"
    assert normalize_kb_path("/docs/proj/sub", "../x.md") == "/docs/proj/x.md"


def test_normalize_escape_root_returns_none():
    assert normalize_kb_path("/docs", "../../x.md") is None


def test_normalize_absolute_ref_resolves_from_root():
    assert normalize_kb_path("/docs/proj", "/abs/a.md") == "/abs/a.md"


def test_normalize_strips_leading_trailing_slash():
    assert normalize_kb_path("docs/proj/", "x.md") == "/docs/proj/x.md"
