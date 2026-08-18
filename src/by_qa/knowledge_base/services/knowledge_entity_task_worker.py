"""Single-file persistence worker for KnowledgeEntity processing tasks.

The orchestration service owns task lifecycle and retries.  This module owns the
actual discovery/enrichment side effects for exactly one source file.  All heavy
dependencies are injected so the worker remains usable from the internal Python
SDK and can be tested without a running database or object store.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    DocumentUpdateRequest,
    FileToMarkdownIndexRequest,
    KnowledgeItemUploadRequest,
    SearchRequest,
)
from by_qa.knowledge_base.infrastructure.storage import (
    StorageLocation,
    StorageNotFoundError,
)
from by_qa.knowledge_base.services.document_update_service import (
    GeneratedOutgoingAssertion,
)
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    EntityCandidate,
    KnowledgeEntityDiscovery,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EvidenceFragment,
    KnowledgeEntityEnricher,
    KnowledgeEntityIdentity,
    RelationTarget,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    ALLOWED_RELATION_CODES,
    AhoCorasickIndex,
    IdentityScope,
    SurfaceEntry,
    SurfaceMatch,
    SurfacePosting,
    normalize_surface,
)

ENTITY_DIRECTORY = "/KnowledgeEntity"
DISCOVERY_TASK_TYPE = "ENTITY_DISCOVERY"
ENRICH_TASK_TYPES = frozenset({"DOCUMENT_ENRICH", "ENTITY_ENRICH"})
ENRICH_RELATION_SOURCE = "ENTITY_ENRICH"
DISCOVERY_RELATION_SOURCE = "ENTITY_DISCOVERY"
MAX_RECENT_RELATIONS = 50
MAX_RELATION_DOCUMENTS = 3
MAX_MATCHED_SECTIONS_PER_DOCUMENT = 6
MAX_RELATION_DOCUMENT_CHARS = 5_000
_SAFE_SLUG_RE = re.compile(r"[^\w-]+", re.UNICODE)
_NON_LINK_PATH_TRANSLATION = str.maketrans(
    {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "[": "&#91;",
        "]": "&#93;",
        "(": "&#40;",
        ")": "&#41;",
        ":": "&#58;",
    }
)


@dataclass(frozen=True, slots=True)
class KnowledgeEntityTaskContext:
    """Stable input contract for one persisted processing task.

    ``run_task`` also accepts a duck-typed orchestration context exposing these
    attributes.  ``fs_entry_id`` remains a read-only compatibility alias while
    the canonical field name is ``source_file_id``.
    """

    task_id: int
    task_type: str
    kb_code: str
    knowledge_base_id: int
    source_file_id: int
    file_path: str
    definition_version: str | None = None
    input_fingerprint: str | None = None
    input_checksum: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)
    batch_id: str | None = None

    @property
    def fs_entry_id(self) -> int:
        """Compatibility name used by early design drafts."""

        return self.source_file_id


@dataclass(frozen=True, slots=True)
class KnowledgeEntityTaskExecutionResult:
    """Serializable result returned to the processing-task orchestrator."""

    result_payload: dict[str, Any]
    index_version: str | None
    target_file_ids: tuple[int, ...] = ()


class KnowledgeEntityTaskWorker:
    """Execute one discovery or enrichment task and persist its effects."""

    def __init__(
        self,
        *,
        connection_factory: Any,
        knowledge_entity_repository: Any,
        knowledge_file_reference_repository: Any,
        storage_provider: Any,
        knowledge_item_ingestion_service: Any,
        document_update_service: Any,
        document_chunking_service: Any,
        knowledge_item_search_service: Any,
        knowledge_entity_discovery: KnowledgeEntityDiscovery,
        knowledge_entity_enricher: KnowledgeEntityEnricher,
    ) -> None:
        self._connection_factory = connection_factory
        self._entity_repository = knowledge_entity_repository
        self._reference_repository = knowledge_file_reference_repository
        self._storage = storage_provider
        self._ingestion = knowledge_item_ingestion_service
        self._document_update = document_update_service
        self._chunker = document_chunking_service
        self._search = knowledge_item_search_service
        self._discovery = knowledge_entity_discovery
        self._enricher = knowledge_entity_enricher

    async def run_task(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> KnowledgeEntityTaskExecutionResult:
        """Run exactly one task; failures are intentionally surfaced to the caller."""

        task_type = str(getattr(context.task_type, "value", context.task_type)).upper()
        task_started_at = time.perf_counter()
        log_args = self._context_log_args(context, task_type=task_type)
        logger.info(
            "knowledge_entity_task_worker started: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s",
            *log_args,
        )
        try:
            if task_type == DISCOVERY_TASK_TYPE:
                result = await self._run_discovery(context)
            elif task_type in ENRICH_TASK_TYPES:
                result = await self._run_enrich(context)
            else:
                raise ValueError(f"unsupported KnowledgeEntity task type: {task_type}")
        except Exception:
            logger.exception(
                "knowledge_entity_task_worker failed: batch_id=%s task_id=%s "
                "kb=%s source_file_id=%s file_path=%s task_type=%s elapsed_ms=%.2f",
                *log_args,
                (time.perf_counter() - task_started_at) * 1000,
            )
            raise
        logger.info(
            "knowledge_entity_task_worker completed: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "target_count=%s index_version_present=%s elapsed_ms=%.2f",
            *log_args,
            len(result.target_file_ids),
            result.index_version is not None,
            (time.perf_counter() - task_started_at) * 1000,
        )
        return result

    async def _run_discovery(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> KnowledgeEntityTaskExecutionResult:
        logger.info(
            "knowledge_entity_discovery started: batch_id=%s task_id=%s kb=%s "
            "source_file_id=%s file_path=%s task_type=%s",
            *self._context_log_args(context),
        )
        source, all_surfaces = await self._load_source_and_surfaces(context)
        markdown = await self._read_markdown(source)
        index = self._build_surface_index(all_surfaces)
        known_matches = self._scan_known_matches(
            index,
            markdown,
            knowledge_base_id=int(context.knowledge_base_id),
            subject_file_id=source.get("subject_file_id"),
        )
        matched_entity_ids, match_warnings = self._unique_matched_entity_ids(
            known_matches,
            source_file_id=int(context.source_file_id),
        )
        logger.info(
            "knowledge_entity_discovery matching completed: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "vocabulary_entity_count=%s match_count=%s matched_identity_count=%s",
            *self._context_log_args(context),
            len(all_surfaces),
            len(known_matches),
            len(matched_entity_ids),
        )
        request_params = context.request_params or {}
        max_entities = int(
            request_params.get("maxEntities", request_params.get("max_entities", 12))
        )
        discovery = await self._discovery.discover(
            markdown,
            known_matches=known_matches,
            max_entities=max_entities,
            log_context=self._intelligence_log_context(context),
        )
        logger.info(
            "knowledge_entity_discovery model completed: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "candidate_count=%s warning_count=%s attempts=%s context_truncated=%s",
            *self._context_log_args(context),
            len(discovery.candidates),
            len(discovery.warnings),
            discovery.attempts,
            discovery.context.truncated,
        )

        current_surfaces = [
            item
            for item in all_surfaces
            if int(item["knowledge_base_id"]) == int(context.knowledge_base_id)
        ]
        warnings = [*match_warnings, *discovery.warnings]
        actions: list[dict[str, Any]] = []
        # AC matches guide canonical naming and identity resolution, but they do
        # not bypass the model's content-salience decision.  Only identities in
        # the complete discovered candidate set become MENTIONS targets.
        target_ids: set[int] = set()

        for candidate in discovery.candidates:
            owner_id = self._resolve_subject_owner(
                candidate, current_surfaces=current_surfaces
            )
            if candidate.identity_scope is IdentityScope.SUBJECT and owner_id is None:
                warnings.append(
                    f"candidate discarded: unresolved same-KB subject for "
                    f"{candidate.entity_name}"
                )
                actions.append(
                    {"action": "DROPPED", "entityName": candidate.entity_name}
                )
                continue

            existing = self._find_existing_candidate(
                candidate,
                current_surfaces=current_surfaces,
                subject_file_id=owner_id,
            )
            if existing is not None:
                entity_id = int(existing["kid"])
                await self._ensure_indexed(context, existing)
                action = "ANCHORED"
            else:
                entity_id, created, was_created = await self._create_or_reuse_entity(
                    context,
                    candidate=candidate,
                    subject_file_id=owner_id,
                )
                existing = created
                current_surfaces.append(created)
                action = "CREATED" if was_created else "ANCHORED"
            if entity_id != int(context.source_file_id):
                target_ids.add(entity_id)
            actions.append(
                {
                    "action": action,
                    "entityName": existing.get("entity_name") or candidate.entity_name,
                    "entityFileId": entity_id,
                    "filePath": existing.get("file_path"),
                }
            )

        await self._persist_mentions(
            context,
            target_file_ids=sorted(target_ids),
            surfaces=current_surfaces,
        )
        logger.info(
            "knowledge_entity_discovery persistence completed: batch_id=%s "
            "task_id=%s kb=%s source_file_id=%s file_path=%s task_type=%s "
            "created_count=%s anchored_count=%s dropped_count=%s "
            "relation_replacement_count=%s",
            *self._context_log_args(context),
            sum(action["action"] == "CREATED" for action in actions),
            sum(action["action"] == "ANCHORED" for action in actions),
            sum(action["action"] == "DROPPED" for action in actions),
            len(target_ids),
        )
        return KnowledgeEntityTaskExecutionResult(
            result_payload={
                "taskType": DISCOVERY_TASK_TYPE,
                "sourceFileId": int(context.source_file_id),
                "matchedSurfaceCount": len(known_matches),
                "candidateCount": len(discovery.candidates),
                "actions": actions,
                "warnings": list(dict.fromkeys(warnings)),
                "attempts": discovery.attempts,
            },
            target_file_ids=tuple(sorted(target_ids)),
            index_version=index.version,
        )

    async def _run_enrich(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> KnowledgeEntityTaskExecutionResult:
        logger.info(
            "knowledge_entity_enrich started: batch_id=%s task_id=%s kb=%s "
            "source_file_id=%s file_path=%s task_type=%s",
            *self._context_log_args(context),
        )
        entity, current_surfaces = await self._load_entity_and_current_surfaces(context)
        identity = self._validate_entity_identity(
            entity, context, current_surfaces=current_surfaces
        )
        existing_markdown = await self._read_markdown(entity)
        evidence = await self._collect_evidence(
            context,
            identity=identity,
        )
        relation_targets = tuple(
            RelationTarget(
                file_id=int(item["kid"]), entity_name=str(item["entity_name"])
            )
            for item in current_surfaces
            if int(item["kid"]) != identity.file_id and item.get("entity_name")
        )
        logger.info(
            "knowledge_entity_enrich evidence ready: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "evidence_input_count=%s relation_target_count=%s",
            *self._context_log_args(context),
            len(evidence),
            len(relation_targets),
        )
        enriched = await self._enricher.enrich(
            identity,
            evidence,
            existing_markdown=existing_markdown,
            relation_targets=relation_targets,
            log_context=self._intelligence_log_context(context),
        )
        allowed_target_ids = {target.file_id for target in relation_targets}
        allowed_relations = tuple(
            relation
            for relation in enriched.relations
            if relation.relation_code.value in ALLOWED_RELATION_CODES
            and relation.target_file_id != identity.file_id
            and relation.target_file_id in allowed_target_ids
        )
        logger.info(
            "knowledge_entity_enrich model completed: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "relation_output_count=%s relation_allowed_count=%s "
            "warning_count=%s attempts=%s template_coverage=%s",
            *self._context_log_args(context),
            len(enriched.relations),
            len(allowed_relations),
            len(enriched.warnings),
            enriched.attempts,
            enriched.template_coverage,
        )
        full_markdown = self._render_entity_markdown(
            entity_name=identity.entity_name,
            body=enriched.markdown,
            aliases=identity.aliases,
            definition_version=identity.definition_version,
            subject_file_id=identity.subject_file_id,
            entity_type=entity.get("entity_type"),
        )
        checksum = context.input_checksum or entity.get("checksum")
        if not checksum:
            raise ValueError("entity enrichment requires an input checksum")
        producer_run_id = f"entity-enrich:{context.task_id}"
        generated_assertions = tuple(
            GeneratedOutgoingAssertion(
                target_fs_entry_id=int(relation.target_file_id),
                relation_code=relation.relation_code.value,
                original_target=relation.target_entity_name,
                discovered_by=ENRICH_RELATION_SOURCE,
                confidence=relation.confidence,
                definition_version=identity.definition_version,
                source_task_id=int(context.task_id),
                evidence_fingerprint=hashlib.sha256(
                    (
                        f"{identity.file_id}:{relation.relation_code.value}:"
                        f"{relation.target_file_id}:{enriched.markdown}"
                    ).encode("utf-8")
                ).hexdigest(),
                producer_run_id=producer_run_id,
            )
            for relation in allowed_relations
        )
        await self._document_update.update_file(
            DocumentUpdateRequest(
                kb_code=context.kb_code,
                file_path=context.file_path,
                file_content=full_markdown.encode("utf-8"),
                process_front_matter=True,
                skip_if_duplicate=False,
                refer_signature=str(checksum),
            ),
            generated_outgoing_assertions=generated_assertions,
            producer_run_id=producer_run_id,
        )
        await self._ingestion.file_to_markdown_index(
            FileToMarkdownIndexRequest(
                kb_code=context.kb_code,
                file_path=context.file_path,
            ),
            document_chunking_service=self._chunker,
        )
        logger.info(
            "knowledge_entity_enrich persistence completed: batch_id=%s task_id=%s "
            "kb=%s source_file_id=%s file_path=%s task_type=%s "
            "relation_replacement_count=%s",
            *self._context_log_args(context),
            len(generated_assertions),
        )

        target_ids = tuple(
            sorted({relation.target_file_id for relation in allowed_relations})
        )
        return KnowledgeEntityTaskExecutionResult(
            result_payload={
                "taskType": "DOCUMENT_ENRICH",
                "sourceFileId": identity.file_id,
                "actions": [
                    {
                        "action": "UPDATED",
                        "filePath": context.file_path,
                        "relationCount": len(allowed_relations),
                    }
                ],
                "warnings": list(enriched.warnings),
                "templateCoverage": enriched.template_coverage,
                "missingSections": list(enriched.missing_sections),
                "placeholderCount": enriched.placeholder_count,
                "discardedRelationCount": enriched.discarded_relation_count,
                "evidenceFragmentCount": len(enriched.evidence.fragments),
                "attempts": enriched.attempts,
            },
            target_file_ids=target_ids,
            index_version=None,
        )

    async def _load_source_and_surfaces(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            source = await self._entity_repository.get_file_with_metadata(
                cursor,
                knowledge_base_id=int(context.knowledge_base_id),
                file_path=context.file_path,
            )
            if source is None:
                raise ValueError(f"source file not found: {context.file_path}")
            if int(source["kid"]) != int(context.source_file_id):
                raise ValueError("source file identity changed after task creation")
            surfaces = await self._entity_repository.list_entity_surfaces(
                cursor, knowledge_base_id=None
            )
            return dict(source), [dict(item) for item in surfaces]
        finally:
            await connection.close()

    async def _load_entity_and_current_surfaces(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            entity = await self._entity_repository.get_file_with_metadata(
                cursor,
                knowledge_base_id=int(context.knowledge_base_id),
                file_path=context.file_path,
            )
            if entity is None:
                raise ValueError(f"entity file not found: {context.file_path}")
            if int(entity["kid"]) != int(context.source_file_id):
                raise ValueError("entity file identity changed after task creation")
            surfaces = await self._entity_repository.list_entity_surfaces(
                cursor, knowledge_base_id=int(context.knowledge_base_id)
            )
            return dict(entity), [dict(item) for item in surfaces]
        finally:
            await connection.close()

    async def _read_markdown(self, row: Mapping[str, Any]) -> str:
        markdown_namespace = row.get("markdown_bucket_name")
        markdown_key = row.get("markdown_object_key")
        if markdown_namespace and markdown_key:
            try:
                content = await self._storage.read(
                    StorageLocation(str(markdown_namespace), str(markdown_key))
                )
                text = content.decode("utf-8").strip()
                if text:
                    return text
            except StorageNotFoundError:
                pass
        original_namespace = row.get("file_bucket_name")
        original_key = row.get("file_object_key")
        if not original_namespace or not original_key:
            raise ValueError(
                f"file has no readable storage object: {row.get('file_path')}"
            )
        content = await self._storage.read(
            StorageLocation(str(original_namespace), str(original_key))
        )
        text = content.decode("utf-8").strip()
        if not text:
            raise ValueError(f"file content is empty: {row.get('file_path')}")
        return text

    @staticmethod
    def _build_surface_index(surfaces: Sequence[Mapping[str, Any]]) -> AhoCorasickIndex:
        entries: list[SurfaceEntry] = []
        version_parts: list[str] = []
        for item in surfaces:
            entity_name = str(item.get("entity_name") or "").strip()
            if not entity_name:
                continue
            posting_values = {
                "entity_file_id": int(item["kid"]),
                "knowledge_base_id": int(item["knowledge_base_id"]),
                "entity_name": entity_name,
                "subject_file_id": (
                    int(item["subject_file_id"])
                    if item.get("subject_file_id") is not None
                    else None
                ),
            }
            entries.append(
                SurfaceEntry(
                    surface=entity_name,
                    posting=SurfacePosting(**posting_values, surface_type="entityName"),
                )
            )
            for alias in item.get("aliases") or ():
                if normalize_surface(str(alias)) == normalize_surface(entity_name):
                    continue
                entries.append(
                    SurfaceEntry(
                        surface=str(alias),
                        posting=SurfacePosting(**posting_values, surface_type="alias"),
                    )
                )
            version_parts.append(
                f"{item['knowledge_base_id']}:{item['kid']}:{entity_name}:"
                f"{','.join(item.get('aliases') or ())}"
            )
        version = hashlib.sha256("\n".join(version_parts).encode()).hexdigest()[:16]
        return AhoCorasickIndex(entries, version=version)

    @staticmethod
    def _scan_known_matches(
        index: AhoCorasickIndex,
        markdown: str,
        *,
        knowledge_base_id: int,
        subject_file_id: Any,
    ) -> tuple[SurfaceMatch, ...]:
        subject_context = (
            {int(subject_file_id)} if subject_file_id is not None else set()
        )
        initial = index.scan(
            markdown,
            current_knowledge_base_id=knowledge_base_id,
            subject_context_file_ids=subject_context,
        )
        subject_context.update(
            posting.entity_file_id
            for match in initial
            for posting in match.anchorable_postings
            if posting.subject_file_id is None
        )
        return index.scan(
            markdown,
            current_knowledge_base_id=knowledge_base_id,
            subject_context_file_ids=subject_context,
        )

    @staticmethod
    def _unique_matched_entity_ids(
        matches: Sequence[SurfaceMatch], *, source_file_id: int
    ) -> tuple[set[int], list[str]]:
        matched_entity_ids: set[int] = set()
        warnings: list[str] = []
        for match in matches:
            ids = {posting.entity_file_id for posting in match.anchorable_postings}
            if len(ids) == 1:
                entity_id = next(iter(ids))
                if entity_id != source_file_id:
                    matched_entity_ids.add(entity_id)
            elif len(ids) > 1:
                warnings.append(f"ambiguous surface not anchored: {match.matched_text}")
        return matched_entity_ids, warnings

    @staticmethod
    def _resolve_subject_owner(
        candidate: EntityCandidate,
        *,
        current_surfaces: Sequence[Mapping[str, Any]],
    ) -> int | None:
        if candidate.identity_scope is IdentityScope.GLOBAL:
            return None
        if candidate.subject_file_id is not None:
            matches = {
                int(item["kid"])
                for item in current_surfaces
                if int(item["kid"]) == candidate.subject_file_id
            }
        else:
            subject_name = normalize_surface(candidate.subject_entity_name or "")
            matches = {
                int(item["kid"])
                for item in current_surfaces
                if item.get("entity_name")
                and normalize_surface(str(item["entity_name"])) == subject_name
                and item.get("subject_file_id") is None
            }
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    def _find_existing_candidate(
        candidate: EntityCandidate,
        *,
        current_surfaces: Sequence[Mapping[str, Any]],
        subject_file_id: int | None,
    ) -> dict[str, Any] | None:
        candidate_surfaces = {
            normalize_surface(value)
            for value in (
                candidate.entity_name,
                candidate.local_name,
                *candidate.aliases,
            )
            if normalize_surface(value)
        }
        matches: list[dict[str, Any]] = []
        for raw in current_surfaces:
            current_subject = raw.get("subject_file_id")
            if subject_file_id is None:
                if current_subject is not None:
                    continue
            elif current_subject is None or int(current_subject) != subject_file_id:
                continue
            entity_surfaces = {
                normalize_surface(str(value))
                for value in (raw.get("entity_name"), *(raw.get("aliases") or ()))
                if value and normalize_surface(str(value))
            }
            if candidate_surfaces & entity_surfaces:
                matches.append(dict(raw))
        if len(matches) > 1:
            raise ValueError(
                f"ambiguous entity identity for candidate: {candidate.entity_name}"
            )
        return matches[0] if matches else None

    async def _create_or_reuse_entity(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        candidate: EntityCandidate,
        subject_file_id: int | None,
    ) -> tuple[int, dict[str, Any], bool]:
        existing = await self._refresh_exact_candidate(
            context,
            candidate=candidate,
            subject_file_id=subject_file_id,
        )
        if existing is not None:
            await self._ensure_indexed(context, existing)
            return int(existing["kid"]), existing, False

        definition_version = context.definition_version or "v1"
        aliases = self._candidate_aliases(candidate)
        content = self._render_entity_markdown(
            entity_name=candidate.entity_name,
            body=(
                f"# {candidate.entity_name}\n\n"
                "## 实体定义与边界\n\n"
                f"{candidate.evidence}\n\n"
                "## 发现来源\n\n"
                f"来源文档：{self._non_link_source_path(context.file_path)}\n"
            ),
            aliases=aliases,
            definition_version=definition_version,
            subject_file_id=subject_file_id,
            entity_type=candidate.entity_type,
        )
        path = self._entity_path(candidate.entity_name)
        occupied = await self._get_entity_by_path(context, path)
        if occupied is not None:
            anchored = self._validate_readable_path_identity(
                occupied,
                candidate=candidate,
                subject_file_id=subject_file_id,
            )
            await self._ensure_indexed(context, anchored)
            return int(anchored["kid"]), anchored, False
        try:
            uploaded = await self._ingestion.upload_file(
                KnowledgeItemUploadRequest(
                    kb_code=context.kb_code,
                    file_path=path,
                    file_content=content.encode("utf-8"),
                    process_front_matter=True,
                    skip_if_duplicate=False,
                )
            )
        except Exception:
            concurrent = await self._refresh_exact_candidate(
                context,
                candidate=candidate,
                subject_file_id=subject_file_id,
            )
            if concurrent is not None:
                await self._ensure_indexed(context, concurrent)
                return int(concurrent["kid"]), concurrent, False
            occupied = await self._get_entity_by_path(context, path)
            if occupied is not None:
                anchored = self._validate_readable_path_identity(
                    occupied,
                    candidate=candidate,
                    subject_file_id=subject_file_id,
                )
                await self._ensure_indexed(context, anchored)
                return int(anchored["kid"]), anchored, False
            raise

        await self._ingestion.file_to_markdown_index(
            FileToMarkdownIndexRequest(kb_code=context.kb_code, file_path=path),
            document_chunking_service=self._chunker,
        )
        created = await self._get_entity_by_path(context, path)
        if created is None:
            created = {
                "kid": int(uploaded["fs_entry_id"]),
                "knowledge_base_id": int(context.knowledge_base_id),
                "file_path": path,
                "entity_name": candidate.entity_name,
                "aliases": list(aliases),
                "subject_file_id": subject_file_id,
            }
        return int(created["kid"]), created, True

    @staticmethod
    def _validate_readable_path_identity(
        row: Mapping[str, Any],
        *,
        candidate: EntityCandidate,
        subject_file_id: int | None,
    ) -> dict[str, Any]:
        path = str(row.get("file_path") or "")
        if row.get("document_kind") != "knowledgeEntity":
            raise ValueError(
                "KnowledgeEntity readable path is occupied by a non-entity "
                f"document: {path}"
            )
        existing_name = str(row.get("entity_name") or "").strip()
        if existing_name and normalize_surface(existing_name) != normalize_surface(
            candidate.entity_name
        ):
            raise ValueError(
                "KnowledgeEntity readable path has conflicting entityName metadata: "
                f"{path}"
            )
        existing_subject = row.get("subject_file_id")
        normalized_existing_subject = (
            int(existing_subject) if existing_subject is not None else None
        )
        if normalized_existing_subject != subject_file_id:
            raise ValueError(
                "KnowledgeEntity readable path has conflicting subject identity: "
                f"{path}"
            )
        anchored = dict(row)
        # Missing identity metadata is not silently rewritten during discovery.
        # The candidate values only complete the in-memory surface used by this
        # task; explicit metadata repair remains a separate document operation.
        anchored["entity_name"] = existing_name or candidate.entity_name
        anchored["aliases"] = list(row.get("aliases") or ())
        return anchored

    async def _refresh_exact_candidate(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        candidate: EntityCandidate,
        subject_file_id: int | None,
    ) -> dict[str, Any] | None:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            surfaces = await self._entity_repository.list_entity_surfaces(
                cursor, knowledge_base_id=int(context.knowledge_base_id)
            )
            return self._find_existing_candidate(
                candidate,
                current_surfaces=surfaces,
                subject_file_id=subject_file_id,
            )
        finally:
            await connection.close()

    async def _get_entity_by_path(
        self, context: KnowledgeEntityTaskContext | Any, path: str
    ) -> dict[str, Any] | None:
        connection = await self._connection_factory()
        try:
            return await self._entity_repository.get_file_with_metadata(
                connection.cursor(),
                knowledge_base_id=int(context.knowledge_base_id),
                file_path=path,
            )
        finally:
            await connection.close()

    async def _ensure_indexed(
        self, context: KnowledgeEntityTaskContext | Any, entity: Mapping[str, Any]
    ) -> None:
        row = entity
        if not row.get("markdown_bucket_name") or not row.get("markdown_object_key"):
            path = str(row.get("file_path") or "")
            if path:
                full = await self._get_entity_by_path(context, path)
                row = full or row
        if row.get("markdown_bucket_name") and row.get("markdown_object_key"):
            return
        path = str(row.get("file_path") or "")
        if not path:
            raise ValueError("existing entity is missing file path")
        await self._ingestion.file_to_markdown_index(
            FileToMarkdownIndexRequest(kb_code=context.kb_code, file_path=path),
            document_chunking_service=self._chunker,
        )

    async def _persist_mentions(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        target_file_ids: Sequence[int],
        surfaces: Sequence[Mapping[str, Any]],
    ) -> None:
        names = {
            int(item["kid"]): str(item.get("entity_name") or item.get("file_path"))
            for item in surfaces
        }
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            await self._reference_repository.delete_outgoing_for_source_fs_entry_id(
                cursor,
                knowledge_base_id=int(context.knowledge_base_id),
                source_fs_entry_id=int(context.source_file_id),
                relation_code="MENTIONS",
                discovered_by=DISCOVERY_RELATION_SOURCE,
            )
            for target_id in target_file_ids:
                if target_id == int(context.source_file_id):
                    continue
                await self._reference_repository.upsert_relation_assertion(
                    cursor,
                    knowledge_base_id=int(context.knowledge_base_id),
                    source_fs_entry_id=int(context.source_file_id),
                    target_fs_entry_id=int(target_id),
                    relation_code="MENTIONS",
                    original_target=names.get(int(target_id), str(target_id)),
                    target_path=None,
                    target_suffix="",
                    target_kind="FILE",
                    status="resolved",
                    confidence=None,
                    discovered_by=DISCOVERY_RELATION_SOURCE,
                    producer_run_id=f"entity-discovery:{context.task_id}",
                    evidence_fingerprint=hashlib.sha256(
                        (
                            f"{context.source_file_id}:{target_id}:"
                            f"{context.definition_version or 'v1'}"
                        ).encode("utf-8")
                    ).hexdigest(),
                    target_locator_type="ENTITY_SURFACE",
                    target_locator_value=names.get(int(target_id), str(target_id)),
                    definition_version=context.definition_version or "v1",
                    source_task_id=int(context.task_id),
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    def _validate_entity_identity(
        self,
        entity: Mapping[str, Any],
        context: KnowledgeEntityTaskContext | Any,
        *,
        current_surfaces: Sequence[Mapping[str, Any]],
    ) -> KnowledgeEntityIdentity:
        if entity.get("document_kind") != "knowledgeEntity":
            raise ValueError("entity enrichment requires documentKind=knowledgeEntity")
        name = str(entity.get("entity_name") or "").strip()
        definition_version = str(entity.get("definition_version") or "").strip()
        if not name or not definition_version:
            raise ValueError("entity identity metadata is incomplete")
        subject_id = entity.get("subject_file_id")
        if subject_id is not None and not any(
            int(item["kid"]) == int(subject_id) for item in current_surfaces
        ):
            raise ValueError("subject-scoped entity owner is not a live same-KB entity")
        return KnowledgeEntityIdentity(
            file_id=int(context.source_file_id),
            knowledge_base_id=int(context.knowledge_base_id),
            entity_name=name,
            aliases=tuple(str(item) for item in entity.get("aliases") or ()),
            subject_file_id=int(subject_id) if subject_id is not None else None,
            definition_version=definition_version,
        )

    async def _collect_evidence(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        identity: KnowledgeEntityIdentity,
    ) -> list[EvidenceFragment]:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            relations = (
                await self._reference_repository.list_recent_assertions_by_target(
                    cursor,
                    knowledge_base_id=identity.knowledge_base_id,
                    target_fs_entry_id=identity.file_id,
                    relation_code=None,
                    limit=MAX_RECENT_RELATIONS,
                    offset=0,
                )
            )
            selected_relations: list[Mapping[str, Any]] = []
            selected_source_ids: set[int] = set()
            for relation in relations:
                source_id = int(relation["source_fs_entry_id"])
                if source_id in selected_source_ids:
                    continue
                selected_relations.append(relation)
                selected_source_ids.add(source_id)
                if len(selected_relations) >= MAX_RELATION_DOCUMENTS:
                    break
            source_rows = await self._entity_repository.get_files_by_ids(
                cursor,
                knowledge_base_id=identity.knowledge_base_id,
                fs_entry_ids=list(selected_source_ids),
            )
        finally:
            await connection.close()

        fragments: list[EvidenceFragment] = []
        rows_by_id = {int(row["kid"]): row for row in source_rows}
        for relation in selected_relations:
            file_id = int(relation["source_fs_entry_id"])
            row = rows_by_id.get(file_id)
            if row is None:
                continue
            content = await self._read_markdown(row)
            selected_content = self._select_entity_sections(
                content,
                names=(identity.entity_name, *identity.aliases),
            )
            if not selected_content:
                continue
            fragments.append(
                EvidenceFragment(
                    document_file_id=file_id,
                    document_path=str(row["file_path"]),
                    content=selected_content,
                    direct_mention=True,
                    explicit_reference=True,
                    relation_code=str(relation.get("relation_code") or "MENTIONS"),
                )
            )

        params = context.request_params or {}
        query = " ".join((identity.entity_name, *identity.aliases)).strip()
        hits = await self._search.search(
            SearchRequest(
                query=query,
                kb_code_list=[context.kb_code],
                top_k=int(params.get("topK", params.get("top_k", 20))),
                search_mode="mixedRecall",
            )
        )
        search_connection = await self._connection_factory()
        try:
            cursor = search_connection.cursor()
            for hit in hits:
                kb_code = str(self._value(hit, "kb_code", "knCode"))
                file_path = str(self._value(hit, "file_path", "filePath"))
                if kb_code != str(context.kb_code):
                    continue
                row = await self._entity_repository.get_file_with_metadata(
                    cursor,
                    knowledge_base_id=identity.knowledge_base_id,
                    file_path=file_path,
                )
                if row is None:
                    continue
                fragments.append(
                    EvidenceFragment(
                        document_file_id=int(row["kid"]),
                        document_path=file_path,
                        content=str(self._value(hit, "chunk_text", "chunkText")),
                        semantic_score=float(self._value(hit, "score") or 0.0),
                        authorized=True,
                    )
                )
        finally:
            await search_connection.close()
        return fragments

    @staticmethod
    def _select_entity_sections(content: str, *, names: Sequence[str]) -> str:
        """Keep bounded complete Markdown sections that mention the entity."""

        normalized_names = tuple(
            normalize_surface(name) for name in names if normalize_surface(name)
        )
        if not normalized_names:
            return ""
        body = re.sub(
            r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)",
            "",
            content,
            count=1,
            flags=re.DOTALL,
        ).strip()
        sections = [
            section.strip()
            for section in re.split(r"(?=^#{1,6}\s)", body, flags=re.MULTILINE)
            if section.strip()
        ]
        matches: list[str] = []
        total_chars = 0
        for section in sections:
            normalized_section = normalize_surface(section)
            if not any(name in normalized_section for name in normalized_names):
                continue
            remaining = MAX_RELATION_DOCUMENT_CHARS - total_chars
            if remaining <= 0:
                break
            selected = section[:remaining]
            matches.append(selected)
            total_chars += len(selected)
            if len(matches) >= MAX_MATCHED_SECTIONS_PER_DOCUMENT:
                break
        return "\n\n".join(matches)

    @staticmethod
    def _candidate_aliases(candidate: EntityCandidate) -> tuple[str, ...]:
        values = [*candidate.aliases]
        if (
            candidate.identity_scope is IdentityScope.SUBJECT
            and candidate.local_name
            and normalize_surface(candidate.local_name)
            != normalize_surface(candidate.entity_name)
        ):
            values.append(candidate.local_name)
        seen = {normalize_surface(candidate.entity_name)}
        aliases: list[str] = []
        for value in values:
            key = normalize_surface(value)
            if not key or key in seen:
                continue
            seen.add(key)
            aliases.append(value.strip())
        return tuple(aliases)

    @staticmethod
    def _entity_path(entity_name: str) -> str:
        normalized = unicodedata.normalize("NFKC", entity_name).strip()
        slug = _SAFE_SLUG_RE.sub("-", normalized.replace("/", "-")).strip("-_")
        slug = slug[:48].strip("-_") or "entity"
        return f"{ENTITY_DIRECTORY}/{slug}.md"

    @staticmethod
    def _non_link_source_path(file_path: str) -> str:
        """Render provenance text without creating links or stable-ref tokens."""

        return str(file_path).translate(_NON_LINK_PATH_TRANSLATION)

    @staticmethod
    def _render_entity_markdown(
        *,
        entity_name: str,
        body: str,
        aliases: Sequence[str],
        definition_version: str,
        subject_file_id: int | None,
        entity_type: str | None,
    ) -> str:
        metadata: dict[str, Any] = {
            "documentKind": "knowledgeEntity",
            "processingCapabilities": ["entityEnrich"],
            "entityName": entity_name,
            "aliases": list(aliases),
            "definitionVersion": definition_version,
        }
        if subject_file_id is not None:
            metadata["subjectFileId"] = subject_file_id
        if entity_type:
            metadata["entityType"] = entity_type
        yaml_text = yaml.safe_dump(
            metadata,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        markdown_body = body.strip()
        if not markdown_body.startswith(f"# {entity_name}"):
            markdown_body = f"# {entity_name}\n\n{markdown_body}"
        return f"---\n{yaml_text}\n---\n\n{markdown_body}\n"

    @staticmethod
    def _value(value: Any, *names: str) -> Any:
        for name in names:
            if isinstance(value, Mapping) and name in value:
                return value[name]
            if hasattr(value, name):
                return getattr(value, name)
        return None

    @staticmethod
    def _context_log_args(
        context: KnowledgeEntityTaskContext | Any, *, task_type: str | None = None
    ) -> tuple[Any, ...]:
        normalized_task_type = (
            task_type
            or str(getattr(context.task_type, "value", context.task_type)).upper()
        )
        return (
            getattr(context, "batch_id", None) or "-",
            context.task_id,
            context.kb_code,
            context.source_file_id,
            context.file_path,
            normalized_task_type,
        )

    @classmethod
    def _intelligence_log_context(
        cls, context: KnowledgeEntityTaskContext | Any
    ) -> dict[str, Any]:
        values = cls._context_log_args(context)
        return dict(
            zip(
                (
                    "batch_id",
                    "task_id",
                    "kb_code",
                    "source_file_id",
                    "file_path",
                    "task_type",
                ),
                values,
                strict=True,
            )
        )
