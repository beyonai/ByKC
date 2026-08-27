"""Candidate adjudication and durable KnowledgeEntity asset operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import DeleteKnowledgeItemRequest
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    KnowledgeEntityLLM,
    _complete_strict_json,
    normalize_surface,
)

ENTITY_EMBEDDING_REPRESENTATION_VERSION = "entity-embedding/3"


class SynonymDecision(StrEnum):
    SAME = "SAME"
    DIFFERENT = "DIFFERENT"
    UNCERTAIN = "UNCERTAIN"


class ResolutionMethod(StrEnum):
    EXACT_CANONICAL = "EXACT_CANONICAL"
    EXACT_ALIAS = "EXACT_ALIAS"
    SYNONYM_ADJUDICATED = "SYNONYM_ADJUDICATED"
    CREATED_NEW = "CREATED_NEW"
    AMBIGUOUS_UNMERGED = "AMBIGUOUS_UNMERGED"


@dataclass(frozen=True, slots=True)
class SynonymAdjudication:
    decision: SynonymDecision
    selected_candidate_id: int | None = None
    alias_to_add: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class EntityResolution:
    entity_id: int
    canonical_name: str
    fs_entry_id: int | None
    method: ResolutionMethod
    alias_added: str | None = None
    candidate_count: int = 0
    created: bool = False
    warnings: tuple[str, ...] = ()


class KnowledgeEntitySynonymAdjudicator:
    """Ask an LLM to decide identity only within an already bounded top-K."""

    _SYSTEM_PROMPT = """\
你是知识实体同义词裁决器。候选召回只是近似信号，你必须结合提及、当前提及描述与原文证据、Subject、类型、规范名、别名和候选 description 判断是否为同一稳定实体。description 是候选的稳定身份描述，不是同一性保证；所属、组成、产品、平台、模块或其他关联关系不等于别名关系。

