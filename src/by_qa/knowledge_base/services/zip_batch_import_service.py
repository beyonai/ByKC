# src/by_qa/knowledge_base/services/zip_batch_import_service.py
"""Batch import a zip archive into a knowledge base directory.

Two-phase concurrent upload: non-markdown files first (phase 1), then a
barrier, then markdown files (phase 2) after rewriting their image/link
references to KB-absolute paths. For zip uploads, an already-existing file
is soft-deleted before re-upload (overwrite semantics).
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    KnowledgeItemUploadRequest,
)
from by_qa.knowledge_common.kb_path_utils import normalize_kb_path

logger = logging.getLogger(__name__)

_MD_SUFFIXES = (".md", ".markdown")
_MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024  # 1 GiB
_MAX_ENTRIES = 10000


class ImportItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_path: str = Field(serialization_alias="filePath")
    success: bool
    error: str | None = None


class ImportSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    succeeded: int
    failed: int


@dataclass
class ZipBatchImportResult:
    data: list[ImportItem]
    summary: ImportSummary


def _is_junk_segment(seg: str) -> bool:
    # `..` is a relative component, not junk — let path resolution flag it as
    # unsafe if it escapes the target dir. macOS metadata starts with `.`.
    if seg == "..":
        return False
    return seg == "__MACOSX" or seg.startswith(".")


def _resolve_within_target(target_dir: str, name: str) -> str | None:
    """Resolve `name` under `target_dir`; None if it escapes the target dir or KB root."""
    resolved = normalize_kb_path("/" + target_dir, name)
    if resolved is None:
        return None
    target_abs = "/" + target_dir if target_dir else ""
    if target_abs == "":
        return resolved
    if resolved == target_abs or resolved.startswith(target_abs + "/"):
        return resolved
    return None


@dataclass
class ZipBatchImportService:
    ingestion_service: object
    max_concurrency: int = 8

    def __post_init__(self) -> None:
        from by_qa.knowledge_base.services.markdown_reference_rewriter import (
            MarkdownReferenceRewriter,
        )

        self._rewriter_factory = MarkdownReferenceRewriter

    async def import_zip(
        self,
        *,
        kb_code: str,
        target_dir: str,
        zip_bytes: bytes,
        process_front_matter: bool = True,
        file_description: str | None = None,
        max_concurrency: int | None = None,
    ) -> ZipBatchImportResult:
        normalized_target = (target_dir or "").strip("/") or ""
        entries = self._extract_entries(zip_bytes)

        non_md: list[tuple[str, bytes]] = []
        md: list[tuple[str, bytes]] = []
        for name, data in entries:
            (md if name.lower().endswith(_MD_SUFFIXES) else non_md).append((name, data))

        # All resolved paths in this batch. A reference to a sibling file that is
        # also in the zip (including md->md) is considered to exist even before
        # that file is uploaded, so concurrent phase-2 md uploads resolve
        # intra-batch references regardless of upload order.
        batch_paths: set[str] = set()
        for name, _ in non_md + md:
            resolved = _resolve_within_target(normalized_target, name)
            if resolved is not None:
                batch_paths.add(resolved)

        async def exists_check(kb_code_: str, full_path: str) -> bool:
            if full_path in batch_paths:
                return True
            return await self.ingestion_service.file_exists(kb_code_, full_path)

        self._rewriter = self._rewriter_factory(exists_check=exists_check)

        limit = max(1, max_concurrency or self.max_concurrency)
        sem = asyncio.Semaphore(limit)

        async def run_phase(group: list[tuple[str, bytes]]) -> list[ImportItem]:
            async def one(name: str, data: bytes) -> ImportItem:
                async with sem:
                    return await self._import_one(
                        kb_code=kb_code,
                        target_dir=normalized_target,
                        name=name,
                        data=data,
                        process_front_matter=process_front_matter,
                        file_description=file_description,
                    )

            return await asyncio.gather(*(one(n, d) for n, d in group))

        # Phase 1: non-md concurrent; Phase 2: md concurrent after barrier.
        non_md_results = await run_phase(non_md)
        md_results = await run_phase(md)

        results = list(non_md_results) + list(md_results)
        succeeded = sum(1 for r in results if r.success)
        return ZipBatchImportResult(
            data=results,
            summary=ImportSummary(
                total=len(results),
                succeeded=succeeded,
                failed=len(results) - succeeded,
            ),
        )

    def _extract_entries(self, zip_bytes: bytes) -> list[tuple[str, bytes]]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid zip file") from exc
        total = 0
        out: list[tuple[str, bytes]] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            segments = name.split("/")
            if any(_is_junk_segment(seg) for seg in segments):
                continue
            total += info.file_size
            if total > _MAX_TOTAL_UNCOMPRESSED or len(out) >= _MAX_ENTRIES:
                raise ValueError("zip too large")
            out.append((name, zf.read(name)))
        return out

    async def _import_one(
        self,
        *,
        kb_code: str,
        target_dir: str,
        name: str,
        data: bytes,
        process_front_matter: bool,
        file_description: str | None,
    ) -> ImportItem:
        resolved = _resolve_within_target(target_dir, name)
        if resolved is None:
            reported = "/" + "/".join(
                p for p in (target_dir.split("/") + name.split("/")) if p
            )
            return ImportItem(file_path=reported, success=False, error="unsafe path")
        file_path = resolved
        try:
            if await self.ingestion_service.file_exists(kb_code, file_path):
                await self.ingestion_service.delete_knowledge_item(
                    DeleteKnowledgeItemRequest(kb_code=kb_code, file_path=file_path)
                )
            content = data
            if name.lower().endswith(_MD_SUFFIXES):
                current_dir = "/".join(resolved.split("/")[:-1]) or "/"
                rewritten = await self._rewriter.rewrite(
                    data.decode("utf-8"), current_dir, kb_code
                )
                content = rewritten.encode("utf-8")
            request = KnowledgeItemUploadRequest(
                kb_code=kb_code,
                file_path=file_path,
                file_description=file_description,
                file_content=content,
                process_front_matter=process_front_matter,
            )
            await self.ingestion_service.upload_file(request)
            return ImportItem(file_path=file_path, success=True, error=None)
        except Exception as exc:
            logger.warning("zip batch import failed: path=%s error=%s", file_path, exc)
            return ImportItem(file_path=file_path, success=False, error=str(exc))
