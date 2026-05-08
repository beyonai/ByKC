"""Tests for document chunking service file type handling."""

import io
import json as json_lib

import httpx
import pytest

from by_qa.core import logger as core_logger
from by_qa.knowledge_build.services.document_chunking_service import (
    DocumentChunkingService,
)
from by_qa.knowledge_build.services.heading_patterns import (
    HeadingPattern,
    load_heading_patterns,
)
from by_qa.knowledge_common.exceptions import KnowledgeConfigurationError


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


def test_extract_text_from_docx_includes_table_cells():
    """DOCX tables should be included in extracted markdown text."""
    docx = pytest.importorskip("docx")
    service = _make_service()
    buffer = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("正文段落")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "措施"
    table.cell(0, 1).text = "内容"
    table.cell(1, 0).text = "一"
    table.cell(1, 1).text = "放宽准入"
    document.save(buffer)

    text = service.extract_text_from_file(buffer.getvalue(), "docx")

    assert "正文段落" in text
    assert "措施 | 内容" in text
    assert "一 | 放宽准入" in text


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

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.txt")

    assert len(chunks) == 1
    assert "report.md" not in chunks[0].chunk_text
    assert "1 / 2" not in chunks[0].chunk_text
    assert "2 / 2" not in chunks[0].chunk_text
    assert chunks[0].chunk_text.startswith("## Section\nParagraph line 1")
    assert chunks[0].chunk_text.endswith(
        "Paragraph line 1\nParagraph line 2\nParagraph line 3 continues the same section."
    )
    assert chunks[0].start_line == 4
    assert chunks[0].end_line == 12


def test_chunk_and_embed_keeps_repeated_markdown_filenames_in_markdown_content(
    monkeypatch: pytest.MonkeyPatch,
):
    """Native markdown can intentionally repeat filenames in project trees."""
    service = _make_service()
    service.chunk_size = 300
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "# Project Layout\n\n"
        "```text\n"
        "├── README.md\n"
        "├── CHANGELOG.md\n"
        "```\n\n"
        "```text\n"
        "├── README.md\n"
        "├── CHANGELOG.md\n"
        "```\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")
    indexed_text = "\n".join(chunk.chunk_text for chunk in chunks)

    assert "├── README.md" in indexed_text
    assert "├── CHANGELOG.md" in indexed_text


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


def test_chunk_and_embed_keeps_numbered_policy_paragraphs_with_inline_body(
    monkeypatch: pytest.MonkeyPatch,
):
    """Policy clauses that start with numbering but contain inline body text should be indexed."""
    service = _make_service()
    service.chunk_size = 220
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "一、总体要求\n\n"
        "（一）放宽市场准入。支持符合条件的市场主体依法开展业务。\n\n"
        "（二）完善监管机制。加强事中事后监管，提升协同效率。\n\n"
        "四、组织实施\n\n"
        "各部门各单位要高度重视，按照职责分工推进落实。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")
    indexed_text = "\n".join(chunk.chunk_text for chunk in chunks)

    assert len(chunks) == 2
    assert "（一）放宽市场准入。支持符合条件的市场主体依法开展业务。" in indexed_text
    assert "（二）完善监管机制。加强事中事后监管，提升协同效率。" in indexed_text
    assert "各部门各单位要高度重视" in chunks[1].chunk_text


def test_chunk_and_embed_indexes_heading_only_documents(
    monkeypatch: pytest.MonkeyPatch,
):
    """Documents that contain only headings should still produce searchable chunks."""
    service = _make_service()
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = "一、总体要求\n\n二、主要任务\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 2
    assert chunks[0].chunk_text == "一、总体要求"
    assert chunks[1].chunk_text == "二、主要任务"


def test_chunk_and_embed_indexes_trailing_heading_without_body(
    monkeypatch: pytest.MonkeyPatch,
):
    """A final heading without following body text should not disappear from the index."""
    service = _make_service()
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = "一、总体要求\n\n正文内容。\n\n二、主要任务\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")
    indexed_text = "\n".join(chunk.chunk_text for chunk in chunks)

    assert len(chunks) == 2
    assert "正文内容。" in chunks[0].chunk_text
    assert "二、主要任务" in indexed_text


def test_chunk_and_embed_keeps_nested_numeric_parent_with_child_body(
    monkeypatch: pytest.MonkeyPatch,
):
    """Nested numeric parents should remain context, not standalone heading chunks."""
    service = _make_service()
    service.chunk_size = 260
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "一、多跳推理质量提升\n\n"
        "1.2 查询分解优化策略\n\n"
        "1.2.1 结构化分解方法\n\n"
        "Question Decomposition for RAG 提出系统化分解框架。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 1
    assert chunks[0].chunk_text.startswith(
        "一、多跳推理质量提升\n1.2 查询分解优化策略\n1.2.1 结构化分解方法\n\n"
    )
    assert "Question Decomposition for RAG" in chunks[0].chunk_text