只输出 JSON 对象：decision 只能是 SAME、DIFFERENT、UNCERTAIN；SAME 时 selectedCandidateId 必须来自给定候选，canonicalName 必须与候选完全一致，aliasToAdd 必须是输入提及；其余情况 selectedCandidateId、canonicalName、aliasToAdd 为 null。reasonCode 使用简短大写下划线枚举。
""".strip()

    def __init__(self, llm: KnowledgeEntityLLM):
        self._llm = llm

    async def adjudicate(
        self,
        *,
        mention: str,
        mention_description: str,
        evidence: str,
        subject_name: str | None,
        entity_type: str | None,
        candidates: Sequence[Mapping[str, Any]],
    ) -> SynonymAdjudication:
        if not candidates:
            return SynonymAdjudication(SynonymDecision.DIFFERENT)
        payload = {
            "mention": mention,
            "mentionDescription": mention_description,
            "evidence": evidence,
            "subject": subject_name,
            "entityType": entity_type,
            "candidates": [
                {
                    "candidateId": int(item["resolved_entity_id"]),
                    "canonicalName": item["canonical_entity_name"],
                    "aliases": list(item.get("aliases") or ()),
                    "entityType": item.get("entity_type"),
                    "description": item.get("description"),
                    "score": float(item.get("score") or 0.0),
                }
                for item in candidates
            ],
        }
        raw, _ = await _complete_strict_json(
            self._llm,
            [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            expected_type=dict,
            max_attempts=2,
            retry_backoff_seconds=0.0,
            sleep=asyncio.sleep,
            operation="entity_synonym_adjudication",
        )
        try:
            decision = SynonymDecision(str(raw.get("decision") or "UNCERTAIN"))
        except ValueError:
            decision = SynonymDecision.UNCERTAIN
        if decision is not SynonymDecision.SAME:
            return SynonymAdjudication(
                decision=decision,
                reason_code=str(raw.get("reasonCode") or "") or None,
            )
        candidate_by_id = {int(item["resolved_entity_id"]): item for item in candidates}
        try:
            selected_id = int(raw.get("selectedCandidateId"))
        except (TypeError, ValueError):
            return SynonymAdjudication(
                SynonymDecision.UNCERTAIN, reason_code="INVALID_CANDIDATE_ID"
            )
        selected = candidate_by_id.get(selected_id)
        if (
            selected is None
            or raw.get("canonicalName") != selected["canonical_entity_name"]
            or normalize_surface(str(raw.get("aliasToAdd") or ""))
            != normalize_surface(mention)
        ):
            return SynonymAdjudication(
                SynonymDecision.UNCERTAIN, reason_code="INVALID_SAME_PAYLOAD"
            )
        return SynonymAdjudication(
            decision=SynonymDecision.SAME,
            selected_candidate_id=selected_id,
            alias_to_add=mention,
            reason_code=str(raw.get("reasonCode") or "") or None,
        )


class KnowledgeEntityAssetService:
    """Resolve extracted candidates and govern canonical/alias lifecycle."""

    def __init__(
        self,
        *,
        connection_factory: Any,
        knowledge_base_repository: Any,
        asset_repository: Any,
        fs_entry_repository: Any,
        file_metadata_repository: Any,
        embedding_service: Any,
        adjudicator: Any,
        ingestion_service: Any | None = None,
        top_k: int = 3,
        exact_alias_enabled: bool = True,
        embedding_index_enabled: bool = True,
        adjudication_enabled: bool = True,
    ) -> None:
        self._connection_factory = connection_factory
        self._knowledge_base_repository = knowledge_base_repository
        self._repository = asset_repository
        self._fs_entries = fs_entry_repository
        self._file_metadata = file_metadata_repository
        self._embedding = embedding_service
        self._adjudicator = adjudicator
        self._ingestion = ingestion_service
        self._top_k = max(1, min(int(top_k), 10))
        self._exact_alias_enabled = exact_alias_enabled
        self._embedding_index_enabled = embedding_index_enabled
        self._adjudication_enabled = adjudication_enabled

    async def resolve_candidate(
        self,
        *,
        knowledge_base_id: int,
        entity_name: str,
        aliases: Sequence[str],
        subject_entity_id: int | None,
        subject_name: str | None,
        entity_type: str | None,
        description: str,
        evidence: str,
    ) -> EntityResolution:
        normalized_name = normalize_surface(entity_name)
        normalized_aliases = [
            value
            for value in (normalize_surface(alias) for alias in aliases)
            if value and value != normalized_name
        ]
        surfaces = list(dict.fromkeys([normalized_name, *normalized_aliases]))
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            exact_rows = await self._repository.resolve_exact(
                cursor,
                knowledge_base_id=knowledge_base_id,
                normalized_surfaces=surfaces,
            )
        finally:
            await connection.close()
        compatible = self._compatible_rows(
            exact_rows,
            subject_entity_id=subject_entity_id,
            entity_type=entity_type,
        )
        exact_ids = (
            {int(row["resolved_entity_id"]) for row in compatible}
            if self._exact_alias_enabled
            else set()
        )
        if len(exact_ids) == 1:
            selected = next(
                row for row in compatible if int(row["resolved_entity_id"]) in exact_ids
            )
            return EntityResolution(
                entity_id=int(selected["resolved_entity_id"]),
                canonical_name=str(selected["canonical_entity_name"]),
                fs_entry_id=self._optional_int(selected.get("fs_entry_id")),
                method=(
                    ResolutionMethod.EXACT_ALIAS
                    if selected.get("matched_name_role") == "alias"
                    else ResolutionMethod.EXACT_CANONICAL
                ),
                candidate_count=len(exact_ids),
            )
        warnings: list[str] = []
        if len(exact_ids) > 1:
            warnings.append("AMBIGUOUS_SURFACE")
            return await self._create_new(
                knowledge_base_id=knowledge_base_id,
                entity_name=entity_name,
                aliases=aliases,
                subject_entity_id=subject_entity_id,
                entity_type=entity_type,
                description=description,
                method=ResolutionMethod.AMBIGUOUS_UNMERGED,
                warnings=warnings,
            )

        candidates: list[dict[str, Any]] = []
        if self._embedding_index_enabled and self._adjudication_enabled:
            try:
                full_query = self._full_query_text(
                    entity_name=entity_name,
                    aliases=aliases,
                    subject_name=subject_name,
                    entity_type=entity_type,
                    description=description,
                )
                full_embedding = await self._embedding.embed_query(full_query)
                connection = await self._connection_factory()
                try:
                    cursor = connection.cursor()
                    candidates = await self._repository.search_similar(
                        cursor,
                        knowledge_base_id=knowledge_base_id,
                        full_embedding=full_embedding,
                        subject_entity_id=subject_entity_id,
                        entity_type=entity_type,
                        limit=self._top_k,
                    )
                finally:
                    await connection.close()
            except Exception:
                warnings.append("ENTITY_EMBEDDING_RECALL_UNAVAILABLE")
        if candidates:
            try:
                adjudication = await self._adjudicator.adjudicate(
                    mention=entity_name,
                    mention_description=description,
                    evidence=evidence,
                    subject_name=subject_name,
                    entity_type=entity_type,
                    candidates=candidates,
                )
            except Exception:
                adjudication = SynonymAdjudication(SynonymDecision.UNCERTAIN)
                warnings.append("SYNONYM_ADJUDICATION_UNAVAILABLE")
            if (
                adjudication.decision is SynonymDecision.SAME
                and adjudication.selected_candidate_id is not None
            ):
                selected = next(
                    row
                    for row in candidates
                    if int(row["resolved_entity_id"])
                    == adjudication.selected_candidate_id
                )
                alias_added = await self.add_alias(
                    knowledge_base_id=knowledge_base_id,
                    entity_id=adjudication.selected_candidate_id,
                    alias=entity_name,
                )
                resolution_warnings: list[str] = []
                try:
                    await self.refresh_embeddings(
                        knowledge_base_id=knowledge_base_id,
                        entity_id=adjudication.selected_candidate_id,
                    )
                except Exception:
                    logger.exception(
                        "knowledge entity embedding refresh failed after alias write: entity_id=%s",
                        adjudication.selected_candidate_id,
                    )
                    resolution_warnings.append("ENTITY_EMBEDDING_PENDING")
                return EntityResolution(
                    entity_id=adjudication.selected_candidate_id,
                    canonical_name=str(selected["canonical_entity_name"]),
                    fs_entry_id=self._optional_int(selected.get("fs_entry_id")),
                    method=ResolutionMethod.SYNONYM_ADJUDICATED,
                    alias_added=entity_name if alias_added else None,
                    candidate_count=len(candidates),
                    warnings=tuple(resolution_warnings),
                )

        return await self._create_new(
            knowledge_base_id=knowledge_base_id,
            entity_name=entity_name,
            aliases=aliases,
            subject_entity_id=subject_entity_id,
            entity_type=entity_type,
            description=description,
            method=ResolutionMethod.CREATED_NEW,
            warnings=warnings,
        )

    async def upsert_topics(
        self,
        *,
        knowledge_base_id: int,
        topics: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add or reuse canonical Topics without a separate evidence registry."""

        connection = await self._connection_factory()
        actions: list[dict[str, Any]] = []
        try:
            cursor = connection.cursor()
            for item in topics:
                name = str(item["name"]).strip()
                normalized_name = normalize_surface(name)
                if not normalized_name:
                    continue
                topic = await self._repository.upsert_topic(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    owner_entity_id=int(item["owner_entity_id"]),
                    name=name,
                    normalized_name=normalized_name,
                )
                actions.append(
                    {
                        "action": "TOPIC_ANCHORED",
                        "topicEntityId": int(topic["kid"]),
                        "ownerEntityId": int(item["owner_entity_id"]),
                        "topicName": name,
                    }
                )
            await connection.commit()
            return actions
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _create_new(
        self,
        *,
        knowledge_base_id: int,
        entity_name: str,
        aliases: Sequence[str],
        subject_entity_id: int | None,
        entity_type: str | None,
        description: str,
        method: ResolutionMethod,
        warnings: Sequence[str],
    ) -> EntityResolution:
        normalized_name = normalize_surface(entity_name)
        if not normalized_name:
            raise KnowledgeBaseValidationError("entity name must not be empty")
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            await self._repository.advisory_lock_surface(
                cursor,
                knowledge_base_id=knowledge_base_id,
                normalized_surface=normalized_name,
            )
            if subject_entity_id is not None:
                subject = await self._repository.get_by_id(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    entity_id=subject_entity_id,
                )
                if subject is None or subject.get("name_role") != "canonical":
                    raise KnowledgeBaseValidationError(
                        "subject entity must be a canonical entity in the same knowledge base"
                    )
            existing = self._compatible_rows(
                await self._repository.resolve_exact(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    normalized_surfaces=[normalized_name],
                ),
                subject_entity_id=subject_entity_id,
                entity_type=entity_type,
            )
            canonical_existing = [
                row for row in existing if row.get("matched_name_role") == "canonical"
            ]
            if len(canonical_existing) == 1:
                selected = canonical_existing[0]
                await connection.commit()
                return EntityResolution(
                    entity_id=int(selected["resolved_entity_id"]),
                    canonical_name=str(selected["canonical_entity_name"]),
                    fs_entry_id=self._optional_int(selected.get("fs_entry_id")),
                    method=ResolutionMethod.EXACT_CANONICAL,
                    created=False,
                )
            created = await self._repository.create_canonical(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_name=entity_name.strip(),
                normalized_entity_name=normalized_name,
                subject_entity_id=subject_entity_id,
                entity_type=entity_type.strip() if entity_type else None,
                description=description.strip() or None,
            )
            entity_id = int(created["kid"])
            for alias in dict.fromkeys(str(item).strip() for item in aliases):
                normalized_alias = normalize_surface(alias)
                if not normalized_alias or normalized_alias == normalized_name:
                    continue
                await self._repository.add_alias(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    canonical_entity_id=entity_id,
                    alias=alias,
                    normalized_alias=normalized_alias,
                )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        resolution_warnings = list(warnings)
        try:
            await self.refresh_embeddings(
                knowledge_base_id=knowledge_base_id, entity_id=entity_id
            )
        except Exception:
            logger.exception(
                "knowledge entity embedding refresh failed after create: entity_id=%s",
                entity_id,
            )
            resolution_warnings.append("ENTITY_EMBEDDING_PENDING")
        return EntityResolution(
            entity_id=entity_id,
            canonical_name=entity_name.strip(),
            fs_entry_id=None,
            method=method,
            created=True,
            warnings=tuple(resolution_warnings),
        )

    async def add_alias(
        self, *, knowledge_base_id: int, entity_id: int, alias: str
    ) -> bool:
        normalized_alias = normalize_surface(alias)
        if not normalized_alias:
            return False
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            await self._repository.advisory_lock_surface(
                cursor,
                knowledge_base_id=knowledge_base_id,
                normalized_surface=normalized_alias,
            )
            canonical = await self._repository.get_by_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
            )
            if canonical is None or canonical.get("name_role") != "canonical":
                raise KnowledgeBaseValidationError("canonical entity not found")
            if normalize_surface(str(canonical["entity_name"])) == normalized_alias:
                await connection.commit()
                return False
            matches = await self._repository.resolve_exact(
                cursor,
                knowledge_base_id=knowledge_base_id,
                normalized_surfaces=[normalized_alias],
            )
            other_ids = {
                int(row["resolved_entity_id"])
                for row in matches
                if int(row["resolved_entity_id"]) != entity_id
            }
            if other_ids:
                await connection.commit()
                return False
            already_exists = any(
                int(row["resolved_entity_id"]) == entity_id for row in matches
            )
            if not already_exists:
                await self._repository.add_alias(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    canonical_entity_id=entity_id,
                    alias=alias.strip(),
                    normalized_alias=normalized_alias,
                )
                await self._repository.delete_embeddings(
                    cursor,
                    entity_id=entity_id,
                    representations=["full"],
                )
            await connection.commit()
            return not already_exists
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def attach_file(
        self,
        *,
        knowledge_base_id: int,
        entity_id: int,
        fs_entry_id: int,
    ) -> None:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            file_row = await self._fs_entries.get_entry_by_id(
                cursor, entry_id=fs_entry_id
            )
            if (
                file_row is None
                or int(file_row["knowledge_base_id"]) != knowledge_base_id
                or file_row.get("entry_type") != "FILE"
                or file_row.get("is_deleted") is True
            ):
                raise KnowledgeBaseValidationError(
                    "file anchor must be a live file in the same knowledge base"
                )
            document_kind = await self._file_metadata.get_active_value(
                cursor,
                fs_entry_id=fs_entry_id,
                property_name="documentKind",
                value_type="string",
            )
            if (
                document_kind is None
                or document_kind.get("value_string") != "knowledgeEntity"
            ):
                raise KnowledgeBaseValidationError(
                    "file anchor must have documentKind=knowledgeEntity"
                )
            await self._repository.attach_fs_entry(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
                fs_entry_id=fs_entry_id,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def refresh_embeddings(
        self, *, knowledge_base_id: int, entity_id: int
    ) -> None:
        if not self._embedding_index_enabled:
            return
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            entity = await self._repository.get_by_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
            )
            if entity is None or entity.get("name_role") != "canonical":
                return
            subject_name = None
            if entity.get("subject_entity_id") is not None:
                subject = await self._repository.get_by_id(
                    cursor,
                    knowledge_base_id=knowledge_base_id,
                    entity_id=int(entity["subject_entity_id"]),
                )
                subject_name = str(subject["entity_name"]) if subject else None
            full_text = self._full_query_text(
                entity_name=str(entity["entity_name"]),
                aliases=entity.get("aliases") or (),
                subject_name=subject_name,
                entity_type=entity.get("entity_type"),
                description=str(entity.get("description") or ""),
            )
            expected = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
            existing = await self._repository.get_embedding_hashes(
                cursor, entity_id=entity_id
            )
        finally:
            await connection.close()
        if existing.get("full") == expected:
            return
        embedding = await self._embedding.embed_query(full_text)
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            await self._repository.upsert_embedding(
                cursor,
                entity_id=entity_id,
                representation="full",
                source_content_hash=expected,
                embedding=embedding,
            )
            logger.info(
                "knowledge entity embedding refreshed: table=%s "
                "entity_id=%s representation=full source_content_hash=%s",
                self._repository.entity_embedding_table_name,
                entity_id,
                expected,
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def delete_alias(
        self, *, kb_code: str, entity_id: int, alias_id: int
    ) -> dict[str, int]:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, kb_code)
            canonical = await self._repository.get_by_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
            )
            if canonical is None or canonical.get("name_role") != "canonical":
                raise KnowledgeBaseValidationError("canonical entity not found")
            deleted = await self._repository.delete_alias(
                cursor,
                knowledge_base_id=knowledge_base_id,
                canonical_entity_id=entity_id,
                alias_id=alias_id,
            )
            if not deleted:
                raise KnowledgeBaseValidationError("alias not found for entity")
            await self._repository.delete_embeddings(
                cursor, entity_id=entity_id, representations=["full"]
            )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        try:
            await self.refresh_embeddings(
                knowledge_base_id=knowledge_base_id, entity_id=entity_id
            )
        except Exception:
            pass
        return {"deletedEntityCount": 0, "deletedAliasCount": 1, "deletedFileCount": 0}

    async def delete_entity(self, *, kb_code: str, entity_id: int) -> dict[str, int]:
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            knowledge_base_id = await self._resolve_kb(cursor, kb_code)
            entity = await self._repository.get_by_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
                for_update=True,
            )
            if entity is None or entity.get("name_role") != "canonical":
                raise KnowledgeBaseValidationError("canonical entity not found")
            children = await self._repository.list_direct_children(
                cursor,
                knowledge_base_id=knowledge_base_id,
                subject_entity_id=entity_id,
            )
            if children:
                raise KnowledgeBaseValidationError(
                    "canonical entity is still referenced as Subject"
                )
            fs_entry_id = self._optional_int(entity.get("fs_entry_id"))
            file_path = None
            if fs_entry_id is not None:
                file_row = await self._fs_entries.get_entry_by_id(
                    cursor, entry_id=fs_entry_id
                )
                if file_row and file_row.get("is_deleted") is not True:
                    file_path = str(file_row["virtual_path"])
            alias_count = len(entity.get("aliases") or ())
        finally:
            await connection.close()
        deleted_file_count = 0
        if file_path is not None:
            if self._ingestion is None:
                raise KnowledgeBaseValidationError(
                    "entity file deletion service is not configured"
                )
            await self._ingestion.delete_knowledge_item(
                DeleteKnowledgeItemRequest(kb_code=kb_code, file_path=file_path)
            )
            deleted_file_count = 1
        connection = await self._connection_factory()
        try:
            cursor = connection.cursor()
            current = await self._repository.get_by_id(
                cursor,
                knowledge_base_id=knowledge_base_id,
                entity_id=entity_id,
                for_update=True,
            )
            if current is None:
                raise KnowledgeBaseValidationError("canonical entity not found")
            children = await self._repository.list_direct_children(
                cursor,
                knowledge_base_id=knowledge_base_id,
                subject_entity_id=entity_id,
            )
            if children:
                raise KnowledgeBaseValidationError(
                    "canonical entity is still referenced as Subject"
                )
            await self._repository.delete_entity(cursor, entity_id=entity_id)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
        return {
            "deletedEntityCount": 1,
            "deletedAliasCount": alias_count,
            "deletedFileCount": deleted_file_count,
        }

    async def _resolve_kb(self, cursor: Any, kb_code: str) -> int:
        row = await self._knowledge_base_repository.get_by_code(cursor, kb_code)
        if row is None:
            raise KnowledgeBaseValidationError(f"knowledge base not found: {kb_code}")
        return int(row["kid"])

    @classmethod
    def _compatible_rows(
        cls,
        rows: Sequence[Mapping[str, Any]],
        *,
        subject_entity_id: int | None,
        entity_type: str | None,
    ) -> list[Mapping[str, Any]]:
        return [
            row
            for row in rows
            if cls._optional_int(row.get("subject_entity_id")) == subject_entity_id
            and cls._type_compatible(row.get("entity_type"), entity_type)
        ]

    @staticmethod
    def _type_compatible(existing: Any, requested: str | None) -> bool:
        if not existing or not requested:
            return True
        return normalize_surface(str(existing)) == normalize_surface(requested)

    @staticmethod
    def _full_query_text(
        *,
        entity_name: str,
        aliases: Sequence[str],
        subject_name: str | None,
        entity_type: str | None,
        description: str,
    ) -> str:
        return "\n".join(
            value
            for value in (
                f"representationVersion={ENTITY_EMBEDDING_REPRESENTATION_VERSION}",
                "representation=full",
                entity_name.strip(),
                *(str(alias).strip() for alias in sorted(set(aliases))),
                (subject_name or "").strip(),
                (entity_type or "").strip(),
                description.strip(),
            )
            if value
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return int(value) if value is not None else None
