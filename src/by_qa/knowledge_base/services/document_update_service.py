"""Transactional replacement of an existing knowledge-base document."""

from __future__ import annotations

import hashlib
import mimetypes
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import DocumentUpdateRequest
from by_qa.knowledge_base.build_status import BUILD_STATUS_RUNNING
from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.metadata_types import (
    infer_metadata_value_type,
    normalize_metadata_value,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.markdown_front_matter import (
    parse_front_matter,
    split_front_matter,
)


@dataclass(frozen=True)
class DocumentUpdateResult:
    """Data needed by the caller to optionally request an LLM timeline summary."""

    timeline_id: int
    is_markdown: bool
    old_markdown_context: str | None = None
    new_markdown_context: str | None = None


@dataclass(frozen=True)
class GeneratedOutgoingAssertion:
    """Internal structured relation emitted together with a document update."""

    target_fs_entry_id: int
    relation_code: str
    original_target: str
    discovered_by: str
    confidence: float | None = None
    definition_version: str | None = None
    source_task_id: int | None = None
    evidence_fingerprint: str | None = None
    producer_run_id: str | None = None


@dataclass
class DocumentUpdateService:
    """Replace file bytes while atomically invalidating all derived state."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: Any
    knowledge_fs_entry_repository: Any
    knowledge_item_chunk_repository: Any
    retrieval_projection_repository: Any
    knowledge_build_task_repository: Any
    knowledge_fetch_cache_repository: Any
    file_metadata_value_repository: Any
    knowledge_file_reference_repository: Any
    markdown_reference_rewriter: Any
    storage_provider: Any
    update_timeline_repository: Any
    markdown_update_summary_service: Any

    MAX_MARKDOWN_CONTEXT_CHARS = 12_000
    FIXED_SUMMARY = "文件内容已更新。"

    async def update_file(
        self,
        request: DocumentUpdateRequest,
        *,
        generated_outgoing_assertions: Sequence[GeneratedOutgoingAssertion] = (),
        producer_run_id: str | None = None,
    ) -> DocumentUpdateResult:
        """Replace a file's original object and reset its indexing state.

        The original object's locator is deliberately stable.  If the database
        transaction fails after storage is overwritten, the exact original bytes
        are restored to that same locator before surfacing the database failure.
        """
        normalized_path = request.file_path.strip("/")
        update_run_id = producer_run_id or uuid.uuid4().hex
        update_started_at = time.perf_counter()
        connection = await self.connection_factory()
        knowledge_base_id: int | None = None
        fs_entry_id: int | None = None
        is_markdown: bool | None = None
        deleted_assertion_count = 0
        old_bytes: bytes | None = None
        original_location: StorageLocation | None = None
        wrote_original = False
        committed = False
        logger.info(
            "document update started: kb_code=%s file_path=%s update_run_id=%s generated_assertion_count=%s skip_if_duplicate=%s process_front_matter=%s",
            request.kb_code,
            normalized_path,
            update_run_id,
            len(generated_outgoing_assertions),
            request.skip_if_duplicate,
            request.process_front_matter,
        )
        try:
            cursor = connection.cursor()
            kb_row = await self.knowledge_base_repository.get_by_code(
                cursor, request.kb_code
            )
            if not kb_row:
                raise KnowledgeBaseValidationError(
                    f"knowledge base not found: {request.kb_code}"
                )
            knowledge_base_id = self._row_id(kb_row)
            file_row = (
                await self.knowledge_fs_entry_repository.get_file_by_path_for_update(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    full_path=normalized_path,
                )
            )
            if not file_row:
                raise KnowledgeBaseValidationError(
                    f"file not found: {request.file_path}"
                )
            fs_entry_id = self._row_id(file_row)
            if (
                request.refer_signature is not None
                and file_row.get("checksum") != request.refer_signature
            ):
                raise KnowledgeBaseValidationError(
                    "file signature mismatch: "
                    f"expected {request.refer_signature}, "
                    f"current {file_row.get('checksum') or ''}"
                )
            latest_task = (
                await self.knowledge_build_task_repository.get_latest_by_fs_entry_id(
                    cursor, fs_entry_id=fs_entry_id
                )
            )
            if latest_task and latest_task.get("status") == BUILD_STATUS_RUNNING:
                raise KnowledgeBaseValidationError(
                    f"File is being built and cannot be updated: {request.file_path}"
                )

            original_location = self._original_location(file_row, request.file_path)
            old_bytes = await self.storage_provider.read(original_location)
            mime_type = self._guess_mime_type(normalized_path)
            is_markdown = self._is_markdown(normalized_path, mime_type)
            old_markdown = old_bytes.decode("utf-8") if is_markdown else None
            final_bytes = request.file_content

            if is_markdown:
                _, final_bytes = split_front_matter(final_bytes)
                materialized = (
                    await self.markdown_reference_rewriter.materialize_existing_tokens(
                        final_bytes.decode("utf-8"),
                        cursor=cursor,
                        reference_repository=self.knowledge_file_reference_repository,
                    )
                )
                final_bytes = materialized.encode("utf-8")

            deleted_assertions = await self.knowledge_file_reference_repository.delete_outgoing_for_source_fs_entry_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=fs_entry_id,
            )
            deleted_assertion_count = len(deleted_assertions or ())

            if is_markdown:
                final_bytes = await self._rewrite_markdown(
                    final_bytes,
                    cursor=cursor,
                    knowledge_base_id=knowledge_base_id,
                    fs_entry_id=fs_entry_id,
                    file_path=normalized_path,
                    producer_run_id=update_run_id,
                )
                new_markdown = final_bytes.decode("utf-8")
                summary = self.markdown_update_summary_service.build_rule_summary(
                    old_markdown, new_markdown
                )
                summary_source = "RULE_BASED"
            else:
                new_markdown = None
                summary = self.FIXED_SUMMARY
                summary_source = "FIXED"

            await self._write_generated_assertions(
                cursor,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=fs_entry_id,
                assertions=generated_outgoing_assertions,
                default_producer_run_id=update_run_id,
            )

            checksum = hashlib.sha256(final_bytes).hexdigest()
            await self.knowledge_fs_entry_repository.lock_checksum_scope(
                cursor,
                knowledge_base_id=knowledge_base_id,
                checksum=checksum,
            )
            if request.skip_if_duplicate:
                duplicate = (
                    await self.knowledge_fs_entry_repository.get_file_by_checksum(
                        cursor,
                        knowledge_base_id=knowledge_base_id,
                        checksum=checksum,
                        exclude_fs_entry_id=fs_entry_id,
                    )
                )
                if duplicate is not None:
                    raise KnowledgeBaseValidationError(
                        "duplicate file checksum in knowledge base: "
                        f"{duplicate['virtual_path']}"
                    )

            # Do this before changing durable derived state. A write failure is
            # rolled back without any committed DB mutation.
            await self.storage_provider.write(
                original_location, final_bytes, content_type=mime_type
            )
            wrote_original = True

            await self.knowledge_item_chunk_repository.delete_for_fs_entry(
                cursor, fs_entry_id=fs_entry_id
            )
            await self.retrieval_projection_repository.delete_for_fs_entry_ids(
                cursor, knowledge_base_id=knowledge_base_id, fs_entry_ids=[fs_entry_id]
            )
            await self.knowledge_build_task_repository.delete_for_fs_entry_id(
                cursor, fs_entry_id=fs_entry_id
            )
            await self.knowledge_fetch_cache_repository.delete_cache_entries_for_fs_entry_ids(
                cursor, fs_entry_ids=[fs_entry_id]
            )
            await self.knowledge_fs_entry_repository.clear_markdown_metadata(
                cursor, fs_entry_id=fs_entry_id
            )
            if is_markdown and request.process_front_matter:
                await self._apply_front_matter(
                    cursor,
                    fs_entry_id=fs_entry_id,
                    knowledge_base_id=knowledge_base_id,
                    content=request.file_content,
                )
            await self.knowledge_file_reference_repository.resolve_pending_for_path(
                cursor,
                knowledge_base_id=knowledge_base_id,
                target_path="/" + normalized_path,
                target_fs_entry_id=fs_entry_id,
            )

            await self.knowledge_fs_entry_repository.update_file_entry_for_update(
                cursor,
                fs_entry_id=fs_entry_id,
                file_description=request.file_description,
                description_provided="file_description" in request.model_fields_set,
                original_location=original_location,
                file_size=len(final_bytes),
                mime_type=mime_type,
                checksum=checksum,
            )
            timeline = await self.update_timeline_repository.create_update_event(
                cursor,
                knowledge_base_id=knowledge_base_id,
                fs_entry_id=fs_entry_id,
                old_checksum=file_row.get("checksum"),
                new_checksum=checksum,
                old_file_size=file_row.get("file_size"),
                new_file_size=len(final_bytes),
                summary=summary,
                summary_source=summary_source,
            )
            if not timeline:
                raise RuntimeError("failed to create document update timeline event")
            await connection.commit()
            committed = True

            old_sidecar = self._markdown_location(file_row)
            if old_sidecar is not None:
                await self.storage_provider.delete_quietly(old_sidecar)
            timeline_id = self._row_id(timeline)
            logger.info(
                "document update completed: kb_id=%s source_id=%s file_path=%s update_run_id=%s timeline_id=%s markdown=%s file_size=%s deleted_assertion_count=%s generated_assertion_count=%s elapsed_ms=%.2f",
                knowledge_base_id,
                fs_entry_id,
                normalized_path,
                update_run_id,
                timeline_id,
                is_markdown,
                len(final_bytes),
                deleted_assertion_count,
                len(generated_outgoing_assertions),
                (time.perf_counter() - update_started_at) * 1000,
            )
            return DocumentUpdateResult(
                timeline_id=timeline_id,
                is_markdown=is_markdown,
                old_markdown_context=self._bounded_context(old_markdown),
                new_markdown_context=self._bounded_context(new_markdown),
            )
        except Exception:
            logger.exception(
                "document update failed: kb_code=%s kb_id=%s source_id=%s file_path=%s update_run_id=%s wrote_original=%s committed=%s elapsed_ms=%.2f",
                request.kb_code,
                knowledge_base_id,
                fs_entry_id,
                normalized_path,
                update_run_id,
                wrote_original,
                committed,
                (time.perf_counter() - update_started_at) * 1000,
            )
            if not committed:
                try:
                    await connection.rollback()
                except Exception:
                    logger.critical(
                        "document update rollback failed; attempting storage compensation: kb_code=%s, file_path=%s",
                        request.kb_code,
                        request.file_path,
                        exc_info=True,
                    )
            if (
                not committed
                and wrote_original
                and old_bytes is not None
                and original_location is not None
            ):
                try:
                    await self.storage_provider.write(
                        original_location,
                        old_bytes,
                        content_type=self._guess_mime_type(normalized_path),
                    )
                    logger.warning(
                        "document update storage compensation completed: kb_code=%s kb_id=%s source_id=%s file_path=%s update_run_id=%s",
                        request.kb_code,
                        knowledge_base_id,
                        fs_entry_id,
                        normalized_path,
                        update_run_id,
                    )
                except Exception:
                    logger.critical(
                        "document update rollback could not restore original object: kb_code=%s, file_path=%s",
                        request.kb_code,
                        request.file_path,
                        exc_info=True,
                    )
            raise
        finally:
            await connection.close()

    async def _rewrite_markdown(self, content: bytes, **kwargs: Any) -> bytes:
        text = content.decode("utf-8")
        parent = str(PurePosixPath(kwargs.pop("file_path")).parent)
        source_dir = "/" if parent in {"", "."} else "/" + parent.strip("/")
        rewritten = await self.markdown_reference_rewriter.rewrite(
            text,
            source_dir=source_dir,
            source_fs_entry_id=kwargs.pop("fs_entry_id"),
            reference_repository=self.knowledge_file_reference_repository,
            fs_entry_repository=self.knowledge_fs_entry_repository,
            **kwargs,
        )
        return rewritten.encode("utf-8")

    async def _write_generated_assertions(
        self,
        cursor: Any,
        *,
        knowledge_base_id: int,
        source_fs_entry_id: int,
        assertions: Sequence[GeneratedOutgoingAssertion],
        default_producer_run_id: str,
    ) -> None:
        for assertion in assertions:
            await self.knowledge_file_reference_repository.upsert_relation_assertion(
                cursor,
                knowledge_base_id=knowledge_base_id,
                source_fs_entry_id=source_fs_entry_id,
                target_fs_entry_id=assertion.target_fs_entry_id,
                relation_code=assertion.relation_code,
                original_target=assertion.original_target,
                target_path=None,
                target_suffix="",
                target_kind="FILE",
                status="resolved",
                confidence=assertion.confidence,
                discovered_by=assertion.discovered_by,
                producer_run_id=assertion.producer_run_id or default_producer_run_id,
                evidence_fingerprint=assertion.evidence_fingerprint,
                target_locator_type="ENTITY_SURFACE",
                target_locator_value=assertion.original_target,
                definition_version=assertion.definition_version,
                source_task_id=assertion.source_task_id,
            )

    async def _apply_front_matter(self, cursor: Any, **kwargs: Any) -> None:
        for name, value in parse_front_matter(kwargs["content"]).items():
            value_type = infer_metadata_value_type(value)
            await self.file_metadata_value_repository.upsert_value(
                cursor,
                fs_entry_id=kwargs["fs_entry_id"],
                knowledge_base_id=kwargs["knowledge_base_id"],
                property_name=str(name),
                value_type=value_type,
                value=normalize_metadata_value(value, value_type),
            )

    @staticmethod
    def _row_id(row: dict[str, Any]) -> int:
        return int(row.get("kid") or row.get("id"))

    @staticmethod
    def _guess_mime_type(path: str) -> str:
        if PurePosixPath(path).suffix.lower() in {".md", ".markdown"}:
            return "text/markdown"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    @classmethod
    def _is_markdown(cls, path: str, mime_type: str) -> bool:
        return mime_type == "text/markdown" or PurePosixPath(path).suffix.lower() in {
            ".md",
            ".markdown",
        }

    @staticmethod
    def _original_location(row: dict[str, Any], path: str) -> StorageLocation:
        namespace, key = row.get("file_bucket_name"), row.get("file_object_key")
        if not namespace or not key:
            raise KnowledgeBaseValidationError(
                f"file has not been uploaded yet: {path}"
            )
        return StorageLocation(namespace=str(namespace), key=str(key))

    @staticmethod
    def _markdown_location(row: dict[str, Any]) -> StorageLocation | None:
        namespace, key = row.get("markdown_bucket_name"), row.get("markdown_object_key")
        return StorageLocation(str(namespace), str(key)) if namespace and key else None

    def _bounded_context(self, markdown: str | None) -> str | None:
        if markdown is None:
            return None
        return markdown[: self.MAX_MARKDOWN_CONTEXT_CHARS]
