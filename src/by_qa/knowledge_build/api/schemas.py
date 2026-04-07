"""Request/response schemas for knowledge build APIs."""

from pydantic import BaseModel, Field

from by_qa.knowledge_common.schemas import KnowledgeItemChunkPayload


class FileToMarkdownRequest(BaseModel):
    """Request body for converting a file to markdown."""

    content: str = Field(min_length=1)
    type: str = Field(min_length=1)


class FileToMarkdownResponse(BaseModel):
    """Business response for file-to-markdown conversion."""

    md_content: str


class BuildMarkdownIndexRequest(BaseModel):
    """Request body for building markdown index."""

    content: str = Field(min_length=1)


class BuildMarkdownIndexResponse(BaseModel):
    """Business response for build-markdown-index."""

    chunks: list[KnowledgeItemChunkPayload]


class FileToMarkdownIndexRequest(BaseModel):
    """Request body for file-to-markdown-index."""

    content: str = Field(min_length=1)
    type: str = Field(min_length=1)


class FileToMarkdownIndexResponse(BaseModel):
    """Business response for file-to-markdown-index."""

    md_content: str
    chunks: list[KnowledgeItemChunkPayload]
