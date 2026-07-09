# tests/knowledge_common/test_markdown_reference.py

from by_qa.knowledge_common.markdown_reference import (
    URL_SCHEME_RE,
    detect_reference_spans,
    detect_reference_token_spans,
    split_target,
)


def test_detect_reference_spans_image_and_link_non_overlapping():
    text = "see ![alt](images/x.png) and [doc](../doc.md) end"
    spans = detect_reference_spans(text)
    assert len(spans) == 2
    # image first
    s0, e0, alt0, tgt0, img0 = spans[0]
    assert text[s0:e0] == "![alt](images/x.png)"
    assert alt0 == "alt"
    assert tgt0 == "images/x.png"
    assert img0 is True
    # link second, not treated as image
    s1, e1, alt1, tgt1, img1 = spans[1]
    assert text[s1:e1] == "[doc](../doc.md)"
    assert alt1 == "doc"
    assert tgt1 == "../doc.md"
    assert img1 is False


def test_detect_reference_spans_image_takes_precedence_over_link():
    # the link regex must not re-match inside an image span
    text = "![a](b.png)"
    spans = detect_reference_spans(text)
    assert len(spans) == 1
    assert spans[0][4] is True


def test_detect_reference_spans_ignores_bare_reference_tokens():
    text = "see [doc](../doc.md), ![img](images/x.png), and byqa-ref://12345"
    spans = detect_reference_spans(text)
    assert [
        (text[s:e], alt, target, is_image) for s, e, alt, target, is_image in spans
    ] == [
        ("[doc](../doc.md)", "doc", "../doc.md", False),
        ("![img](images/x.png)", "img", "images/x.png", True),
    ]


def test_detect_reference_token_spans_detects_bare_tokens_in_order():
    text = "refs byqa-ref://12345 and byqa-ref://67890"
    spans = detect_reference_token_spans(text)
    assert spans == [
        (5, 21, 12345),
        (26, 42, 67890),
    ]
    assert [text[start:end] for start, end, _ in spans] == [
        "byqa-ref://12345",
        "byqa-ref://67890",
    ]


def test_detect_reference_token_spans_ignores_partial_tokens():
    assert detect_reference_token_spans("skip byqa-ref:// and keep text") == []


def test_detect_reference_token_spans_excludes_adjacent_punctuation():
    text = "refs (byqa-ref://123), byqa-ref://456. [byqa-ref://789]\nbyqa-ref://10"
    spans = detect_reference_token_spans(text)
    assert [text[start:end] for start, end, _ in spans] == [
        "byqa-ref://123",
        "byqa-ref://456",
        "byqa-ref://789",
        "byqa-ref://10",
    ]
    assert [reference_id for _, _, reference_id in spans] == [123, 456, 789, 10]


def test_detect_reference_token_spans_rejects_embedded_or_suffixed_fragments():
    text = "bad abcbyqa-ref://123 byqa-ref://456abc _byqa-ref://789 byqa-ref://10_ ok byqa-ref://11"
    valid_start = text.index("byqa-ref://11")
    valid_end = valid_start + len("byqa-ref://11")

    spans = detect_reference_token_spans(text)

    assert spans == [(valid_start, valid_end, 11)]
    assert text[valid_start:valid_end] == "byqa-ref://11"


def test_split_target_strips_anchor_and_query():
    assert split_target("images/x.png") == ("images/x.png", "")
    assert split_target("a.md#section") == ("a.md", "#section")
    assert split_target("a.md?q=1") == ("a.md", "?q=1")
    assert split_target("a.md?q=1#x") == ("a.md", "?q=1#x")


def test_url_scheme_re_matches_external():
    assert URL_SCHEME_RE.match("http://x")
    assert URL_SCHEME_RE.match("https://x")
    assert URL_SCHEME_RE.match("mailto:a@b.com")
    assert URL_SCHEME_RE.match("data:image/png")
    assert not URL_SCHEME_RE.match("images/x.png")
    assert not URL_SCHEME_RE.match("/abs/path.md")
