"""Central definitions for knowledge-build status and step values."""

from __future__ import annotations

from typing import Final

BUILD_STATUS_COMPLETE: Final[str] = "complete"
BUILD_STATUS_FAILED: Final[str] = "failed"
BUILD_STATUS_RUNNING: Final[str] = "running"

BUILD_STEP_MARKDOWN: Final[str] = "markdown"
BUILD_STEP_CHUNKING: Final[str] = "chunking"
BUILD_STEP_VECTORIZING: Final[str] = "vectorizing"

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
]
