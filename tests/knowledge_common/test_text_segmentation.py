"""Unit tests for Chinese text segmentation utility."""

from __future__ import annotations

from by_qa.knowledge_common.text_segmentation import segment_for_fts


def test_segment_for_fts_empty_string():
    assert segment_for_fts("") == ""


def test_segment_for_fts_chinese_text():
    result = segment_for_fts("如何部署Kubernetes集群")
    tokens = result.split()
    assert "部署" in tokens
    assert "Kubernetes" in tokens
    assert len(tokens) >= 3


def test_segment_for_fts_pure_english():
    result = segment_for_fts("hello world")
    tokens = result.split()
    assert "hello" in tokens
    assert "world" in tokens


def test_segment_for_fts_mixed_chinese_english():
    result = segment_for_fts("使用Python开发Web应用")
    tokens = result.split()
    assert "Python" in tokens
    assert len(tokens) >= 3


def test_segment_for_fts_numbers_preserved():
    result = segment_for_fts("2024年第一季度报告")
    tokens = result.split()
    assert "2024" in tokens
    assert "季度" in tokens or "第一" in tokens
    assert len(tokens) >= 3


def test_segment_for_fts_filters_common_stopwords():
    result = segment_for_fts("我的和你的都在这")
    tokens = result.split()
    assert "的" not in tokens
    assert "和" not in tokens
    assert "都" not in tokens
    assert "在" not in tokens


def test_segment_for_fts_filters_punctuation():
    result = segment_for_fts("一、概述。内容：")
    tokens = result.split()
    assert "、" not in tokens
    assert "。" not in tokens
    assert "：" not in tokens


def test_segment_for_fts_cut_for_search_produces_ngrams():
    """cut_for_search produces n-grams for better search recall."""
    result = segment_for_fts("北京市经济发展")
    tokens = result.split()
    assert "北京" in tokens
    assert "经济" in tokens
    assert "发展" in tokens
    # cut_for_search also produces the full compound
    assert "北京市" in tokens or "经济发展" in tokens


def test_segment_for_fts_normalizes_cjk_compatibility():
    """CJK Compatibility Ideographs (e.g. ⾼) normalize to standard forms."""
    result = segment_for_fts("高精尖 ⾼精尖")
    tokens = result.split()
    assert tokens.count("高精尖") == 2
