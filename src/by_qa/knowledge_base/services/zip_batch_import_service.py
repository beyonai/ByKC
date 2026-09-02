# src/by_qa/knowledge_base/services/zip_batch_import_service.py
"""Batch import a zip archive into a knowledge base directory.

Two-phase concurrent upload: non-markdown files first (phase 1), then a
barrier, then markdown files (phase 2). Markdown reference tokenization is
owned by the ingestion service so zip and single-file uploads share the same
transactional behavior. For zip uploads, an already-existing file is
soft-deleted before re-upload (overwrite semantics).
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from by_qa.knowledge_base.api.schemas import (
    CreateDirectoryRequest,
    DeleteKnowledgeItemRequest,
    KnowledgeItemUploadRequest,
)
from by_qa.knowledge_common.kb_path_utils import normalize_kb_path

logger = logging.getLogger(__name__)

_MD_SUFFIXES = (".md", ".markdown")
# Bounds resident memory: entries are still held in `out` (decompressed in
# full) before upload. Full streaming (extract-one-upload-one via tempdir) is a
# future improvement, but these caps bound the worst case.
_MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024  # 256 MiB across all entries
_MAX_ENTRY_UNCOMPRESSED = 64 * 1024 * 1024  # 64 MiB per single entry
_MAX_ENTRIES = 10000
_CHUNK = 64 * 1024


class ImportItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    file_path: str = Field(serialization_alias="filePath")
    success: bool
    error: str | None = None
    resource_type: Literal["file", "directory"] = Field(default="file", exclude=True)


class ImportSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    succeeded: int
    failed: int


@dataclass
class ZipBatchImportResult:
    data: list[ImportItem]
    summary: ImportSummary
    post_process_errors: list[str] = dataclass_field(default_factory=list)


@dataclass
class _ImportedItem:
    item: ImportItem
    upload_row: dict[str, Any] | None = None


@dataclass(frozen=True)
class _ZipEntry:
    name: str
    data: bytes | None
    is_directory: bool


def _is_junk_segment(seg: str) -> bool:
    # `..` is a relative component, not junk — let path resolution flag it as
    # unsafe if it escapes the target dir. macOS metadata starts with `.`.
    if seg == "..":
        return False
    return seg == "__MACOSX" or seg.startswith(".")


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Decode a zip entry filename, recovering GBK names without the UTF-8 flag.

    Python's ``zipfile`` decodes entry names as UTF-8 when flag bit 0x800 is set
    (which ``zipfile`` itself sets when writing), and as CP437 otherwise. Zips
    produced by Windows Explorer / WinRAR / 好压 on Chinese Windows encode
    filenames as GBK *without* setting the flag, so ``info.filename`` is CP437
    mojibake (e.g. ``中文文档`` → ``╓╨╬─╬─╡╡``). Re-encode the CP437 string back
    to bytes and decode as GBK to recover the real name. Pure-ASCII names are
    left untouched (no high-bit bytes → no mojibake).
    """
    name = info.filename
    if info.flag_bits & 0x800:
        return name  # UTF-8; zipfile already decoded it correctly.
    if name.isascii():
        return name  # all bytes were 0x00-0x7F; cp437 == ascii here.
    try:
        return name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            return name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return name  # give up; keep the cp437 decode


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
    ingestion_service: Any
    directory_service: Any | None = None
    max_concurrency: int = 8

    async def import_zip(
        self,
        *,
        kb_code: str,
        target_dir: str,
        zip_bytes: bytes,
        process_front_matter: bool = True,
        file_description: str | None = None,
        skip_if_duplicate: bool = False,
        metadata: dict[str, Any] | None = None,
        max_concurrency: int | None = None,
    ) -> ZipBatchImportResult:
        normalized_target = (target_dir or "").strip("/") or ""
        entries = self._extract_entries(zip_bytes)

        # Dedup entries by resolved KB path: keep the first occurrence of each
        # resolved path; later duplicates are recorded as failures and skipped.
        # Unsafe entries (resolved is None) are NOT duplicates — they still go
        # through their importer to be recorded as unsafe.
        seen: set[str] = set()
        directories: list[_ZipEntry] = []
        non_md: list[tuple[str, bytes]] = []
        md: list[tuple[str, bytes]] = []
        duplicate_results: list[ImportItem] = []
        for entry in entries:
            resolved = _resolve_within_target(normalized_target, entry.name)
            if resolved is None:
                if entry.is_directory:
                    directories.append(entry)
                else:
                    group = md if entry.name.lower().endswith(_MD_SUFFIXES) else non_md
                    group.append((entry.name, entry.data or b""))
                continue
            if resolved in seen:
                duplicate_results.append(
                    ImportItem(
                        file_path=resolved,
                        success=False,
                        error="duplicate path in zip",
                        resource_type=("directory" if entry.is_directory else "file"),
                    )
                )
                continue
            seen.add(resolved)
            if entry.is_directory:
                directories.append(entry)
            else:
                group = md if entry.name.lower().endswith(_MD_SUFFIXES) else non_md
                group.append((entry.name, entry.data or b""))

        directory_imports = await self._import_directories(
            directories,
            kb_code=kb_code,
            target_dir=normalized_target,
            directory_description=file_description,
            metadata=metadata,
        )

        limit = max(1, max_concurrency or self.max_concurrency)
        sem = asyncio.Semaphore(limit)

        # Phase 1: non-md concurrent.
        non_md_imports = await self._run_phase(
            non_md,
            sem=sem,
            kb_code=kb_code,
            target_dir=normalized_target,
            process_front_matter=process_front_matter,
            file_description=file_description,
            skip_if_duplicate=skip_if_duplicate,
            metadata=metadata,
        )

        # Phase 2: md concurrent after barrier.
        md_imports = await self._run_phase(
            md,
            sem=sem,
            kb_code=kb_code,
            target_dir=normalized_target,
            process_front_matter=process_front_matter,
            file_description=file_description,
            skip_if_duplicate=skip_if_duplicate,
            metadata=metadata,
        )

        file_imports = list(non_md_imports) + list(md_imports)
        imports = directory_imports + file_imports
        results = [uploaded.item for uploaded in imports] + duplicate_results
        upload_rows = [
            uploaded.upload_row
            for uploaded in imports
            if uploaded.item.success and uploaded.upload_row is not None
        ]
        post_process_errors: list[str] = []
        compensation_failure = await self._resolve_pending_references_after_batch(
            kb_code=kb_code,
            file_paths=[
                imported.item.file_path
                for imported in file_imports
                if imported.item.success
            ],
            uploaded_rows=upload_rows,
        )
        if compensation_failure is not None:
            post_process_errors.append(compensation_failure)
        succeeded = sum(1 for r in results if r.success)
        return ZipBatchImportResult(
            data=results,
            summary=ImportSummary(
                total=len(results),
                succeeded=succeeded,
                failed=len(results) - succeeded,
            ),
            post_process_errors=post_process_errors,
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
        skip_if_duplicate: bool,
        metadata: dict[str, Any] | None = None,
    ) -> list[_ImportedItem]:
        async def one(name: str, data: bytes) -> _ImportedItem:
            async with sem:
                return await self._import_one(
                    kb_code=kb_code,
                    target_dir=target_dir,
                    name=name,
                    data=data,
                    process_front_matter=process_front_matter,
                    file_description=file_description,
                    skip_if_duplicate=skip_if_duplicate,
                    metadata=metadata,
                )

        return await asyncio.gather(*(one(n, d) for n, d in group))

    async def _resolve_pending_references_after_batch(
        self,
        *,
        kb_code: str,
        file_paths: list[str],
        uploaded_rows: list[dict[str, Any]],
    ) -> str | None:
        if not file_paths and not uploaded_rows:
            return None
        try:
            await self.ingestion_service.resolve_pending_references_for_paths(
                kb_code=kb_code,
                file_paths=file_paths,
                uploaded_rows=uploaded_rows or None,
            )
        except Exception as exc:
            logger.exception(
                "zip batch reference compensation failed: kb_code=%s", kb_code
            )
            return f"batch reference compensation failed: {exc}"
        return None

    def _extract_entries(self, zip_bytes: bytes) -> list[_ZipEntry]:
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise ValueError("invalid zip file") from exc
        total = 0
        out: list[_ZipEntry] = []
        for info in zf.infolist():
            name = _decode_zip_name(info)
            segments = name.split("/")
            if any(_is_junk_segment(seg) for seg in segments):
                continue
            if len(out) >= _MAX_ENTRIES:
                raise ValueError("zip too large")
            if info.is_dir():
                out.append(_ZipEntry(name=name, data=None, is_directory=True))
                continue
            # Stream-decompress and cap ACTUAL uncompressed bytes, not the
            # (spoofable) header-declared file_size, to defend against zip
            # bombs that declare small sizes but decompress to gigabytes.
            # Open by ZipInfo object (not the decoded name string) so zipfile
            # does not try to match the (possibly re-decoded) name against the
            # raw entry name.
            buf = bytearray()
            with zf.open(info) as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    total += len(chunk)
                    # Per-entry cap on ACTUAL decompressed bytes (not the
                    # spoofable header file_size) bounds a single bomb entry.
                    if len(buf) > _MAX_ENTRY_UNCOMPRESSED:
                        raise ValueError("zip too large")
                    if total > _MAX_TOTAL_UNCOMPRESSED:
                        raise ValueError("zip too large")
            out.append(_ZipEntry(name=name, data=bytes(buf), is_directory=False))
        return out

    async def _import_directories(
        self,
        entries: list[_ZipEntry],
        *,
        kb_code: str,
        target_dir: str,
        directory_description: str | None,
        metadata: dict[str, Any] | None,
    ) -> list[_ImportedItem]:
        """Create explicit directories, using leaf paths on the common fast path."""
        ordered = sorted(entries, key=lambda item: item.name.count("/"))
        if directory_description is not None or metadata is not None:
            return [
                await self._import_directory(
                    kb_code=kb_code,
                    target_dir=target_dir,
                    name=entry.name,
                    directory_description=directory_description,
                    metadata=metadata,
                )
                for entry in ordered
            ]

        resolved_entries = [
            (entry, _resolve_within_target(target_dir, entry.name)) for entry in ordered
        ]
        valid = [(entry, path) for entry, path in resolved_entries if path is not None]
        entries_by_path = {path: entry for entry, path in valid}
        non_leaves: set[str] = set()
        for path in entries_by_path:
            parent = path.rpartition("/")[0]
            while parent:
                if parent in entries_by_path:
                    non_leaves.add(parent)
                parent = parent.rpartition("/")[0]
        leaves = [(entry, path) for entry, path in valid if path not in non_leaves]
        outcomes: dict[str, _ImportedItem] = {}

        def explicit_branch(path: str) -> list[tuple[_ZipEntry, str]]:
            branch: list[tuple[_ZipEntry, str]] = []
            current = path
            while current:
                entry = entries_by_path.get(current)
                if entry is not None:
                    branch.append((entry, current))
                current = current.rpartition("/")[0]
            branch.reverse()
            return branch

        for entry, path in leaves:
            leaf_result = await self._import_directory(
                kb_code=kb_code,
                target_dir=target_dir,
                name=entry.name,
                directory_description=None,
                metadata=None,
            )
            if leaf_result.item.success:
                for _, ancestor_path in explicit_branch(path):
                    outcomes.setdefault(
                        ancestor_path,
                        _ImportedItem(
                            item=ImportItem(
                                file_path=ancestor_path,
                                success=True,
                                error=None,
                                resource_type="directory",
                            )
                        ),
                    )
                continue

            # A failed recursive leaf transaction may still have valid explicit
            # ancestors. Re-run that branch shallow-to-deep so successful
            # parents retain the same partial-success semantics as before.
            for branch_entry, branch_path in explicit_branch(path):
                if branch_path in outcomes:
                    continue
                outcomes[branch_path] = await self._import_directory(
                    kb_code=kb_code,
                    target_dir=target_dir,
                    name=branch_entry.name,
                    directory_description=None,
                    metadata=None,
                )

        imports: list[_ImportedItem] = []
        for entry, path in resolved_entries:
            if path is None:
                imports.append(
                    await self._import_directory(
                        kb_code=kb_code,
                        target_dir=target_dir,
                        name=entry.name,
                        directory_description=None,
                        metadata=None,
                    )
                )
            else:
                imports.append(outcomes[path])
        return imports

    async def _import_directory(
        self,
        *,
        kb_code: str,
        target_dir: str,
        name: str,
        directory_description: str | None,
        metadata: dict[str, Any] | None,
    ) -> _ImportedItem:
        resolved = _resolve_within_target(target_dir, name)
        if resolved is None:
            reported = "/" + "/".join(
                p for p in (target_dir.split("/") + name.split("/")) if p
            )
            return _ImportedItem(
                item=ImportItem(
                    file_path=reported,
                    success=False,
                    error="unsafe path",
                    resource_type="directory",
                )
            )

        service = self.directory_service or self.ingestion_service
        try:
            await service.create_directory(
                CreateDirectoryRequest(
                    kb_code=kb_code,
                    directory_path=resolved,
                    directory_description=directory_description,
                    metadata=metadata,
                )
            )
            return _ImportedItem(
                item=ImportItem(
                    file_path=resolved,
                    success=True,
                    error=None,
                    resource_type="directory",
                )
            )
        except Exception as exc:
            logger.warning(
                "zip batch directory import failed: path=%s error=%s", resolved, exc
            )
            return _ImportedItem(
                item=ImportItem(
                    file_path=resolved,
                    success=False,
                    error=str(exc),
                    resource_type="directory",
                )
            )

    async def _import_one(
        self,
        *,
        kb_code: str,
        target_dir: str,
        name: str,
        data: bytes,
        process_front_matter: bool,
        file_description: str | None,
        skip_if_duplicate: bool,
        metadata: dict[str, Any] | None = None,
    ) -> _ImportedItem:
        resolved = _resolve_within_target(target_dir, name)
        if resolved is None:
            reported = "/" + "/".join(
                p for p in (target_dir.split("/") + name.split("/")) if p
            )
            return _ImportedItem(
                item=ImportItem(file_path=reported, success=False, error="unsafe path")
            )
        file_path = resolved
        try:
            # Validate markdown bytes BEFORE deleting the existing file: a
            # malformed md (UnicodeDecodeError) must NOT lose the original.
            if name.lower().endswith(_MD_SUFFIXES):
                data.decode("utf-8")
            if skip_if_duplicate:
                duplicate_path = await self.ingestion_service.find_duplicate_file(
                    kb_code=kb_code,
                    file_content=data,
                )
                if duplicate_path is not None:
                    return _ImportedItem(
                        item=ImportItem(
                            file_path=file_path,
                            success=False,
                            error=(
                                "duplicate file checksum in knowledge base: "
                                f"{duplicate_path}"
                            ),
                        )
                    )
            if await self.ingestion_service.file_exists(kb_code, file_path):
                await self.ingestion_service.delete_knowledge_item(
                    DeleteKnowledgeItemRequest(kb_code=kb_code, file_path=file_path)
                )
            request = KnowledgeItemUploadRequest(
                kb_code=kb_code,
                file_path=file_path,
                file_description=file_description,
                file_content=data,
                process_front_matter=process_front_matter,
                skip_if_duplicate=skip_if_duplicate,
                metadata=metadata,
            )
            upload_row = await self.ingestion_service.upload_file(request)
            return _ImportedItem(
                item=ImportItem(file_path=file_path, success=True, error=None),
                upload_row=dict(upload_row) if isinstance(upload_row, dict) else None,
            )
        except Exception as exc:
            logger.warning("zip batch import failed: path=%s error=%s", file_path, exc)
            return _ImportedItem(
                item=ImportItem(file_path=file_path, success=False, error=str(exc))
            )
