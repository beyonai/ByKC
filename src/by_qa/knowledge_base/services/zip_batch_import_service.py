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
from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)
from by_qa.knowledge_common.kb_path_utils import normalize_kb_path

logger = logging.getLogger(__name__)

_MD_SUFFIXES = (".md", ".markdown")
_MAX_TOTAL_UNCOMPRESSED = 1024 * 1024 * 1024  # 1 GiB
_MAX_ENTRIES = 10000
_CHUNK = 64 * 1024


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

        # Dedup entries by resolved KB path: keep the first occurrence of each
        # resolved path; later duplicates are recorded as failures and skipped.
        # Unsafe entries (resolved is None) are NOT duplicates — they still go
        # through _import_one to be recorded as unsafe (preserve behavior).
        seen: set[str] = set()
        non_md: list[tuple[str, bytes]] = []
        md: list[tuple[str, bytes]] = []
        duplicate_results: list[ImportItem] = []
        for name, data in entries:
            resolved = _resolve_within_target(normalized_target, name)
            if resolved is None:
                (md if name.lower().endswith(_MD_SUFFIXES) else non_md).append(
                    (name, data)
                )
                continue
            if resolved in seen:
                duplicate_results.append(
                    ImportItem(
                        file_path=resolved, success=False, error="duplicate path in zip"
                    )
                )
                continue
            seen.add(resolved)
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

        limit = max(1, max_concurrency or self.max_concurrency)
        sem = asyncio.Semaphore(limit)

        # Phase 1: non-md concurrent.
        non_md_results = await self._run_phase(
            non_md,
            sem=sem,
            kb_code=kb_code,
            target_dir=normalized_target,
            process_front_matter=process_front_matter,
            file_description=file_description,
            rewriter=None,
        )

        # Drop failed phase-1 paths from batch_paths so md references to a
        # non-md sibling that failed to upload are not rewritten to a dangling
        # KB-absolute path.
        for item in non_md_results:
            if not item.success and item.file_path in batch_paths:
                batch_paths.discard(item.file_path)

        # Now construct the rewriter with the batch-aware closure reflecting
        # phase-1 successes.
        async def exists_check(kb_code_: str, paths: frozenset[str]) -> frozenset[str]:
            in_batch = frozenset(p for p in paths if p in batch_paths)
            rest = paths - in_batch
            if not rest:
                return in_batch
            return in_batch | frozenset(
                await self.ingestion_service.files_exist(kb_code_, rest)
            )

        rewriter = MarkdownReferenceRewriter(exists_check=exists_check)

        # Phase 2: md concurrent after barrier.
        md_results = await self._run_phase(
            md,
            sem=sem,
            kb_code=kb_code,
            target_dir=normalized_target,
            process_front_matter=process_front_matter,
            file_description=file_description,
            rewriter=rewriter,
        )

        results = list(non_md_results) + list(md_results) + duplicate_results
        succeeded = sum(1 for r in results if r.success)
        return ZipBatchImportResult(
            data=results,
            summary=ImportSummary(
                total=len(results),
                succeeded=succeeded,
                failed=len(results) - succeeded,
            ),
        )

    async def _run_phase(
        self,
        group: list[tuple[str, bytes]],
        *,
        sem: asyncio.Semaphore,
        kb_code: str,
        target_dir: str,
        process_front_matter: bool,
        file_description: str | None,
        rewriter: MarkdownReferenceRewriter | None,
    ) -> list[ImportItem]:
        async def one(name: str, data: bytes) -> ImportItem:
            async with sem:
                return await self._import_one(
                    kb_code=kb_code,
                    target_dir=target_dir,
                    name=name,
                    data=data,
                    process_front_matter=process_front_matter,
                    file_description=file_description,
                    rewriter=rewriter,
                )

        return await asyncio.gather(*(one(n, d) for n, d in group))

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
            if len(out) >= _MAX_ENTRIES:
                raise ValueError("zip too large")
            # Stream-decompress and cap ACTUAL uncompressed bytes, not the
            # (spoofable) header-declared file_size, to defend against zip
            # bombs that declare small sizes but decompress to gigabytes.
            buf = bytearray()
            with zf.open(name) as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    total += len(chunk)
                    if total > _MAX_TOTAL_UNCOMPRESSED:
                        raise ValueError("zip too large")
            out.append((name, bytes(buf)))
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
        rewriter: MarkdownReferenceRewriter | None,
    ) -> ImportItem:
        resolved = _resolve_within_target(target_dir, name)
        if resolved is None:
            reported = "/" + "/".join(
                p for p in (target_dir.split("/") + name.split("/")) if p
            )
            return ImportItem(file_path=reported, success=False, error="unsafe path")
        file_path = resolved
        try:
            # Prepare content BEFORE deleting the existing file: a malformed md
            # (UnicodeDecodeError) or rewrite failure must NOT lose the original.
            content = data
            if name.lower().endswith(_MD_SUFFIXES):
                current_dir = "/".join(resolved.split("/")[:-1]) or "/"
                rewritten = await rewriter.rewrite(
                    data.decode("utf-8"), current_dir, kb_code
                )
                content = rewritten.encode("utf-8")
            if await self.ingestion_service.file_exists(kb_code, file_path):
                await self.ingestion_service.delete_knowledge_item(
                    DeleteKnowledgeItemRequest(kb_code=kb_code, file_path=file_path)
                )
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
