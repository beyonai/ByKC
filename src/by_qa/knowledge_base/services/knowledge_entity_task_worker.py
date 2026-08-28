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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
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
    DISCOVERY_PROTOCOL_VERSION,
    DiscoveredEntity,
    KnowledgeEntityDiscovery,
    build_discovery_context,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EvidenceFragment,
    KnowledgeEntityEnricher,
    KnowledgeEntityIdentity,
    RelationTarget,
    format_source_reference,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    ALLOWED_RELATION_CODES,
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
MAX_RELATION_FRAGMENT_CHARS = 2_000
MAX_SEARCH_QUERY_CHARS = 1_000
MAX_TOPICS_PER_SEARCH_QUERY = 6
MAX_SEMANTIC_FRAGMENTS_PER_DOCUMENT = 25
MIN_SEMANTIC_SCORE_RATIO = 0.7
STRONG_SEMANTIC_SCORE_RATIO = 0.97
ENTITY_ENRICHED_PROPERTY = "entityEnriched"
_SAFE_SLUG_RE = re.compile(r"[^\w-]+", re.UNICODE)


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
        knowledge_entity_asset_service: Any,
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
        if knowledge_entity_asset_service is None:
            raise ValueError("knowledge_entity_asset_service is required")
        self._asset_service = knowledge_entity_asset_service

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
        """Resolve only extracted candidates against the durable entity registry."""
        source = await self._load_source(context)
        markdown = await self._read_markdown(source)
        markdown = (
            await self._search.resolve_markdown_texts(
                knowledge_base_id=int(context.knowledge_base_id),
                texts=[markdown],
            )
        )[0]
        request_params = context.request_params or {}
        discovery = await self._discovery.discover(
            markdown,
            max_entities=int(
                request_params.get(
                    "maxEntities", request_params.get("max_entities", 12)
                )
            ),
            max_topics=int(
                request_params.get("maxTopics", request_params.get("max_topics", 24))
            ),
            log_context=self._intelligence_log_context(context),
        )
        warnings = list(discovery.warnings)
        actions: list[dict[str, Any]] = []
        target_ids: set[int] = set()
        projections: list[dict[str, Any]] = []
        resolved_by_ref: dict[str, tuple[Any, dict[str, Any]]] = {}
        for candidate in discovery.entities:
            resolution = await self._asset_service.resolve_candidate(
                knowledge_base_id=int(context.knowledge_base_id),
                entity_name=candidate.name,
                aliases=candidate.aliases,
                subject_entity_id=None,
                subject_name=None,
                entity_type=None,
                description=candidate.description,
                evidence=candidate.evidence_summary,
            )
            warnings.extend(resolution.warnings)
            canonical_candidate = replace(
                candidate,
                name=resolution.canonical_name,
                aliases=tuple(
                    dict.fromkeys(
                        (
                            *candidate.aliases,
                            *(
                                (candidate.name,)
                                if normalize_surface(candidate.name)
                                != normalize_surface(resolution.canonical_name)
                                else ()
                            ),
                        )
                    )
                ),
            )
            projection = None
            if resolution.fs_entry_id is not None:
                projection = await self._get_entity_by_file_id(
                    context, resolution.fs_entry_id
                )
            if projection is None:
                _, projection, _ = await self._create_or_reuse_entity(
                    context,
                    candidate=canonical_candidate,
                )
                await self._asset_service.attach_file(
                    knowledge_base_id=int(context.knowledge_base_id),
                    entity_id=resolution.entity_id,
                    fs_entry_id=int(projection["kid"]),
                )
            else:
                await self._ensure_indexed(context, projection)
            projection = dict(projection)
            projection["entity_name"] = resolution.canonical_name
            projections.append(projection)
            resolved_by_ref[candidate.entity_ref] = (
                resolution,
                projection,
            )
            file_id = int(projection["kid"])
            if file_id != int(context.source_file_id):
                target_ids.add(file_id)
            actions.append(
                {
                    "action": "CREATED" if resolution.created else "ANCHORED",
                    "entityRef": candidate.entity_ref,
                    "inputEntityName": candidate.name,
                    "entityName": resolution.canonical_name,
                    "canonicalEntityId": resolution.entity_id,
                    "entityFileId": file_id,
                    "filePath": projection.get("file_path"),
                    "resolutionMethod": resolution.method.value,
                    "aliasAdded": resolution.alias_added,
                    "candidateCount": resolution.candidate_count,
                }
            )

        topic_inputs: list[dict[str, Any]] = []
        for topic in discovery.topics:
            owner = resolved_by_ref.get(topic.owner_entity_ref)
            if owner is None:
                warnings.append(
                    f"topic discarded: unresolved owner {topic.owner_entity_ref}"
                )
                continue
            topic_inputs.append(
                {
                    "owner_entity_id": int(owner[0].entity_id),
                    "name": topic.name,
                }
            )
        topic_actions = await self._asset_service.upsert_topics(
            knowledge_base_id=int(context.knowledge_base_id),
            topics=topic_inputs,
        )
        actions.extend(topic_actions)

        await self._persist_mentions(
            context,
            target_file_ids=sorted(target_ids),
            surfaces=projections,
        )
        return KnowledgeEntityTaskExecutionResult(
            result_payload={
                "taskType": DISCOVERY_TASK_TYPE,
                "sourceFileId": int(context.source_file_id),
                "matchedSurfaceCount": sum(
                    action.get("resolutionMethod") in {"EXACT_CANONICAL", "EXACT_ALIAS"}
                    for action in actions
                ),
                "candidateCount": len(discovery.entities),
                "topicCount": len(discovery.topics),
                "protocolVersion": DISCOVERY_PROTOCOL_VERSION,
                "rawDiscovery": dict(discovery.raw_json),
                "sourceChecksum": str(
                    context.input_checksum or source.get("checksum") or ""
                ),
                "contextTruncated": bool(
                    discovery.context and discovery.context.truncated
                ),
                "actions": actions,
                "warnings": list(dict.fromkeys(warnings)),
                "attempts": discovery.attempts,
            },
            target_file_ids=tuple(sorted(target_ids)),
            index_version=None,
        )

    async def _load_source(
        self, context: KnowledgeEntityTaskContext | Any
    ) -> dict[str, Any]:
        connection = await self._connection_factory()
        try:
            source = await self._entity_repository.get_file_with_metadata(
                connection.cursor(),
                knowledge_base_id=int(context.knowledge_base_id),
                file_path=context.file_path,
            )
            if source is None:
                raise ValueError(f"source file not found: {context.file_path}")
            if int(source["kid"]) != int(context.source_file_id):
                raise ValueError("source file identity changed after task creation")
            return dict(source)
        finally:
            await connection.close()

    async def _get_entity_by_file_id(
        self, context: KnowledgeEntityTaskContext | Any, fs_entry_id: int
    ) -> dict[str, Any] | None:
        connection = await self._connection_factory()
        try:
            rows = await self._entity_repository.get_files_by_ids(
                connection.cursor(),
                knowledge_base_id=int(context.knowledge_base_id),
                fs_entry_ids=[fs_entry_id],
            )
            return dict(rows[0]) if rows else None
        finally:
            await connection.close()

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
        previous_enrich_at = await self._load_previous_enrich_at(context, identity)
        incremental = (
            entity.get("entity_enriched") is True and previous_enrich_at is not None
        )
        topics = await self._load_entity_topics(
            identity,
            updated_after=previous_enrich_at if incremental else None,
        )
        existing_markdown = await self._read_markdown(entity)
        existing_markdown = (
            await self._search.resolve_markdown_texts(
                knowledge_base_id=identity.knowledge_base_id,
                texts=[existing_markdown],
            )
        )[0]
        evidence = await self._collect_evidence(
            context,
            identity=identity,
            existing_markdown=existing_markdown,
            topics=topics,
            updated_after=previous_enrich_at if incremental else None,
        )
        if incremental and not evidence:
            logger.info(
                "knowledge_entity_enrich skipped: reason=no_new_evidence "
                "batch_id=%s task_id=%s kb=%s source_file_id=%s file_path=%s "
                "task_type=%s previous_enrich_at=%s topic_count=%s",
                *self._context_log_args(context),
                previous_enrich_at.isoformat(),
                len(topics),
            )
            return KnowledgeEntityTaskExecutionResult(
                result_payload={
                    "taskType": "DOCUMENT_ENRICH",
                    "sourceFileId": identity.file_id,
                    "actions": [
                        {
                            "action": "SKIPPED_NO_NEW_EVIDENCE",
                            "filePath": context.file_path,
                            "relationCount": 0,
                        }
                    ],
                    "incremental": True,
                    "previousEnrichAt": previous_enrich_at.isoformat(),
                    "topicCount": len(topics),
                    "evidenceFragmentCount": 0,
                    "warnings": [],
                },
                target_file_ids=(),
                index_version=None,
            )
        relation_targets = tuple(
            RelationTarget(
                file_id=int(item["kid"]), entity_name=str(item["entity_name"])
            )
            for item in current_surfaces
            if int(item["kid"]) != identity.file_id
            and item.get("entity_name")
            and item.get("entity_enriched") is True
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
            topics=topics,
            incremental=incremental,
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
            subject_file_id=identity.subject_file_id,
            entity_type=entity.get("entity_type"),
            entity_enriched=True,
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
                "topicCount": len(topics),
                "incremental": incremental,
                "previousEnrichAt": (
                    previous_enrich_at.isoformat() if previous_enrich_at else None
                ),
                "attempts": enriched.attempts,
            },
            target_file_ids=target_ids,
            index_version=None,
        )

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

    async def _load_entity_topics(
        self,
        identity: KnowledgeEntityIdentity,
        *,
        updated_after: datetime | None = None,
    ) -> tuple[str, ...]:
        loader = getattr(self._asset_service, "list_topics_for_entity_file", None)
        if loader is None:
            return ()
        topics = await loader(
            knowledge_base_id=identity.knowledge_base_id,
            fs_entry_id=identity.file_id,
            updated_after=updated_after,
        )
        return tuple(
            dict.fromkeys(str(topic).strip() for topic in topics if str(topic).strip())
        )

    async def _load_previous_enrich_at(
        self,
        context: KnowledgeEntityTaskContext | Any,
        identity: KnowledgeEntityIdentity,
    ) -> datetime | None:
        loader = getattr(
            self._entity_repository,
            "get_latest_successful_enrich_finished_at",
            None,
        )
        if loader is None:
            return None
        connection = await self._connection_factory()
        try:
            value = await loader(
                connection.cursor(),
                knowledge_base_id=identity.knowledge_base_id,
                fs_entry_id=identity.file_id,
                before_task_id=int(context.task_id),
            )
        finally:
            await connection.close()
        if value is None:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("previous enrich finished_at must be timezone-aware")
        return value

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

    async def _create_or_reuse_entity(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        candidate: DiscoveredEntity,
    ) -> tuple[int, dict[str, Any], bool]:
        aliases = self._candidate_aliases(candidate)
        content = self._render_entity_markdown(
            entity_name=candidate.name,
            body=(
                f"# {candidate.name}\n\n"
                "## 实体定义与边界\n\n"
                f"{candidate.description} "
                f"{format_source_reference(context.file_path)}\n\n"
            ),
            aliases=aliases,
            subject_file_id=None,
            entity_type=None,
            entity_enriched=False,
        )
        path = self._entity_path(candidate.name)
        occupied = await self._get_entity_by_path(context, path)
        if occupied is not None:
            anchored = self._validate_readable_path_identity(
                occupied,
                candidate=candidate,
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
            occupied = await self._get_entity_by_path(context, path)
            if occupied is not None:
                anchored = self._validate_readable_path_identity(
                    occupied,
                    candidate=candidate,
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
                "entity_name": candidate.name,
                "aliases": list(aliases),
                "subject_file_id": None,
            }
        return int(created["kid"]), created, True

    @staticmethod
    def _validate_readable_path_identity(
        row: Mapping[str, Any],
        *,
        candidate: DiscoveredEntity,
    ) -> dict[str, Any]:
        path = str(row.get("file_path") or "")
        if row.get("document_kind") != "knowledgeEntity":
            raise ValueError(
                "KnowledgeEntity readable path is occupied by a non-entity "
                f"document: {path}"
            )
        existing_name = str(row.get("entity_name") or "").strip()
        if existing_name and normalize_surface(existing_name) != normalize_surface(
            candidate.name
        ):
            raise ValueError(
                "KnowledgeEntity readable path has conflicting entityName metadata: "
                f"{path}"
            )
        existing_subject = row.get("subject_file_id")
        normalized_existing_subject = (
            int(existing_subject) if existing_subject is not None else None
        )
        if normalized_existing_subject is not None:
            raise ValueError(
                "KnowledgeEntity readable path has conflicting subject identity: "
                f"{path}"
            )
        anchored = dict(row)
        # Missing identity metadata is not silently rewritten during discovery.
        # The candidate values only complete the in-memory surface used by this
        # task; explicit metadata repair remains a separate document operation.
        anchored["entity_name"] = existing_name or candidate.name
        anchored["aliases"] = list(row.get("aliases") or ())
        return anchored

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
                        f"{context.source_file_id}:{target_id}".encode("utf-8")
                    ).hexdigest(),
                    target_locator_type="ENTITY_SURFACE",
                    target_locator_value=names.get(int(target_id), str(target_id)),
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
        if not name:
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
        )

    async def _collect_evidence(
        self,
        context: KnowledgeEntityTaskContext | Any,
        *,
        identity: KnowledgeEntityIdentity,
        existing_markdown: str,
        topics: Sequence[str] = (),
        updated_after: datetime | None = None,
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
                relation_created_at = relation.get("created_at")
                if updated_after is not None and (
                    not isinstance(relation_created_at, datetime)
                    or relation_created_at <= updated_after
                ):
                    continue
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
        relation_documents: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
        for relation in selected_relations:
            file_id = int(relation["source_fs_entry_id"])
            row = rows_by_id.get(file_id)
            if row is None:
                continue
            content = await self._read_markdown(row)
            relation_documents.append((relation, row, content))

        resolved_relation_contents = await self._search.resolve_markdown_texts(
            knowledge_base_id=identity.knowledge_base_id,
            texts=[content for _, _, content in relation_documents],
        )
        for (relation, row, _), content in zip(
            relation_documents,
            resolved_relation_contents,
            strict=True,
        ):
            file_id = int(relation["source_fs_entry_id"])
            mention_scopes = [("", ())]
            mention_scopes.extend((topic, (topic,)) for topic in topics)
            for topic, matched_topics in mention_scopes:
                selected_content = self._select_entity_sections(
                    content,
                    names=(identity.entity_name, *identity.aliases),
                    topics=matched_topics,
                    prefer_topics=bool(topic),
                    require_topic_match=bool(topic),
                )
                if not selected_content:
                    continue
                for selected_fragment in self._split_relation_evidence(
                    selected_content
                ):
                    fragments.append(
                        EvidenceFragment(
                            document_file_id=file_id,
                            document_path=str(row["file_path"]),
                            content=selected_fragment,
                            direct_mention=True,
                            explicit_reference=True,
                            relation_code=str(
                                relation.get("relation_code") or "MENTIONS"
                            ),
                            matched_topics=matched_topics,
                        )
                    )

        params = context.request_params or {}
        names = (identity.entity_name, *identity.aliases)
        topic_queries = [(topic, (topic,)) for topic in topics] or [("", ())]
        grouped_hits: list[tuple[int, str, Any]] = []
        for group_index, (topic, query_topics) in enumerate(topic_queries):
            batch_hits = await self._search.search(
                SearchRequest(
                    query=self._build_enrichment_search_query(
                        existing_markdown,
                        names=names,
                        topics=query_topics,
                    ),
                    kb_code_list=[context.kb_code],
                    top_k=int(params.get("topK", params.get("top_k", 20))),
                    search_mode="mixedRecall",
                    where=self._enrichment_search_where(
                        context.file_path, updated_after=updated_after
                    ),
                )
            )
            grouped_hits.extend((group_index, topic, hit) for hit in batch_hits)
        search_connection = await self._connection_factory()
        try:
            cursor = search_connection.cursor()
            semantic_candidates: list[tuple[int, str, Any, Mapping[str, Any]]] = []
            rows_by_path: dict[str, Mapping[str, Any] | None] = {}
            for group_index, topic, hit in grouped_hits:
                kb_code = str(self._value(hit, "kb_code", "knCode"))
                file_path = str(self._value(hit, "file_path", "filePath"))
                if kb_code != str(context.kb_code) or file_path == str(
                    context.file_path
                ):
                    continue
                if file_path not in rows_by_path:
                    rows_by_path[
                        file_path
                    ] = await self._entity_repository.get_file_with_metadata(
                        cursor,
                        knowledge_base_id=identity.knowledge_base_id,
                        file_path=file_path,
                    )
                row = rows_by_path[file_path]
                if row is None:
                    continue
                if int(row["kid"]) == identity.file_id:
                    continue
                row_updated_at = row.get("updated_at")
                if updated_after is not None and (
                    not isinstance(row_updated_at, datetime)
                    or row_updated_at <= updated_after
                ):
                    continue
                if (
                    row.get("document_kind") == "knowledgeEntity"
                    and row.get("entity_enriched") is not True
                ):
                    continue
                semantic_candidates.append((group_index, topic, hit, row))

            best_scores: dict[int, float] = {}
            for group_index, _, hit, _ in semantic_candidates:
                best_scores[group_index] = max(
                    best_scores.get(group_index, 0.0),
                    float(self._value(hit, "score") or 0.0),
                )
            document_counts: dict[int, int] = {}
            semantic_by_content: dict[tuple[int, str], int] = {}
            for group_index, topic, hit, row in semantic_candidates:
                content = str(self._value(hit, "chunk_text", "chunkText")).strip()
                score = float(self._value(hit, "score") or 0.0)
                best_score = best_scores.get(group_index, 0.0)
                file_id = int(row["kid"])
                names_entity_in_scope = self._text_mentions_entity(
                    content, names=(identity.entity_name, *identity.aliases)
                )
                source_entity_in_scope = (
                    file_id in selected_source_ids
                    or self._text_mentions_entity(
                        str(row["file_path"]),
                        names=(identity.entity_name, *identity.aliases),
                    )
                )
                normalized_content = normalize_surface(content)
                if (
                    not content
                    or (
                        best_score > 0 and score < best_score * MIN_SEMANTIC_SCORE_RATIO
                    )
                    or (
                        best_score > 0
                        and score < best_score * STRONG_SEMANTIC_SCORE_RATIO
                        and not names_entity_in_scope
                    )
                    or (not names_entity_in_scope and not source_entity_in_scope)
                    or self._is_stub_evidence(
                        content, names=(identity.entity_name, *identity.aliases)
                    )
                ):
                    continue
                content_key = (file_id, normalized_content)
                existing_index = semantic_by_content.get(content_key)
                if existing_index is not None:
                    existing = fragments[existing_index]
                    fragments[existing_index] = replace(
                        existing,
                        semantic_score=max(existing.semantic_score, score),
                        matched_topics=tuple(
                            dict.fromkeys(
                                (
                                    *existing.matched_topics,
                                    *((topic,) if topic else ()),
                                )
                            )
                        ),
                    )
                    continue
                if (
                    document_counts.get(file_id, 0)
                    >= MAX_SEMANTIC_FRAGMENTS_PER_DOCUMENT
                ):
                    continue
                document_counts[file_id] = document_counts.get(file_id, 0) + 1
                fragments.append(
                    EvidenceFragment(
                        document_file_id=file_id,
                        document_path=str(row["file_path"]),
                        content=content,
                        start=self._optional_int(
                            self._value(hit, "start_line", "startLine")
                        ),
                        end=self._optional_int(self._value(hit, "end_line", "endLine")),
                        semantic_score=score,
                        authorized=True,
                        matched_topics=(topic,) if topic else (),
                    )
                )
                semantic_by_content[content_key] = len(fragments) - 1
        finally:
            await search_connection.close()
        return fragments

    @staticmethod
    def _enrichment_search_where(
        file_path: str, *, updated_after: datetime | None = None
    ) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = [
            {"ne": {"fieldName": "filePath", "value": str(file_path)}},
            {
                "or": [
                    {
                        "ne": {
                            "fieldName": "documentKind",
                            "value": "knowledgeEntity",
                        }
                    },
                    {
                        "eq": {
                            "fieldName": ENTITY_ENRICHED_PROPERTY,
                            "value": True,
                        }
                    },
                ]
            },
        ]
        if updated_after is not None:
            normalized = (
                updated_after.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
            conditions.append({"gt": {"fieldName": "updatedAt", "value": normalized}})
        return {"and": conditions}

    @staticmethod
    def _build_enrichment_search_query(
        content: str, *, names: Sequence[str], topics: Sequence[str] = ()
    ) -> str:
        body = re.sub(
            r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)",
            "",
            content,
            count=1,
            flags=re.DOTALL,
        ).strip()
        factual_lines = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        parts = [
            *(name.strip() for name in names if name.strip()),
            *(
                f"{name.strip()} {topic.strip()}"
                for topic in topics
                if topic.strip()
                for name in names
                if name.strip()
            ),
            *factual_lines,
        ]
        return "\n".join(dict.fromkeys(parts))[:MAX_SEARCH_QUERY_CHARS]

    @staticmethod
    def _text_mentions_entity(content: str, *, names: Sequence[str]) -> bool:
        normalized_content = normalize_surface(content)
        return any(
            normalized_name in normalized_content
            for name in names
            if (normalized_name := normalize_surface(name))
        )

    @staticmethod
    def _is_stub_evidence(content: str, *, names: Sequence[str]) -> bool:
        normalized_names = {
            normalize_surface(name) for name in names if normalize_surface(name)
        }
        lines = [
            re.sub(r"^#{1,6}\s*", "", line).strip()
            for line in content.splitlines()
            if line.strip()
        ]
        return bool(lines and normalized_names) and all(
            normalize_surface(line) in normalized_names for line in lines
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _select_entity_sections(
        content: str,
        *,
        names: Sequence[str],
        topics: Sequence[str] = (),
        prefer_topics: bool = False,
        require_topic_match: bool = False,
    ) -> str:
        """Keep Entity-scoped source context, with a deterministic fallback."""

        normalized_names = tuple(
            normalize_surface(name) for name in names if normalize_surface(name)
        )
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
        normalized_topics = tuple(
            normalize_surface(topic) for topic in topics if normalize_surface(topic)
        )

        def matching_sections(
            terms: Sequence[str], *, prefer_heading_match: bool = False
        ) -> list[str]:
            matches = [
                section
                for section in sections
                if any(term in normalize_surface(section) for term in terms)
            ]
            if not prefer_heading_match:
                return matches

            def heading_matches(section: str) -> bool:
                heading = section.splitlines()[0] if section else ""
                normalized_heading = normalize_surface(
                    re.sub(r"^#{1,6}\s*", "", heading)
                )
                return any(term in normalized_heading for term in terms)

            heading_matched = [
                section for section in matches if heading_matches(section)
            ]
            return heading_matched or matches

        candidate_sections = (
            matching_sections(normalized_topics, prefer_heading_match=True)
            if prefer_topics
            else []
        )
        if require_topic_match and not candidate_sections:
            return ""
        if not candidate_sections:
            candidate_sections = matching_sections(normalized_names)
        if not candidate_sections:
            candidate_sections = matching_sections(normalized_topics)
        if not candidate_sections:
            discovery_context = build_discovery_context(body)
            candidate_sections = [
                reference.content for reference in discovery_context.source_references
            ]

        matches: list[str] = []
        total_chars = 0
        for section in candidate_sections:
            remaining = MAX_RELATION_DOCUMENT_CHARS - total_chars
            if remaining <= 0:
                break
            selected = KnowledgeEntityTaskWorker._bounded_context_around_terms(
                section,
                terms=topics if prefer_topics else (*names, *topics),
                limit=remaining,
            )
            matches.append(selected)
            total_chars += len(selected)
            if len(matches) >= MAX_MATCHED_SECTIONS_PER_DOCUMENT:
                break
        return "\n\n".join(matches)

    @staticmethod
    def _bounded_context_around_terms(
        content: str, *, terms: Sequence[str], limit: int
    ) -> str:
        """Keep a long source section centered on its first Entity/Topic mention."""

        if len(content) <= limit:
            return content
        folded = content.casefold()
        heading_positions: list[tuple[bool, int, int]] = []
        line_start = 0
        normalized_terms = tuple(
            (term.casefold(), normalize_surface(term)) for term in terms if term.strip()
        )
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            folded_line = stripped.casefold()
            normalized_line = normalize_surface(
                re.sub(r"^(?:#{1,6}\s*|\*\*|__)", "", stripped)
            )
            if len(stripped) <= 200 and any(
                folded_term in folded_line for folded_term, _ in normalized_terms
            ):
                starts_with_term = any(
                    normalized_line.startswith(normalized_term)
                    for _, normalized_term in normalized_terms
                )
                heading_positions.append(
                    (not starts_with_term, len(stripped), line_start)
                )
            line_start += len(line)
        if heading_positions:
            mention = min(heading_positions)[2]
        else:
            positions = [
                position
                for term in terms
                if term.strip()
                and (position := folded.find(term.strip().casefold())) >= 0
            ]
            if not positions:
                return content[:limit]
            mention = min(positions)
        # Keep the matched Topic in the first evidence fragment. Topic × Source
        # quota allocation may only have room for that fragment, so placing the
        # anchor several fragments later would satisfy the quota with background
        # text instead of the Topic's substantive section.
        start = max(0, mention - min(limit // 4, MAX_RELATION_FRAGMENT_CHARS // 4))
        end = min(len(content), start + limit)
        start = max(0, end - limit)
        return content[start:end]

    @staticmethod
    def _split_relation_evidence(content: str) -> tuple[str, ...]:
        """Split selected source context without losing text to fragment limits."""

        blocks = [
            block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()
        ]
        fragments: list[str] = []
        current = ""
        for block in blocks:
            pending = block
            while pending:
                remaining = MAX_RELATION_FRAGMENT_CHARS - len(current)
                if remaining <= 0:
                    fragments.append(current)
                    current = ""
                    remaining = MAX_RELATION_FRAGMENT_CHARS
                if len(pending) <= remaining:
                    current = f"{current}\n\n{pending}".strip()
                    pending = ""
                    continue
                if current:
                    fragments.append(current)
                    current = ""
                    continue
                fragments.append(pending[:MAX_RELATION_FRAGMENT_CHARS])
                pending = pending[MAX_RELATION_FRAGMENT_CHARS:]
        if current:
            fragments.append(current)
        return tuple(fragments)

    @staticmethod
    def _candidate_aliases(candidate: DiscoveredEntity) -> tuple[str, ...]:
        values = [*candidate.aliases]
        seen = {normalize_surface(candidate.name)}
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
    def _render_entity_markdown(
        *,
        entity_name: str,
        body: str,
        aliases: Sequence[str],
        subject_file_id: int | None,
        entity_type: str | None,
        entity_enriched: bool,
    ) -> str:
        metadata: dict[str, Any] = {
            "documentKind": "knowledgeEntity",
            "processingCapabilities": ["entityEnrich"],
            "entityName": entity_name,
            "aliases": list(aliases),
            ENTITY_ENRICHED_PROPERTY: entity_enriched,
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