def test_chunk_and_embed_treats_consecutive_numbered_steps_as_body(
    monkeypatch: pytest.MonkeyPatch,
):
    """Procedure lists extracted from PDFs should not become one chunk per step."""
    service = _make_service()
    service.chunk_size = 260
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "1.3 错误传播控制\n\n"
        "Chain-of-Verification (CoVe)：\n"
        "1. 生成初始草稿答案\n"
        "2. 生成验证问题检查关键事实\n"
        "3. 独立回答验证问题（避免偏见）\n"
        "4. 发现不一致时生成修正答案\n"
        "实验显示 CoVe 显著减少错误。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")
    indexed_text = "\n".join(chunk.chunk_text for chunk in chunks)

    assert len(chunks) == 1
    assert "1. 生成初始草稿答案" in indexed_text
    assert "4. 发现不一致时生成修正答案" in indexed_text
    assert "实验显示 CoVe 显著减少错误。" in indexed_text


def test_chunk_and_embed_does_not_treat_decimal_table_values_as_headings(
    monkeypatch: pytest.MonkeyPatch,
):
    """Numeric table cells like 0.15 should stay with surrounding table content."""
    service = _make_service()
    service.chunk_size = 260
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "五、关键环节协同优化\n\n"
        "模块\n"
        "权重\n"
        "最终答案\n"
        "答案正确性\n"
        "0.15\n"
        "整体评估用于端到端优化。\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="input.md")

    assert len(chunks) == 1
    assert "0.15" in chunks[0].chunk_text
    assert "整体评估用于端到端优化。" in chunks[0].chunk_text


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
        json_lib.dumps(
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


def test_batch_embed_splits_requests_by_configured_max_texts(
    monkeypatch: pytest.MonkeyPatch,
):
    """Embedding requests should be split into stable batches when text volume exceeds the limit."""
    service = DocumentChunkingService(
        embedding_base_url="http://example.com",
        embedding_api_key="test-key",
        embedding_model_name="test-model",
        embedding_dimension=3,
        embedding_batch_max_texts=2,
    )
    seen_inputs: list[list[str]] = []

    class _FakeResponse:
        def __init__(self, texts: list[str]) -> None:
            self._texts = texts

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {
                        "index": index,
                        "embedding": [float(len(text)), float(index), 3.0],
                    }
                    for index, text in enumerate(self._texts)
                ]
            }

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        del url, headers, timeout
        texts = json["input"]
        seen_inputs.append(texts)
        return _FakeResponse(texts)

    monkeypatch.setattr(
        "by_qa.knowledge_build.services.document_chunking_service.httpx.post",
        _fake_post,
    )

    embeddings = service._batch_embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert seen_inputs == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert embeddings == [
        [1.0, 0.0, 3.0],
        [2.0, 1.0, 3.0],
        [3.0, 0.0, 3.0],
        [4.0, 1.0, 3.0],
        [5.0, 0.0, 3.0],
    ]


def test_batch_embed_supports_minus_one_for_single_request(
    monkeypatch: pytest.MonkeyPatch,
):
    """A batch size of -1 should send all texts in one embedding request."""
    service = DocumentChunkingService(
        embedding_base_url="http://example.com",
        embedding_api_key="test-key",
        embedding_model_name="test-model",
        embedding_dimension=3,
        embedding_batch_max_texts=-1,
    )
    seen_inputs: list[list[str]] = []

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                    {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    {"index": 2, "embedding": [0.7, 0.8, 0.9]},
                ]
            }

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        del url, headers, timeout
        seen_inputs.append(json["input"])
        return _FakeResponse()

    monkeypatch.setattr(
        "by_qa.knowledge_build.services.document_chunking_service.httpx.post",
        _fake_post,
    )

    embeddings = service._batch_embed(["a", "bb", "ccc"])

    assert seen_inputs == [["a", "bb", "ccc"]]
    assert embeddings == [
        [0.1, 0.2, 0.3],
        [0.4, 0.5, 0.6],
        [0.7, 0.8, 0.9],
    ]


def test_batch_embed_rejects_invalid_negative_batch_size():
    """Only -1 is allowed as the non-batching sentinel value."""
    service = DocumentChunkingService(
        embedding_base_url="http://example.com",
        embedding_api_key="test-key",
        embedding_model_name="test-model",
        embedding_dimension=3,
        embedding_batch_max_texts=-2,
    )

    with pytest.raises(KnowledgeConfigurationError, match="greater than 0 or -1"):
        service._batch_embed(["a"])


