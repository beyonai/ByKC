# tests/knowledge_common/test_markdown_reference.py

from by_qa.knowledge_common.markdown_reference import (
    URL_SCHEME_RE,
    detect_reference_spans,
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
