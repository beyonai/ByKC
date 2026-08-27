"""Real OpenGauss integration coverage for v2 entity assets and delete APIs."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from psycopg import sql

from by_qa.config import get_settings
from by_qa.knowledge_base.api.routes import register_routes
from by_qa.knowledge_base.api.schemas import DeleteKnowledgeBaseRequest
from by_qa.knowledge_base.infrastructure.database import build_connection_factory
from by_qa.knowledge_base.repositories.file_metadata_value_repository import (
    FileMetadataValueRepository,
)
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.knowledge_entity_asset_repository import (
    KnowledgeEntityAssetRepository,
)
from by_qa.knowledge_base.repositories.knowledge_fs_entry_repository import (
    KnowledgeFsEntryRepository,
)
from by_qa.knowledge_base.services.bootstrap_service import (
    KnowledgeBaseSchemaBootstrapService,
)
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService
from by_qa.knowledge_base.services.knowledge_entity_synonym_resolution import (
    KnowledgeEntityAssetService,
    SynonymAdjudication,
    SynonymDecision,
)
from by_qa.knowledge_base.services.knowledge_item_ingestion_service import (
    KnowledgeItemIngestionService,
)


class _Embedding:
    async def embed_query(self, query: str) -> list[float]:
        lowered = query.casefold()
        if "baseqa" in lowered or "基础问答" in query:
            return [1.0, 0.01, 0.0]
        return [0.0, 1.0, 0.01]


class _SameAdjudicator:
    async def adjudicate(self, *, mention, candidates, **_kwargs):
        assert mention == "ByKC-基础问答引擎"
        assert candidates[0]["description"] == "ByKC base QA engine."
        return SynonymAdjudication(
            decision=SynonymDecision.SAME,
            selected_candidate_id=int(candidates[0]["resolved_entity_id"]),
            alias_to_add=mention,
            reason_code="TRANSLATION_AND_CONTEXT_MATCH",
        )


class _Storage:
    storage_path_bound_to_logical_path = False


class _DeleteApi:
    def __init__(self, service: KnowledgeEntityAssetService):
        self.service = service

    async def delete_knowledge_entity(self, request):
        return await self.service.delete_entity(
            kb_code=request.kb_code, entity_id=request.entity_id
        )

    async def delete_knowledge_entity_alias(self, request):
        return await self.service.delete_alias(
            kb_code=request.kb_code,
            entity_id=request.entity_id,
            alias_id=request.alias_id,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_synonym_resolution_file_lifecycle_and_delete_apis() -> None:
    base_settings = get_settings()
    if not base_settings.resolved_kb_opengauss_dsn:
        pytest.fail(
            "Entity asset integration requires DB_HOST, DB_USER, and DB_PASS",
            pytrace=False,
        )
    schema_name = f"kb_entity_asset_it_{uuid4().hex[:16]}"
    settings = base_settings.model_copy(update={"db_schema": schema_name})
    connection_factory = build_connection_factory(settings)
    bootstrap = KnowledgeBaseSchemaBootstrapService(
        embedding_model_name="entity-asset-it",
        embedding_dimension=3,
    )
    setup = await connection_factory()
    await setup.close()
    try:
        connection = await connection_factory()
        await bootstrap.apply(connection)
        await connection.close()

        kb_repository = KnowledgeBaseRepository()
        fs_repository = KnowledgeFsEntryRepository()
        metadata_repository = FileMetadataValueRepository()
        asset_repository = KnowledgeEntityAssetRepository(
            bootstrap.entity_embedding_table_name
        )
        ingestion = KnowledgeItemIngestionService(
            connection_factory=connection_factory,
            knowledge_base_repository=kb_repository,
            knowledge_fs_entry_repository=fs_repository,
            knowledge_item_chunk_repository=None,
            retrieval_projection_repository=None,
            storage_provider=_Storage(),
            embedding_dimension=3,
            knowledge_entity_asset_repository=asset_repository,
        )
        service = KnowledgeEntityAssetService(
            connection_factory=connection_factory,
            knowledge_base_repository=kb_repository,
            asset_repository=asset_repository,
            fs_entry_repository=fs_repository,
            file_metadata_repository=metadata_repository,
            embedding_service=_Embedding(),
            adjudicator=_SameAdjudicator(),
            ingestion_service=ingestion,
        )

        connection = await connection_factory()
        cursor = connection.cursor()
        kb_row = await kb_repository.create_knowledge_base(
            cursor, kb_name=f"Entity asset IT {uuid4().hex[:8]}", kb_description=None
        )
        await connection.commit()
        await connection.close()
        kb_id = int(kb_row["kid"])
        kb_code = str(kb_id)

        canonical = await service.resolve_candidate(
            knowledge_base_id=kb_id,
            entity_name="ByKC-BaseQAEngine",
            aliases=(),
            subject_entity_id=None,
            subject_name=None,
            entity_type="engine",
            description="ByKC base QA engine.",
            evidence="ByKC uses BaseQAEngine.",
        )
        resolved = await service.resolve_candidate(
            knowledge_base_id=kb_id,
            entity_name="ByKC-基础问答引擎",
            aliases=(),
            subject_entity_id=None,
            subject_name=None,
            entity_type="engine",
            description="ByKC 的基础问答引擎。",
            evidence="ByKC 的基础问答引擎负责回答。",
        )
        assert resolved.entity_id == canonical.entity_id
        assert resolved.canonical_name == "ByKC-BaseQAEngine"
        assert resolved.alias_added == "ByKC-基础问答引擎"

        exact = await service.resolve_candidate(
            knowledge_base_id=kb_id,
            entity_name="ByKC-基础问答引擎",
            aliases=(),
            subject_entity_id=None,
            subject_name=None,
            entity_type="engine",
            description="ByKC 的基础问答引擎。",
            evidence="再次提及。",
        )
        assert exact.entity_id == canonical.entity_id
        assert exact.method.value == "EXACT_ALIAS"

        connection = await connection_factory()
        cursor = connection.cursor()
        isolated_kb = await kb_repository.create_knowledge_base(
            cursor,
            kb_name=f"Isolated entity KB {uuid4().hex[:8]}",
            kb_description=None,
        )
        await connection.commit()
        await connection.close()
        isolated_resolution = await service.resolve_candidate(
            knowledge_base_id=int(isolated_kb["kid"]),
            entity_name="ByKC-基础问答引擎",
            aliases=(),
            subject_entity_id=None,
            subject_name=None,
            entity_type="engine",
            description="另一个知识库的基础问答引擎。",
            evidence="另一个知识库独立使用该名称。",
        )
        assert isolated_resolution.method.value == "CREATED_NEW"
        assert isolated_resolution.canonical_name == "ByKC-基础问答引擎"
        assert isolated_resolution.entity_id != canonical.entity_id

        connection = await connection_factory()
        cursor = connection.cursor()
        file_row = await fs_repository.create_file_entry(
            cursor,
            knowledge_base_id=kb_id,
            full_path="KnowledgeEntity/ByKC-BaseQAEngine.md",
        )
        file_id = int(file_row["kid"])
        await metadata_repository.upsert_value(
            cursor,
            fs_entry_id=file_id,
            knowledge_base_id=kb_id,
            property_name="documentKind",
            value_type="string",
            value="knowledgeEntity",
        )
        await connection.commit()
        await connection.close()
        await service.attach_file(
            knowledge_base_id=kb_id,
            entity_id=canonical.entity_id,
            fs_entry_id=file_id,
        )

        await ingestion.delete_knowledge_item(
            type(
                "Request",
                (),
                {
                    "kb_code": kb_code,
                    "file_path": "/KnowledgeEntity/ByKC-BaseQAEngine.md",
                },
            )()
        )
        connection = await connection_factory()
        cursor = connection.cursor()
        retained = await asset_repository.get_by_id(
            cursor, knowledge_base_id=kb_id, entity_id=canonical.entity_id
        )
        assert retained is not None
        assert retained["fs_entry_id"] is None
        alias_id = await _alias_id(cursor, canonical.entity_id)
        with pytest.raises(
            ValueError, match="entity embedding requires a canonical entity"
        ):
            await asset_repository.upsert_embedding(
                cursor,
                entity_id=alias_id,
                representation="full",
                source_content_hash="0" * 64,
                embedding=[1.0, 0.0, 0.0],
            )
        assert (
            await _vector_count(
                cursor, bootstrap.entity_embedding_table_name, canonical.entity_id
            )
            == 1
        )
        await connection.close()

        app = FastAPI()

        async def _api_service():
            return _DeleteApi(service)

        register_routes(
            app,
            get_knowledge_base_service=lambda: None,
            get_knowledge_item_ingestion_service=lambda: None,
            get_knowledge_item_search_service=lambda: None,
            get_document_chunking_service=lambda: None,
            get_metadata_search_service=lambda: None,
            get_file_metadata_query_service=lambda: None,
            get_knowledge_entity_processing_service=_api_service,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            parent = await service.resolve_candidate(
                knowledge_base_id=kb_id,
                entity_name="ByKC",
                aliases=(),
                subject_entity_id=None,
                subject_name=None,
                entity_type="system",
                description="ByKC is a system.",
                evidence="ByKC is the system.",
            )
            child = await service.resolve_candidate(
                knowledge_base_id=kb_id,
                entity_name="ByKC-Worker",
                aliases=(),
                subject_entity_id=parent.entity_id,
                subject_name="ByKC",
                entity_type="component",
                description="ByKC-Worker is a ByKC component.",
                evidence="Worker belongs to ByKC.",
            )
            blocked = await client.post(
                "/api/v1/knowledgeEntities/delete",
                json={"knCode": kb_code, "entityId": parent.entity_id},
            )
            assert blocked.json()["resultCode"] == "-1"
            assert "Subject" in blocked.json()["resultMsg"]
            await service.delete_entity(kb_code=kb_code, entity_id=child.entity_id)
            await service.delete_entity(kb_code=kb_code, entity_id=parent.entity_id)

            alias_response = await client.post(
                "/api/v1/knowledgeEntities/aliases/delete",
                json={
                    "knCode": kb_code,
                    "entityId": canonical.entity_id,
                    "aliasId": alias_id,
                },
            )
            assert alias_response.json()["resultObject"] == {
                "deletedEntityCount": 0,
                "deletedAliasCount": 1,
                "deletedFileCount": 0,
            }
            connection = await connection_factory()
            cursor = connection.cursor()
            replacement_file = await fs_repository.create_file_entry(
                cursor,
                knowledge_base_id=kb_id,
                full_path="KnowledgeEntity/ByKC-BaseQAEngine-v2.md",
            )
            replacement_file_id = int(replacement_file["kid"])
            await metadata_repository.upsert_value(
                cursor,
                fs_entry_id=replacement_file_id,
                knowledge_base_id=kb_id,
                property_name="documentKind",
                value_type="string",
                value="knowledgeEntity",
            )
            await connection.commit()
            await connection.close()
            await service.attach_file(
                knowledge_base_id=kb_id,
                entity_id=canonical.entity_id,
                fs_entry_id=replacement_file_id,
            )
            entity_response = await client.post(
                "/api/v1/knowledgeEntities/delete",
                json={"knCode": kb_code, "entityId": canonical.entity_id},
            )
            assert entity_response.json()["resultObject"] == {
                "deletedEntityCount": 1,
                "deletedAliasCount": 0,
                "deletedFileCount": 1,
            }

        connection = await connection_factory()
        cursor = connection.cursor()
        assert (
            await asset_repository.get_by_id(
                cursor, knowledge_base_id=kb_id, entity_id=canonical.entity_id
            )
            is None
        )
        assert (
            await _vector_count(
                cursor, bootstrap.entity_embedding_table_name, canonical.entity_id
            )
            == 0
        )
        deleted_file = await fs_repository.get_entry_by_id(
            cursor, entry_id=replacement_file_id
        )
        assert deleted_file is None
        await connection.rollback()
        await connection.close()

        connection = await connection_factory()
        cursor = connection.cursor()
        second_kb = await kb_repository.create_knowledge_base(
            cursor,
            kb_name=f"Entity asset KB delete {uuid4().hex[:8]}",
            kb_description=None,
        )
        await connection.commit()
        await connection.close()
        second_kb_id = int(second_kb["kid"])
        await service.resolve_candidate(
            knowledge_base_id=second_kb_id,
            entity_name="PostgreSQL",
            aliases=("PG",),
            subject_entity_id=None,
            subject_name=None,
            entity_type="database",
            description="PostgreSQL is a relational database.",
            evidence="PG is used as the database.",
        )
        kb_service = KnowledgeBaseService(
            connection_factory=connection_factory,
            knowledge_base_repository=kb_repository,
            knowledge_fs_entry_repository=fs_repository,
            knowledge_entity_asset_repository=asset_repository,
        )
        await kb_service.delete_knowledge_base(
            DeleteKnowledgeBaseRequest(kb_code=str(second_kb_id))
        )
        connection = await connection_factory()
        cursor = connection.cursor()
        await cursor.execute(
            "SELECT COUNT(*) AS total FROM knowledge_entity WHERE knowledge_base_id = %(kb_id)s",
            {"kb_id": second_kb_id},
        )
        assert int((await cursor.fetchone())["total"]) == 0
        await connection.rollback()
        await connection.close()

    finally:
        cleanup = await connection_factory()
        try:
            await cleanup.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name))
            )
            await cleanup.commit()
        finally:
            await cleanup.close()


async def _alias_id(cursor, entity_id: int) -> int:
    await cursor.execute(
        """
        SELECT kid
        FROM knowledge_entity
        WHERE canonical_entity_id = %(entity_id)s
          AND name_role = 'alias'
        """,
        {"entity_id": entity_id},
    )
    return int((await cursor.fetchone())["kid"])


async def _vector_count(cursor, table_name: str, entity_id: int) -> int:
    await cursor.execute(
        f"SELECT COUNT(*) AS total FROM {table_name} WHERE entity_id = %(entity_id)s",
        {"entity_id": entity_id},
    )
    return int((await cursor.fetchone())["total"])
