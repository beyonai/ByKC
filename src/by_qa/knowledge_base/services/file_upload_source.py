"""Internal disk-backed upload requests and bounded file operations."""

import asyncio
import hashlib
from pathlib import Path

from pydantic import Field, FilePath, field_validator

from by_qa.knowledge_base.api.schemas import KnowledgeItemUploadRequest

UPLOAD_CHUNK_SIZE = 1024 * 1024


class FileUploadRequest(KnowledgeItemUploadRequest):
    """Internal request; local paths are never accepted from the public API."""

    file_content: FilePath = Field(validation_alias="fileContent")

    @field_validator("file_content")
    @classmethod
    def nonempty_file(cls, value: Path) -> Path:
        if value.stat().st_size == 0:
            raise ValueError("fileContent must not be empty")
        return value


async def file_io(function, *args):
    """Finish an outstanding disk operation before its temporary files are removed."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def file_checksum(source: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        while chunk := stream.read(UPLOAD_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
