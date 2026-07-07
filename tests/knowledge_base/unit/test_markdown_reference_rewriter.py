# tests/knowledge_base/unit/test_markdown_reference_rewriter.py

from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)


async def _exists(found: set[str]):
    async def _check(kb_code: str, resolved: str) -> bool:  # pylint: disable=unused-argument
        return resolved in found

    return _check


async def test_rewrite_image_when_target_exists():
    rewriter = MarkdownReferenceRewriter(
        exists_check=await _exists({"/docs/p/images/x.png"})
    )
    out = await rewriter.rewrite(
        "see ![alt](images/x.png) here", current_dir="/docs/p", kb_code="kb1"
    )
    assert out == "see ![alt](/docs/p/images/x.png) here"


async def test_rewrite_link_when_target_exists_with_anchor_preserved():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists({"/docs/p/a.md"}))
    out = await rewriter.rewrite(
        "go [doc](a.md#sec) now", current_dir="/docs/p", kb_code="kb1"
    )
    assert out == "go [doc](/docs/p/a.md#sec) now"


async def test_rewrite_dotdot_relative():
    rewriter = MarkdownReferenceRewriter(
        exists_check=await _exists({"/docs/p/img/x.png"})
    )
    out = await rewriter.rewrite(
        "![a](../img/x.png)", current_dir="/docs/p/sub", kb_code="kb1"
    )
    assert out == "![a](/docs/p/img/x.png)"


async def test_rewrite_leaves_missing_target_unchanged():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists(set()))
    out = await rewriter.rewrite(
        "![a](missing.png)", current_dir="/docs/p", kb_code="kb1"
    )
    assert out == "![a](missing.png)"


async def test_rewrite_leaves_external_url_unchanged():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists(set()))
    out = await rewriter.rewrite(
        "![a](https://host/x.png)", current_dir="/docs/p", kb_code="kb1"
    )
    assert out == "![a](https://host/x.png)"


async def test_rewrite_leaves_escape_root_unchanged():
    # ../../../x.png from a 2-deep dir escapes the KB root -> normalize_kb_path
    # returns None -> reference left unchanged (even if /x.png exists).
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists({"/x.png"}))
    out = await rewriter.rewrite(
        "![a](../../../x.png)", current_dir="/docs/p", kb_code="kb1"
    )
    assert out == "![a](../../../x.png)"


async def test_rewrite_no_references_returns_unchanged():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists(set()))
    src = "plain text, no refs"
    out = await rewriter.rewrite(src, current_dir="/docs/p", kb_code="kb1")
    assert out == src


async def test_rewrite_link_not_image_form():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists({"/docs/p/b.md"}))
    out = await rewriter.rewrite("[t](b.md)", current_dir="/docs/p", kb_code="kb1")
    assert out == "[t](/docs/p/b.md)"
