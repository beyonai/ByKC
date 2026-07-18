"""Unit tests for Markdown update summaries."""

import asyncio
from types import SimpleNamespace

import pytest

from by_qa.core.model_config import LLMModelProfile
from by_qa.knowledge_base.services.markdown_update_summary_service import (
    MarkdownUpdateSummaryService,
)


def test_rule_summary_describes_size_changes_and_affected_headings():
    """The rule summary reports deterministic document facts in Chinese."""
    service = MarkdownUpdateSummaryService()

    summary = service.build_rule_summary(
        "# 概述\n旧内容\n\n## 范围\n保持不变\n",
        "# 概述\n新内容\n\n## 范围\n保持不变\n\n## 风险\n新增内容\n",
    )

    assert summary == "文档已更新：行数 5→8，字符数 21→33；涉及章节：概述、风险。"


def test_rule_summary_uses_fixed_fallback_without_markdown_structure():
    """Plain text has no stable structural context for a detailed summary."""
    service = MarkdownUpdateSummaryService()

    assert (
        service.build_rule_summary("旧内容\n", "新内容\n") == service.FALLBACK_SUMMARY
    )


def test_rule_summary_ignores_internal_reference_tokens():
    """Reference IDs must not affect counts or become part of the summary."""
    service = MarkdownUpdateSummaryService()

    summary = service.build_rule_summary(
        "# 图示\n[流程图](byqa-ref://12)\n",
        "# 图示\n[流程图](byqa-ref://999)\n",
    )

    assert summary == "文档已更新：行数 2→2，字符数 12→12；涉及章节：无。"
    assert "byqa-ref" not in summary


@pytest.mark.parametrize(
    "output",
    [
        "",
        "  ",
        '{"summary":"更新"}',
        "- 更新了概述",
        "# 更新摘要",
        "x" * 181,
        "Updated the overview section.",
        "已更新 byqa-ref://42 引用。",
        "```markdown\n已更新概述\n```",
        "已更新 **概述** 内容。",
        "> 已更新概述内容。",
        "已更新 <strong>概述</strong> 内容。",
    ],
)
async def test_llm_summary_rejects_unsafe_or_invalid_output(output):
    """Invalid LLM output leaves the deterministic fallback in place."""
    llm = _FakeLLM(output)
    service = MarkdownUpdateSummaryService(llm_service=llm, timeout_seconds=1)

    assert await service.generate_llm_summary("# 概述\n旧", "# 概述\n新") is None
    assert llm.model_types == [LLMModelProfile.STANDARD]


async def test_llm_summary_times_out_and_returns_none():
    """A slow LLM must not delay persistence of the fallback summary."""
    service = MarkdownUpdateSummaryService(
        llm_service=_SlowLLM(), timeout_seconds=0.001
    )

    assert await service.generate_llm_summary("# 概述\n旧", "# 概述\n新") is None


async def test_llm_summary_accepts_a_valid_40_character_chinese_summary():
    """The lower output boundary accepts 40-character Chinese plain text."""
    output = "已" * 40
    service = MarkdownUpdateSummaryService(
        llm_service=_FakeLLM(output), timeout_seconds=1
    )

    assert await service.generate_llm_summary("# 概述\n旧", "# 概述\n新") == output


async def test_llm_summary_rejects_a_39_character_summary():
    """The lower output boundary rejects incomplete short summaries."""
    service = MarkdownUpdateSummaryService(
        llm_service=_FakeLLM("已" * 39), timeout_seconds=1
    )

    assert await service.generate_llm_summary("# 概述\n旧", "# 概述\n新") is None


def test_service_uses_configured_default_timeout(monkeypatch):
    """The configured timeout applies when callers do not override it."""
    monkeypatch.setattr(
        "by_qa.knowledge_base.services.markdown_update_summary_service.get_settings",
        lambda: SimpleNamespace(kb_update_timeline_llm_timeout_seconds=7),
    )

    service = MarkdownUpdateSummaryService(llm_service=_FakeLLM("已" * 40))

    assert service._timeout_seconds == 7


class _FakeLLM:
    def __init__(self, output: str):
        self.output = output
        self.model_types = []
        self.messages = []

    async def generate(self, messages, model_type):
        self.messages.append(messages)
        self.model_types.append(model_type)
        return self.output


class _SlowLLM:
    async def generate(self, messages, model_type):
        assert messages
        assert model_type == LLMModelProfile.STANDARD
        await asyncio.sleep(1)
        return "已更新概述内容，补充风险说明。"
