"""Tests for document chunking service file type handling."""

import json

import pytest

from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)
from by_qa.knowledge_build.services.heading_patterns import (
    HeadingPattern,
    load_heading_patterns,
)


def _make_service() -> DocumentChunkingService:
    """Create a service instance without exercising embedding calls."""
    return DocumentChunkingService(
        embedding_base_url="http://example.com",
        embedding_api_key="test-key",
        embedding_model_name="test-model",
        embedding_dimension=3,
    )


def test_extract_text_from_file_accepts_text_types_case_insensitively():
    """Direct service callers should get case-insensitive text type handling."""
    service = _make_service()

    assert service.extract_text_from_file(b"hello", "TXT") == "hello"
    assert service.extract_text_from_file(b"# title", "Md") == "# title"
    assert service.extract_text_from_file(b"# title", "markdown") == "# title"
    assert (
        service.extract_text_from_file(b"name,age\nalice,18\n", "CSV")
        == "name | age\nalice | 18"
    )


def test_chunk_and_embed_preserves_body_line_numbers_when_prepending_headings(
    monkeypatch: pytest.MonkeyPatch,
):
    """Heading context may be prepended, but line numbers should still map to body lines."""
    service = _make_service()
    service.chunk_size = 80
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "# Report Title\n\n"
        "## First Section\n\n"
        "First paragraph line 1\n"
        "First paragraph line 2\n\n"
        "Second paragraph line 1\n"
        "Second paragraph line 2\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 2
    assert chunks[0].start_line == 5
    assert chunks[0].end_line == 6
    assert chunks[0].char_start == markdown.index("First paragraph line 1")
    assert chunks[0].chunk_text.startswith("# Report Title\n## First Section\n\n")
    assert chunks[0].chunk_text.endswith(
        "First paragraph line 1\nFirst paragraph line 2"
    )

    assert chunks[1].start_line == 8
    assert chunks[1].end_line == 9
    assert chunks[1].char_start == markdown.index("Second paragraph line 1")
    assert chunks[1].chunk_text.startswith("# Report Title\n## First Section\n\n")
    assert chunks[1].chunk_text.endswith(
        "Second paragraph line 1\nSecond paragraph line 2"
    )


def test_chunk_and_embed_prefers_paragraph_boundaries_over_character_cuts(
    monkeypatch: pytest.MonkeyPatch,
):
    """Chunks should break at paragraph boundaries when paragraphs already fit the budget."""
    service = _make_service()
    service.chunk_size = 90
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "## Section\n\n"
        "Paragraph one has enough text to stand alone without being merged awkwardly.\n\n"
        "Paragraph two should become its own chunk instead of being split by raw size.\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 2
    assert chunks[0].start_line == 3
    assert chunks[0].end_line == 3
    assert chunks[0].chunk_text.endswith(
        "Paragraph one has enough text to stand alone without being merged awkwardly."
    )
    assert chunks[1].start_line == 5
    assert chunks[1].end_line == 5
    assert chunks[1].chunk_text.endswith(
        "Paragraph two should become its own chunk instead of being split by raw size."
    )


def test_chunk_and_embed_skips_repeated_page_noise_and_keeps_body_ranges(
    monkeypatch: pytest.MonkeyPatch,
):
    """Repeated filename/date/page markers should not pollute chunk text."""
    service = _make_service()
    service.chunk_size = 300
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "report.md\n"
        "2026-04-13\n"
        "1 / 2\n"
        "## Section\n\n"
        "Paragraph line 1\n"
        "Paragraph line 2\n\n"
        "report.md\n"
        "2026-04-13\n"
        "2 / 2\n"
        "Paragraph line 3 continues the same section.\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 1
    assert "report.md" not in chunks[0].chunk_text
    assert "1 / 2" not in chunks[0].chunk_text
    assert "2 / 2" not in chunks[0].chunk_text
    assert chunks[0].chunk_text.startswith("## Section\n\n")
    assert chunks[0].chunk_text.endswith(
        "Paragraph line 1\nParagraph line 2\nParagraph line 3 continues the same section."
    )
    assert chunks[0].start_line == 6
    assert chunks[0].end_line == 12


def test_chunk_and_embed_keeps_numbered_list_items_in_same_chunk_when_they_fit(
    monkeypatch: pytest.MonkeyPatch,
):
    """Parent sections should be able to keep short numbered items together."""
    service = _make_service()
    service.chunk_size = 220
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "1. Industry Overview\n\n"
        "（1）First point explains the industry baseline.\n\n"
        "（2）Second point explains the competitive position.\n\n"
        "（3）Third point explains the near-term opportunity.\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 1
    assert chunks[0].start_line == 3
    assert chunks[0].end_line == 7
    assert "1. Industry Overview" in chunks[0].chunk_text
    assert "（1）First point" in chunks[0].chunk_text
    assert "（2）Second point" in chunks[0].chunk_text
    assert "（3）Third point" in chunks[0].chunk_text