def test_batch_embed_wraps_http_errors_as_configuration_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    """HTTP failures from the embedding service should surface as knowledge config errors."""
    service = _make_service()

    def _fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        del url, headers, json, timeout
        raise httpx.HTTPError("connection failed")

    monkeypatch.setattr(
        "by_qa.knowledge_build.services.document_chunking_service.httpx.post",
        _fake_post,
    )

    with pytest.raises(
        KnowledgeConfigurationError, match="embedding service request failed"
    ):
        service._batch_embed(["a"])


def test_chunk_and_embed_emits_chunking_and_embedding_stage_logs(
    monkeypatch: pytest.MonkeyPatch,
):
    """Chunking should log markdown splitting and embedding completion summaries."""
    service = _make_service()
    info_messages: list[str] = []
    chunks = [
        {
            "chunk_no": 1,
            "start_line": 1,
            "end_line": 1,
            "chunk_text": "chunk one",
            "char_start": 0,
            "char_end": 9,
        },
        {
            "chunk_no": 2,
            "start_line": 2,
            "end_line": 2,
            "chunk_text": "chunk two",
            "char_start": 10,
            "char_end": 19,
        },
    ]

    monkeypatch.setattr(
        service, "_extract_text", lambda file_bytes, ext: "# Title\n\nBody"
    )
    monkeypatch.setattr(service, "_split_text", lambda text, ext: chunks)
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    monkeypatch.setattr(
        core_logger,
        "info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    payloads = service.chunk_and_embed(b"# Title\n\nBody", filename="input.md")

    assert len(payloads) == 2
    assert info_messages == [
        "document_chunking markdown chunking completed: filename=input.md, chunk_count=2",
        "document_chunking embedding completed: filename=input.md, chunk_count=2",
    ]


def test_chunk_and_embed_does_not_treat_code_block_comments_as_headings(
    monkeypatch: pytest.MonkeyPatch,
):
    """Lines inside fenced code blocks must not be treated as markdown headings.

    A YAML comment like '# .github/workflows/ci.yml' inside a ```yaml block
    matches the markdown_h1 pattern but must not corrupt the heading stack or
    split the surrounding body into tiny chunks.
    """
    service = _make_service()
    service.chunk_size = 512
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "# Guide\n\n"
        "## Section One\n\n"
        "Intro paragraph.\n\n"
        "```yaml\n"
        "# .github/workflows/ci.yml\n"
        "name: CI\n"
        "on: [push]\n"
        "```\n\n"
        "Closing paragraph.\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="guide.md")

    # The comment inside the code block must NOT become a heading.
    # All body text (intro + code block + closing) should be in one or two chunks,
    # not fragmented into many tiny pieces.
    assert len(chunks) <= 2, f"Expected ≤2 chunks but got {len(chunks)}: " + str(
        [c.chunk_text[:60] for c in chunks]
    )

    # The breadcrumb in every chunk must use real document headings only.
    for chunk in chunks:
        assert "# .github/workflows/ci.yml" not in chunk.chunk_text or (
            chunk.chunk_text.count("# .github/workflows/ci.yml") == 1
            and "```yaml" in chunk.chunk_text
        ), f"Code block comment leaked into breadcrumb: {chunk.chunk_text!r}"


def test_chunk_and_embed_preserves_heading_breadcrumb_after_code_block(
    monkeypatch: pytest.MonkeyPatch,
):
    """Heading context must remain correct for body paragraphs that follow a code block."""
    service = _make_service()
    service.chunk_size = 100
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    markdown = (
        "# Title\n\n"
        "## Setup\n\n"
        "Before code.\n\n"
        "```bash\n"
        "# install deps\n"
        "npm install\n"
        "```\n\n"
        "After code paragraph.\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="doc.md")

    # The chunk containing "After code paragraph." must have the real heading breadcrumb.
    after_chunks = [c for c in chunks if "After code paragraph." in c.chunk_text]
    assert after_chunks, "No chunk contains 'After code paragraph.'"
    for chunk in after_chunks:
        assert chunk.chunk_text.startswith("# Title"), (
            f"Breadcrumb corrupted after code block: {chunk.chunk_text!r}"
        )


def test_split_table_block_splits_large_table_by_rows(
    monkeypatch: pytest.MonkeyPatch,
):
    """Large Markdown tables should be split row-by-row, not by character cuts."""
    service = _make_service()
    service.chunk_size = 200
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    header = "| Name | Height | Year |"
    sep = "|------|--------|------|"
    rows = [f"| Building_{i} | {100 + i}m | {2000 + i} |" for i in range(30)]
    markdown = "# Buildings\n\n" + "\n".join([header, sep] + rows) + "\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="table.md")

    assert len(chunks) > 1
    for chunk in chunks:
        body = (
            chunk.chunk_text.split("\n\n", 1)[-1]
            if "\n\n" in chunk.chunk_text
            else chunk.chunk_text
        )
        lines = body.strip().split("\n")
        assert lines[0] == header, f"Chunk missing table header: {lines[0]}"
        assert lines[1] == sep, f"Chunk missing separator: {lines[1]}"


def test_split_table_block_preserves_heading_context(
    monkeypatch: pytest.MonkeyPatch,
):
    """Each table chunk should carry the section heading breadcrumb."""
    service = _make_service()
    service.chunk_size = 200
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    header = "| City | Population |"
    sep = "|------|------------|"
    rows = [f"| City_{i} | {1000000 + i * 1000} |" for i in range(30)]
    markdown = (
        "# Demographics\n\n## Cities\n\n" + "\n".join([header, sep] + rows) + "\n"
    )

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="demo.md")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.chunk_text.startswith("# Demographics\n## Cities\n"), (
            f"Missing heading breadcrumb: {chunk.chunk_text[:60]!r}"
        )


def test_split_table_block_respects_chunk_size_limit(
    monkeypatch: pytest.MonkeyPatch,
):
    """No table chunk body should exceed the hard body size limit."""
    service = _make_service()
    service.chunk_size = 512
    service.chunk_overlap = 64
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    header = "| Name | Value | Description | Category | Status |"
    sep = "|------|-------|-------------|----------|--------|"
    rows = [
        f"| Item_{i} | {i * 100} | A long description for item number {i} | Cat_{i % 5} | Active |"
        for i in range(80)
    ]
    markdown = "## Data\n\n" + "\n".join([header, sep] + rows) + "\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="big.md")

    hard_body_size = max(int(512 * 1.6), 512 + max(64, 128))
    for i, chunk in enumerate(chunks):
        body = (
            chunk.chunk_text.split("\n\n", 1)[-1]
            if "\n\n" in chunk.chunk_text
            else chunk.chunk_text
        )
        assert len(body) <= hard_body_size, (
            f"Chunk {i} body exceeds limit: {len(body)} > {hard_body_size}"
        )


def test_split_table_block_returns_none_for_non_table():
    """Non-table blocks should not be handled by table splitter."""
    from by_qa.knowledge_build.services.document_chunking_service import _TextBlock

    service = _make_service()
    text = "This is a regular paragraph with no table markers at all.\n" * 20
    block = _TextBlock(
        text=text,
        start_char=0,
        end_char=len(text),
        start_line=0,
        end_line=19,
        kind="paragraph",
    )

    result = service._split_table_block(block, 200)
    assert result is None


def test_split_inline_table_splits_single_line_table(
    monkeypatch: pytest.MonkeyPatch,
):
    """Single-line inline tables (header+sep+data on one line) should be split by logical rows."""

    service = _make_service()
    service.chunk_size = 200
    service.chunk_overlap = 0
    monkeypatch.setattr(
        service,
        "_batch_embed",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )

    # Build an inline table: | H1 | H2 | H3 | | --- | --- | --- | | d1 | d2 | d3 | ...
    headers = "| Name | Score | Grade |"
    sep = " --- | --- | --- |"
    data_cells = "".join(
        f" Player_{i} | {i * 10} | {'ABCDE'[i % 5]} |" for i in range(40)
    )
    inline_line = headers + " |" + sep + " |" + data_cells

    markdown = "# Results\n\n" + inline_line + "\n"

    chunks = service.chunk_and_embed(markdown.encode("utf-8"), filename="inline.md")

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.chunk_text.startswith("# Results\n"), (
            f"Missing heading context: {chunk.chunk_text[:40]!r}"
        )


def test_split_inline_table_preserves_header_in_each_chunk():
    """Each chunk from an inline table split should start with the reconstructed header."""
    from by_qa.knowledge_build.services.document_chunking_service import _TextBlock

    service = _make_service()
    max_body = 400

    headers = "| City | Pop | Area |"
    sep = " --- | --- | --- |"
    data_cells = "".join(f" City_{i} | {1000 + i} | {50 + i} |" for i in range(30))
    inline_line = headers + " |" + sep + " |" + data_cells

    block = _TextBlock(
        text=inline_line,
        start_char=0,
        end_char=len(inline_line),
        start_line=0,
        end_line=0,
        kind="paragraph",
    )

    parts = service._split_table_block(block, max_body)
    assert parts is not None
    assert len(parts) > 1
    for i, part in enumerate(parts):
        lines = part.text.split("\n")
        assert "City" in lines[0], f"Chunk {i} missing header: {lines[0]}"
        assert "---" in lines[1], f"Chunk {i} missing separator: {lines[1]}"
        assert len(part.text) <= max_body, f"Chunk {i} exceeds limit: {len(part.text)}"
