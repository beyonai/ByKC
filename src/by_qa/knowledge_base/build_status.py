"""Central definitions for knowledge-build status and step values."""

from __future__ import annotations

from typing import Final

BUILD_STATUS_COMPLETE: Final[str] = "complete"
BUILD_STATUS_FAILED: Final[str] = "failed"
BUILD_STATUS_RUNNING: Final[str] = "running"
BUILD_STATUS_SKIPPED: Final[str] = "skipped"
BUILD_STATUS_UNSUPPORTED: Final[str] = "unsupported"

BUILD_STEP_MARKDOWN: Final[str] = "markdown"
BUILD_STEP_CHUNKING: Final[str] = "chunking"
BUILD_STEP_VECTORIZING: Final[str] = "vectorizing"
BUILD_STEP_COMPLETE: Final[str] = "complete"

STATUS_DICT: Final[list[dict[str, str]]] = [
    {
        "standCode": BUILD_STATUS_COMPLETE,
        "standDisplayValue": "已完成",
        "standDisplayValueEn": BUILD_STATUS_COMPLETE,
    },
    {
        "standCode": BUILD_STATUS_FAILED,
        "standDisplayValue": "失败",
        "standDisplayValueEn": BUILD_STATUS_FAILED,
    },
    {
        "standCode": BUILD_STATUS_RUNNING,
        "standDisplayValue": "构建中",
        "standDisplayValueEn": BUILD_STATUS_RUNNING,
    },
    {
        "standCode": BUILD_STATUS_SKIPPED,
        "standDisplayValue": "已跳过",
        "standDisplayValueEn": BUILD_STATUS_SKIPPED,
    },
    {
        "standCode": BUILD_STATUS_UNSUPPORTED,
        "standDisplayValue": "不支持构建",
        "standDisplayValueEn": BUILD_STATUS_UNSUPPORTED,
    },
]

STEP_DICT: Final[list[dict[str, str]]] = [
    {
        "standCode": BUILD_STEP_MARKDOWN,
        "standDisplayValue": "原始文件转 Markdown",
        "standDisplayValueEn": BUILD_STEP_MARKDOWN,
    },
    {
        "standCode": BUILD_STEP_CHUNKING,
        "standDisplayValue": "文档切片",
        "standDisplayValueEn": BUILD_STEP_CHUNKING,
    },
    {
        "standCode": BUILD_STEP_VECTORIZING,
        "standDisplayValue": "切片向量化",
        "standDisplayValueEn": BUILD_STEP_VECTORIZING,
    },
    {
        "standCode": BUILD_STEP_COMPLETE,
        "standDisplayValue": "已完成",
        "standDisplayValueEn": BUILD_STEP_COMPLETE,
    },
]


def legacy_build_status(status: str | None) -> str | None:
    """Map the durable lifecycle onto the legacy status vocabulary."""
    normalized = (status or "").lower()
    if normalized in {"pending", "running"}:
        return BUILD_STATUS_RUNNING
    if normalized in {"succeeded", "complete"}:
        return BUILD_STATUS_COMPLETE
    if normalized in {
        BUILD_STATUS_FAILED,
        BUILD_STATUS_SKIPPED,
        BUILD_STATUS_UNSUPPORTED,
    }:
        return normalized
    return status


def legacy_build_step(
    *, status: str | None, current_step: str | None, current_stage: str | None
) -> str | None:
    """Map new milestones onto the four legacy build steps."""
    if legacy_build_status(status) == BUILD_STATUS_COMPLETE:
        return BUILD_STEP_COMPLETE
    normalized_stage = (current_stage or "").lower()
    stage_steps = {
        "accepted": BUILD_STEP_MARKDOWN,
        "extracting": BUILD_STEP_MARKDOWN,
        "chunking": BUILD_STEP_CHUNKING,
        "embedding": BUILD_STEP_VECTORIZING,
        "committing": BUILD_STEP_VECTORIZING,
    }
    return stage_steps.get(normalized_stage, current_step)