def test_chunk_and_embed_recognizes_pdf_normalized_chinese_heading_forms(
    monkeypatch: pytest.MonkeyPatch,
):
    """Compatibility-form Chinese numerals from PDF extraction should still be treated as headings."""
    service = _make_service()
    service.chunk_size = 220
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "⼀、总体情况\n\n"
        "（⼀）区域基础\n\n"
        "第一段正文。\n\n"
        "1. 核心指标\n\n"
        "第二段正文。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 2
    assert chunks[0].chunk_text.startswith("⼀、总体情况")
    assert "（⼀）区域基础" in chunks[0].chunk_text
    assert "第一段正文。" in chunks[0].chunk_text
    assert chunks[1].chunk_text.startswith("⼀、总体情况")
    assert "（⼀）区域基础" in chunks[1].chunk_text
    assert "1. 核心指标" in chunks[1].chunk_text
    assert "第二段正文。" in chunks[1].chunk_text


def test_chunk_and_embed_does_not_split_numbered_colon_list_items_into_headings(
    monkeypatch: pytest.MonkeyPatch,
):
    """Numbered list items with inline colon content should stay in the same section chunk."""
    service = _make_service()
    service.chunk_size = 320
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "四、策略建议\n\n"
        "（四）具体方案\n\n"
        "4. 目标设定（分阶段推进）\n\n"
        "-. 短期目标（1-2年）：完成基础布局。\n"
        "/. 中期目标（3-5年）：形成产业协同。\n"
        "0. 长期目标（5-10年）：建成领先集群。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 1
    assert "4. 目标设定（分阶段推进）" in chunks[0].chunk_text
    assert "-. 短期目标（1-2年）：完成基础布局。" in chunks[0].chunk_text
    assert "/. 中期目标（3-5年）：形成产业协同。" in chunks[0].chunk_text
    assert "0. 长期目标（5-10年）：建成领先集群。" in chunks[0].chunk_text


def test_chunk_and_embed_infers_heading_hierarchy_from_document_patterns(
    monkeypatch: pytest.MonkeyPatch,
):
    """Heading levels should come from the document's pattern sequence instead of hardcoded Chinese levels."""
    service = _make_service()
    service.chunk_size = 240
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "第一章 总则\n\n"
        "章节导语。\n\n"
        "1. 适用范围\n\n"
        "第一层正文。\n\n"
        "（一）基本原则\n\n"
        "第二层正文。\n\n"
        "1.1 实施细则\n\n"
        "第三层正文。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 4
    assert chunks[0].chunk_text.startswith("第一章 总则\n\n")
    assert "章节导语。" in chunks[0].chunk_text
    assert chunks[1].chunk_text.startswith("第一章 总则\n1. 适用范围\n\n")
    assert "第一层正文。" in chunks[1].chunk_text
    assert chunks[2].chunk_text.startswith(
        "第一章 总则\n1. 适用范围\n（一）基本原则\n\n"
    )
    assert "第二层正文。" in chunks[2].chunk_text
    assert chunks[3].chunk_text.startswith(
        "第一章 总则\n1. 适用范围\n（一）基本原则\n1.1 实施细则\n\n"
    )
    assert "第三层正文。" in chunks[3].chunk_text


def test_chunk_and_embed_supports_custom_heading_pattern_configuration(
    monkeypatch: pytest.MonkeyPatch,
):
    """Heading templates should be replaceable without editing chunking core logic."""
    service = _make_service()
    service.chunk_size = 220
    service.chunk_overlap = 0
    service.heading_patterns = [
        HeadingPattern(name="part_style", regex=r"^第[一二三四五六七八九十]+编"),
        HeadingPattern(
            name="numeric_dot",
            regex=r"^\d+(?:\.\d+)*[.、]\s*\S",
            reject_if_contains_colon=True,
        ),
    ]
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = "第一编 总则\n\n编级导语。\n\n1. 适用范围\n\n条款正文。\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 2
    assert chunks[0].chunk_text.startswith("第一编 总则\n\n")
    assert chunks[1].chunk_text.startswith("第一编 总则\n1. 适用范围\n\n")


def test_load_heading_patterns_reads_json_configuration(
    tmp_path: pytest.TempPathFactory,
):
    """Heading templates should be loadable from a JSON config list."""
    config_path = tmp_path / "heading_patterns.json"
    config_path.write_text(
        json.dumps(
            [
                {
                    "name": "part_style",
                    "regex": "^第[一二三四五六七八九十]+编",
                },
                {
                    "name": "numeric_dot",
                    "regex": "^\\d+(?:\\.\\d+)*[.、]\\s*\\S",
                    "reject_if_contains_colon": True,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    patterns = load_heading_patterns(config_path)

    assert [pattern.name for pattern in patterns] == ["part_style", "numeric_dot"]
    assert patterns[0].regex == "^第[一二三四五六七八九十]+编"
    assert patterns[1].reject_if_contains_colon is True
