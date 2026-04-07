"""Shared schemas used across knowledge modules."""

from typing import Optional

from pydantic import BaseModel


class KnowledgeItemChunkPayload(BaseModel):
    """Single chunk payload used for build and ingestion flows."""

    chunk_no: int
    start_line: int
    end_line: int
    chunk_text: str
    embedding: list[float]
    char_start: Optional[int] = None
    char_end: Optional[int] = None
