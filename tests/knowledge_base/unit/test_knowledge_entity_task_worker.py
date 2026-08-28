from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from by_qa.knowledge_base.services import knowledge_entity_task_worker as worker_module
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DiscoveredEntity,
    DiscoveredTopic,
    DiscoveryDocumentContext,
    DiscoveryResult,
    SourceReference,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EnrichmentResult,
    KnowledgeEntityEnricher,
    KnowledgeEntityIdentity,
    SemanticRelation,
    format_source_reference,
    organize_evidence,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    KnowledgeEntityOutputError,
    RelationCode,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskContext,
    KnowledgeEntityTaskWorker,
)
from by_qa.knowledge_base.services.markdown_front_matter import (
    parse_front_matter,
    split_front_matter,
)
from by_qa.knowledge_common.markdown_reference import (
    detect_reference_spans,
    detect_reference_token_spans,
)


def discovered_entity(
    name: str,
    evidence_summary: str,
    *,
    aliases: tuple[str, ...] = (),
    entity_ref: str = "e1",
) -> DiscoveredEntity:
    return DiscoveredEntity(
        entity_ref=entity_ref,
        name=name,
        aliases=aliases,
        evidence_summary=evidence_summary,
        description=f"{name} 的稳定身份描述。",
    )


class FakeCursor:
    def __init__(self, sql_calls: list[tuple[str, dict]]) -> None:
        self.sql_calls = sql_calls

    async def execute(self, sql: str, params: dict) -> None:
        self.sql_calls.append((sql, params))


class FakeConnection:
    def __init__(self, sql_calls: list[tuple[str, dict]]) -> None:
        self._cursor = FakeCursor(sql_calls)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


class FakeConnectionFactory:
    def __init__(self) -> None:
        self.sql_calls: list[tuple[str, dict]] = []
        self.connections: list[FakeConnection] = []

    async def __call__(self) -> FakeConnection:
        connection = FakeConnection(self.sql_calls)
        self.connections.append(connection)
        return connection


def file_row(
    file_id: int,
    path: str,
    *,
    kb_id: int = 1,
    content_key: str | None = None,
    markdown_key: str | None = None,
    document_kind: str | None = None,
    entity_name: str | None = None,
    aliases: list[str] | None = None,
    subject_file_id: int | None = None,
    entity_type: str | None = None,
    entity_enriched: bool | None = None,
    checksum: str = "checksum-1",
    updated_at: datetime | None = None,
) -> dict:
    return {
        "kid": file_id,
        "knowledge_base_id": kb_id,
        "name": path.rsplit("/", 1)[-1],
        "file_path": path,
        "checksum": checksum,
        "file_bucket_name": "original" if content_key else None,
        "file_object_key": content_key,
        "markdown_bucket_name": "markdown" if markdown_key else None,
        "markdown_object_key": markdown_key,
        "mime_type": "text/markdown",
        "document_kind": document_kind,
        "processing_capabilities": [],
        "entity_name": entity_name,
        "aliases": aliases or [],
        "subject_file_id": subject_file_id,
        "entity_type": entity_type,
        "entity_enriched": entity_enriched,
        "updated_at": updated_at or datetime(2026, 8, 27, tzinfo=UTC),
    }


class FakeEntityRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = {int(row["kid"]): row for row in rows}
        self.list_surface_calls = 0
        self.previous_enrich_at: datetime | None = None

    async def get_file_with_metadata(
        self, cursor, *, knowledge_base_id: int, file_path: str
    ) -> dict | None:
        del cursor
        return next(
            (
                dict(row)
                for row in self.rows.values()
                if int(row["knowledge_base_id"]) == knowledge_base_id
                and row["file_path"] == file_path
            ),
            None,
        )

    async def list_entity_surfaces(
        self, cursor, *, knowledge_base_id: int | None = None
    ) -> list[dict]:
        del cursor
        self.list_surface_calls += 1
        return [
            {
                "kid": row["kid"],
                "knowledge_base_id": row["knowledge_base_id"],
                "name": row["name"],
                "file_path": row["file_path"],
                "entity_name": row["entity_name"],
                "aliases": list(row["aliases"]),
                "subject_file_id": row["subject_file_id"],
                "entity_enriched": row.get("entity_enriched"),
            }
            for row in self.rows.values()
            if row.get("document_kind") == "knowledgeEntity"
            and (
                knowledge_base_id is None
                or int(row["knowledge_base_id"]) == knowledge_base_id
            )
        ]

    async def get_files_by_ids(
        self, cursor, *, knowledge_base_id: int, fs_entry_ids: list[int]
    ) -> list[dict]:
        del cursor
        return [
            dict(self.rows[file_id])
            for file_id in fs_entry_ids
            if file_id in self.rows
            and int(self.rows[file_id]["knowledge_base_id"]) == knowledge_base_id
        ]

    async def get_latest_successful_enrich_finished_at(self, cursor, **kwargs):
        del cursor, kwargs
        return self.previous_enrich_at


class FakeStorage:
    def __init__(self, objects: dict[tuple[str, str], bytes]) -> None:
        self.objects = objects

    async def read(self, location) -> bytes:
        return self.objects[(location.namespace, location.key)]


class FakeIngestion:
    def __init__(self, repository: FakeEntityRepository, storage: FakeStorage) -> None:
        self.repository = repository
        self.storage = storage
        self.uploads = []
        self.indexed_paths: list[str] = []
        self.next_id = max(repository.rows) + 1

    async def upload_file(self, request) -> dict:
        self.uploads.append(request)
        metadata = parse_front_matter(request.file_content)
        _, body = split_front_matter(request.file_content)
        file_id = self.next_id
        self.next_id += 1
        object_key = f"entity-{file_id}"
        self.storage.objects[("original", object_key)] = body
        self.repository.rows[file_id] = file_row(
            file_id,
            request.file_path,
            content_key=object_key,
            document_kind=metadata["documentKind"],
            entity_name=metadata["entityName"],
            aliases=metadata.get("aliases", []),
            subject_file_id=metadata.get("subjectFileId"),
            entity_type=metadata.get("entityType"),
            entity_enriched=metadata.get("entityEnriched"),
        )
        return {
            "fs_entry_id": file_id,
            "knowledge_base_id": 1,
            "virtual_path": request.file_path,
        }

    async def file_to_markdown_index(
        self, request, *, document_chunking_service
    ) -> None:
        assert document_chunking_service == "chunker"
        self.indexed_paths.append(request.file_path)
        row = next(
            row
            for row in self.repository.rows.values()
            if row["file_path"] == request.file_path
            and int(row["knowledge_base_id"]) == int(request.kb_code)
        )
        markdown_key = f"markdown-{row['kid']}"
        row["markdown_bucket_name"] = "markdown"
        row["markdown_object_key"] = markdown_key
        original = self.storage.objects[("original", row["file_object_key"])]
        self.storage.objects[("markdown", markdown_key)] = original


class FakeDocumentUpdate:
    def __init__(self) -> None:
        self.requests = []
        self.generated_assertions = []
        self.producer_run_ids = []

    async def update_file(
        self,
        request,
        *,
        generated_outgoing_assertions=(),
        producer_run_id=None,
    ) -> None:
        self.requests.append(request)
        self.generated_assertions.append(tuple(generated_outgoing_assertions))
        self.producer_run_ids.append(producer_run_id)


class FakeReferenceRepository:
    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.deletes: list[dict] = []
        self.incoming_relations: list[dict] = []

    async def upsert_relation_assertion(self, cursor, **kwargs) -> dict:
        del cursor
        self.upserts.append(kwargs)
        return kwargs

    async def delete_outgoing_for_source_fs_entry_id(
        self, cursor, **kwargs
    ) -> list[dict]:
        del cursor
        self.deletes.append(kwargs)
        return []

    async def list_recent_assertions_by_target(self, cursor, **kwargs) -> list[dict]:
        del cursor, kwargs
        return list(self.incoming_relations)


class FakeDiscovery:
    def __init__(
        self,
        candidates: tuple[DiscoveredEntity, ...],
        topics: tuple[DiscoveredTopic, ...] = (),
    ) -> None:
        self.candidates = candidates
        self.topics = topics
        self.log_context = None

    async def discover(self, markdown, *, max_entities, max_topics, log_context=None):
        assert markdown
        assert max_entities == 12
        assert max_topics == 24
        self.log_context = log_context
        return DiscoveryResult(
            entities=self.candidates,
            topics=self.topics,
            warnings=(),
            attempts=1,
            context=DiscoveryDocumentContext(
                markdown,
                (SourceReference("s1", markdown, 0, len(markdown)),),
                False,
            ),
        )


class FakeSearch:
    def __init__(self, hits=()) -> None:
        self.hits = list(hits)
        self.requests = []
        self.resolve_requests: list[tuple[int, list[str]]] = []

    async def search(self, request):
        self.requests.append(request)
        return list(self.hits)

    async def resolve_markdown_texts(self, *, knowledge_base_id: int, texts: list[str]):
        self.resolve_requests.append((knowledge_base_id, list(texts)))
        return [
            text.replace("byqa-ref://61", "/docs/research.md").replace(
                "byqa-ref://62", "/docs/source.md"
            )
            for text in texts
        ]


class FakeCreatedAssetService:
    def __init__(self, topics: tuple[str, ...] = ()) -> None:
        self.attachments: list[dict] = []
        self.topic_upserts: list[dict] = []
        self.topics = topics
        self.topic_requests: list[dict] = []

    async def resolve_candidate(self, **kwargs):
        return SimpleNamespace(
            entity_id=100,
            canonical_name=kwargs["entity_name"],
            fs_entry_id=None,
            method=SimpleNamespace(value="CREATED_NEW"),
            alias_added=None,
            candidate_count=0,
            created=True,
            warnings=(),
        )

    async def attach_file(self, **kwargs):
        self.attachments.append(kwargs)

    async def upsert_topics(self, **kwargs):
        self.topic_upserts.append(kwargs)
        return []

    async def list_topics_for_entity_file(self, **kwargs):
        self.topic_requests.append(kwargs)
        return self.topics


class FakeEnricher:
    def __init__(self, relations=()) -> None:
        self.relations = tuple(relations)
        self.evidence = []
        self.targets = ()
        self.log_context = None
        self.existing_markdown = ""
        self.topics = ()
        self.incremental = False

    async def enrich(
        self,
        identity,
        evidence,
        *,
        existing_markdown,
        relation_targets,
        topics=(),
        incremental=False,
        log_context=None,
    ) -> EnrichmentResult:
        self.existing_markdown = existing_markdown
        self.evidence = list(evidence)
        self.targets = relation_targets
        self.topics = tuple(topics)
        self.incremental = incremental
        self.log_context = log_context
        bundle = organize_evidence(self.evidence, target_file_id=identity.file_id)
        if not bundle.fragments:
            raise KnowledgeEntityOutputError("enrichment requires authorized evidence")
        source = bundle.fragments[0]
        return EnrichmentResult(
            markdown=(
                f"# {identity.entity_name}\n\n## 核心事实\n\n"
                f"已富化内容。[{source.document_path.rsplit('/', 1)[-1]}]"
                f"({source.document_path})"
            ),
            relations=self.relations,
            warnings=("soft-template-warning",),
            discarded_relation_count=1,
            missing_sections=("证据、冲突与不确定性",),
            template_coverage=0.67,
            placeholder_count=0,
            evidence=bundle,
            attempts=1,
        )


def make_worker(
    *,
    rows: list[dict],
    objects: dict[tuple[str, str], bytes],
    discovery: FakeDiscovery | None = None,
    enricher: FakeEnricher | None = None,
    search_hits=(),
    asset_service=None,
):
    factory = FakeConnectionFactory()
    repository = FakeEntityRepository(rows)
    storage = FakeStorage(objects)
    ingestion = FakeIngestion(repository, storage)
    references = FakeReferenceRepository()
    updater = FakeDocumentUpdate()
    discovery = discovery or FakeDiscovery(())
    enricher = enricher or FakeEnricher()
    search = FakeSearch(search_hits)
    asset_service = asset_service or object()
    worker = KnowledgeEntityTaskWorker(
        connection_factory=factory,
        knowledge_entity_repository=repository,
        knowledge_file_reference_repository=references,
        storage_provider=storage,
        knowledge_item_ingestion_service=ingestion,
        document_update_service=updater,
        document_chunking_service="chunker",
        knowledge_item_search_service=search,
        knowledge_entity_discovery=discovery,
        knowledge_entity_enricher=enricher,
        knowledge_entity_asset_service=asset_service,
    )
    return SimpleNamespace(
        worker=worker,
        factory=factory,
        repository=repository,
        storage=storage,
        ingestion=ingestion,
        references=references,
        updater=updater,
        discovery=discovery,
        enricher=enricher,
        search=search,
        asset_service=asset_service,
    )


@pytest.mark.asyncio
async def test_discovery_never_loads_or_builds_the_full_surface_vocabulary():
    source = file_row(10, "/docs/source.md", content_key="source")
    entity = file_row(
        20,
        "/KnowledgeEntity/ByKC-BaseQAEngine.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="ByKC-BaseQAEngine",
    )
    candidate = discovered_entity(
        "ByKC-基础问答引擎",
        "ByKC 的基础问答引擎负责回答。",
    )

    class AssetService:
        async def resolve_candidate(self, **kwargs):
            assert kwargs["entity_name"] == candidate.name
            return SimpleNamespace(
                entity_id=100,
                canonical_name="ByKC-BaseQAEngine",
                fs_entry_id=20,
                method=SimpleNamespace(value="EXACT_ALIAS"),
                alias_added=None,
                candidate_count=1,
                created=False,
                warnings=(),
            )

        async def attach_file(self, **kwargs):
            raise AssertionError(f"existing file must not be reattached: {kwargs}")

        async def upsert_topics(self, **_kwargs):
            return []

    deps = make_worker(
        rows=[source, entity],
        objects={
            ("original", "source"): b"ByKC uses a base QA engine.",
            ("original", "entity"): b"# ByKC-BaseQAEngine",
            ("markdown", "entity-md"): b"# ByKC-BaseQAEngine",
        },
        discovery=FakeDiscovery((candidate,)),
        asset_service=AssetService(),
    )

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=700,
            task_type="ENTITY_DISCOVERY",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=10,
            file_path="/docs/source.md",
        )
    )

    assert deps.repository.list_surface_calls == 0
    assert result.index_version is None
    assert result.result_payload["actions"][0]["canonicalEntityId"] == 100
    assert result.result_payload["actions"][0]["resolutionMethod"] == "EXACT_ALIAS"


@pytest.mark.asyncio
async def test_discovery_persists_topics_after_entity_owner_resolution_without_files():
    source = file_row(10, "/docs/source.md", content_key="source")
    candidate = discovered_entity(
        "OpenClaw",
        "OpenClaw 是本文的核心研究对象。",
    )
    asset_service = FakeCreatedAssetService()
    deps = make_worker(
        rows=[source],
        objects={
            ("original", "source"): (
                "OpenClaw 是本文的核心研究对象，上下文管理是其特化方向。"
            ).encode(),
        },
        discovery=FakeDiscovery(
            (candidate,),
            (
                DiscoveredTopic(
                    "e1",
                    "上下文管理",
                    "该方向附属于 OpenClaw 且具有持续检索价值。",
                ),
            ),
        ),
        asset_service=asset_service,
    )

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=503,
            task_type="ENTITY_DISCOVERY",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=10,
            file_path="/docs/source.md",
            input_checksum="checksum-1",
        )
    )

    assert len(deps.ingestion.uploads) == 1
    assert deps.ingestion.uploads[0].file_path == "/KnowledgeEntity/OpenClaw.md"
    upsert = asset_service.topic_upserts[0]
    assert upsert["topics"] == [{"owner_entity_id": 100, "name": "上下文管理"}]
    assert result.result_payload["topicCount"] == 1
    assert result.result_payload["protocolVersion"] == "entity-topic/2.2"


@pytest.mark.parametrize(
    ("entity_name", "expected_path"),
    [
        ("知识实体", "/KnowledgeEntity/知识实体.md"),
        ("OpenAI Platform", "/KnowledgeEntity/OpenAI-Platform.md"),
        ("Alpha/Beta", "/KnowledgeEntity/Alpha-Beta.md"),
    ],
)
def test_entity_path_uses_readable_name_without_identity_signature(
    entity_name: str, expected_path: str
) -> None:
    assert KnowledgeEntityTaskWorker._entity_path(entity_name) == expected_path


def test_discovery_source_path_is_rendered_as_a_markdown_reference() -> None:
    rendered = format_source_reference("/docs/source file.md")

    assert rendered == "[source file.md](/docs/source%20file.md)"
    assert detect_reference_spans(rendered)[0][3] == "/docs/source%20file.md"
    assert detect_reference_token_spans(rendered) == []


@pytest.mark.asyncio
async def test_discovery_anchors_readable_path_when_entity_name_metadata_is_missing():
    source = file_row(10, "/docs/source.md", content_key="source")
    occupied = file_row(
        11,
        "/KnowledgeEntity/Alpha-Beta.md",
        content_key="occupied",
        markdown_key="occupied-md",
        document_kind="knowledgeEntity",
        entity_name=None,
    )
    asset_service = FakeCreatedAssetService()
    deps = make_worker(
        rows=[source, occupied],
        objects={
            ("original", "source"): b"Alpha Beta is mentioned.",
            ("original", "occupied"): b"# Alpha/Beta",
            ("markdown", "occupied-md"): b"# Alpha/Beta",
        },
        discovery=FakeDiscovery(
            (discovered_entity("Alpha/Beta", "Alpha Beta is a stable component."),)
        ),
        asset_service=asset_service,
    )

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=502,
            task_type="ENTITY_DISCOVERY",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=10,
            file_path="/docs/source.md",
        )
    )

    assert deps.ingestion.uploads == []
    assert result.target_file_ids == (11,)
    assert result.result_payload["actions"][0] == {
        "entityRef": "e1",
        "action": "CREATED",
        "inputEntityName": "Alpha/Beta",
        "entityName": "Alpha/Beta",
        "canonicalEntityId": 100,
        "entityFileId": 11,
        "filePath": "/KnowledgeEntity/Alpha-Beta.md",
        "resolutionMethod": "CREATED_NEW",
        "aliasAdded": None,
        "candidateCount": 0,
    }
    assert asset_service.attachments == [
        {"knowledge_base_id": 1, "entity_id": 100, "fs_entry_id": 11}
    ]
    assert len(deps.references.upserts) == 1
    assert deps.references.upserts[0]["source_fs_entry_id"] == 10
    assert deps.references.upserts[0]["target_fs_entry_id"] == 11


@pytest.mark.asyncio
async def test_discovery_rejects_conflicting_metadata_at_readable_path():
    source = file_row(10, "/docs/source.md", content_key="source")
    conflicting = file_row(
        11,
        "/KnowledgeEntity/Alpha-Beta.md",
        content_key="conflicting",
        document_kind="knowledgeEntity",
        entity_name="Different Entity",
    )
    deps = make_worker(
        rows=[source, conflicting],
        objects={
            ("original", "source"): b"Alpha Beta is mentioned.",
            ("original", "conflicting"): b"# Different Entity",
        },
        discovery=FakeDiscovery(
            (discovered_entity("Alpha/Beta", "Alpha Beta is a stable component."),)
        ),
        asset_service=FakeCreatedAssetService(),
    )

    with pytest.raises(
        ValueError,
        match="readable path has conflicting entityName metadata",
    ):
        await deps.worker.run_task(
            KnowledgeEntityTaskContext(
                task_id=503,
                task_type="ENTITY_DISCOVERY",
                kb_code="1",
                knowledge_base_id=1,
                source_file_id=10,
                file_path="/docs/source.md",
            )
        )

    assert deps.ingestion.uploads == []
    assert deps.references.upserts == []


@pytest.mark.asyncio
async def test_enrich_uses_bounded_evidence_cas_and_replaces_only_enrich_relations():
    entity = file_row(
        30,
        "/KnowledgeEntity/beta.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Beta",
        aliases=["B"],
        checksum="before-enrich",
    )
    target = file_row(
        20,
        "/KnowledgeEntity/alpha.md",
        content_key="alpha",
        markdown_key="alpha-md",
        document_kind="knowledgeEntity",
        entity_name="Alpha",
        entity_enriched=True,
    )
    other_kb_target = file_row(
        99,
        "/KnowledgeEntity/remote.md",
        kb_id=2,
        content_key="remote",
        markdown_key="remote-md",
        document_kind="knowledgeEntity",
        entity_name="Remote",
    )
    direct = file_row(10, "/docs/direct.md", content_key="direct")
    explicit = file_row(11, "/docs/reference.md", content_key="reference")
    recalled = file_row(12, "/docs/recalled.md", content_key="recalled")
    unenriched = file_row(
        15,
        "/KnowledgeEntity/draft.md",
        content_key="draft",
        markdown_key="draft-md",
        document_kind="knowledgeEntity",
        entity_name="Beta Draft",
        entity_enriched=False,
    )
    recent_third = file_row(13, "/docs/recent-third.md", content_key="recent-third")
    older_fourth = file_row(14, "/docs/older-fourth.md", content_key="older-fourth")
    valid = SemanticRelation(
        source_file_id=30,
        relation_code=RelationCode.PART_OF,
        target_file_id=20,
        target_entity_name="Alpha",
        confidence=0.9,
    )
    cross_kb = SemanticRelation(
        source_file_id=30,
        relation_code=RelationCode.IS_A,
        target_file_id=99,
        target_entity_name="Remote",
        confidence=0.8,
    )
    invalid_code = SimpleNamespace(
        source_file_id=30,
        relation_code=SimpleNamespace(value="RELATED_TO"),
        target_file_id=20,
        target_entity_name="Alpha",
        confidence=0.5,
    )
    deps = make_worker(
        rows=[
            entity,
            target,
            other_kb_target,
            direct,
            explicit,
            recalled,
            recent_third,
            older_fourth,
            unenriched,
        ],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): (
                b"# Beta\n\nOld content. [source](byqa-ref://62)"
            ),
            ("original", "alpha"): b"# Alpha",
            ("markdown", "alpha-md"): b"# Alpha",
            ("original", "remote"): b"# Remote",
            ("markdown", "remote-md"): b"# Remote",
            ("original", "direct"): (
                b"Direct Beta evidence. [research](byqa-ref://61)"
            ),
            ("original", "reference"): b"Explicit [[Beta]] evidence.",
            ("original", "recalled"): b"Recall source.",
            ("original", "recent-third"): b"Recent Beta evidence.",
            ("original", "older-fourth"): b"Older Beta evidence.",
            ("original", "draft"): b"# Beta Draft",
            ("markdown", "draft-md"): b"# Beta Draft\n\nUnenriched Beta evidence.",
        },
        enricher=FakeEnricher((valid, cross_kb, invalid_code)),
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/KnowledgeEntity/beta.md",
                chunk_text="Current Beta content.",
                score=0.99,
            ),
            SimpleNamespace(
                kb_code="1",
                file_path="/KnowledgeEntity/draft.md",
                chunk_text="Unenriched Beta evidence.",
                score=0.9,
            ),
            SimpleNamespace(
                kb_code="1",
                file_path="/docs/recalled.md",
                chunk_text="Semantic Beta evidence.",
                score=0.72,
            ),
        ],
    )
    deps.references.incoming_relations = [
        {"source_fs_entry_id": 10, "relation_code": "DEPENDS_ON"},
        {"source_fs_entry_id": 11, "relation_code": "IS_A"},
        {"source_fs_entry_id": 13, "relation_code": "PART_OF"},
        {"source_fs_entry_id": 14, "relation_code": "MENTIONS"},
    ]

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=601,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/beta.md",
            input_checksum="before-enrich",
            request_params={"topK": 5},
            batch_id="batch-601",
        )
    )

    assert {item.document_file_id for item in deps.enricher.evidence} == {
        10,
        11,
        12,
        13,
    }
    assert 14 not in {item.document_file_id for item in deps.enricher.evidence}
    assert {target.file_id for target in deps.enricher.targets} == {20}
    assert deps.search.requests[0].search_mode == "mixedRecall"
    assert deps.search.requests[0].top_k == 5
    assert deps.search.requests[0].where == {
        "and": [
            {
                "ne": {
                    "fieldName": "filePath",
                    "value": "/KnowledgeEntity/beta.md",
                }
            },
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
                            "fieldName": "entityEnriched",
                            "value": True,
                        }
                    },
                ]
            },
        ]
    }
    assert "Old content." in deps.search.requests[0].query
    assert "byqa-ref://" not in deps.search.requests[0].query
    assert "/docs/source.md" in deps.enricher.existing_markdown
    direct_evidence = next(
        item for item in deps.enricher.evidence if item.document_file_id == 10
    )
    assert "[research](/docs/research.md)" in direct_evidence.content
    assert "byqa-ref://" not in direct_evidence.content

    update = deps.updater.requests[0]
    assert update.refer_signature == "before-enrich"
    assert update.process_front_matter is True
    metadata = parse_front_matter(update.file_content)
    assert metadata == {
        "documentKind": "knowledgeEntity",
        "processingCapabilities": ["entityEnrich"],
        "entityName": "Beta",
        "aliases": ["B"],
        "entityEnriched": True,
    }
    assert b"# Beta" in update.file_content
    assert b"[direct.md](/docs/direct.md)" in update.file_content
    assert deps.ingestion.indexed_paths == ["/KnowledgeEntity/beta.md"]
    generated = deps.updater.generated_assertions[0]
    assert len(generated) == 1
    assert generated[0].target_fs_entry_id == 20
    assert generated[0].relation_code == "PART_OF"
    assert generated[0].source_task_id == 601
    assert generated[0].discovered_by == "ENTITY_ENRICH"
    assert generated[0].producer_run_id == "entity-enrich:601"
    assert len(generated[0].evidence_fingerprint) == 64
    assert deps.updater.producer_run_ids == ["entity-enrich:601"]
    assert deps.enricher.log_context == {
        "batch_id": "batch-601",
        "task_id": 601,
        "kb_code": "1",
        "source_file_id": 30,
        "file_path": "/KnowledgeEntity/beta.md",
        "task_type": "DOCUMENT_ENRICH",
    }
    assert deps.references.upserts == []
    assert deps.references.deletes == []
    assert result.target_file_ids == (20,)
    assert result.index_version is None
    assert result.result_payload["templateCoverage"] == 0.67
    assert result.result_payload["warnings"] == ["soft-template-warning"]


@pytest.mark.asyncio
async def test_reenrich_uses_topics_and_only_evidence_newer_than_previous_run():
    watermark = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    entity = file_row(
        30,
        "/KnowledgeEntity/beta.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Beta",
        entity_enriched=True,
        checksum="before-enrich",
    )
    old_source = file_row(
        10,
        "/docs/old.md",
        content_key="old",
        updated_at=watermark - timedelta(minutes=1),
    )
    new_source = file_row(
        11,
        "/docs/new.md",
        content_key="new",
        updated_at=watermark + timedelta(minutes=1),
    )
    topics = tuple(f"Topic {index}" for index in range(13))
    deps = make_worker(
        rows=[entity, old_source, new_source],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): b"# Beta\n\nOld baseline. [old](/docs/old.md)",
            ("original", "old"): b"Old Beta evidence.",
            ("original", "new"): b"New Beta evidence.",
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/docs/old.md",
                chunk_text="Old Beta evidence.",
                score=0.9,
            ),
            SimpleNamespace(
                kb_code="1",
                file_path="/docs/new.md",
                chunk_text="New Beta evidence.",
                score=0.95,
            ),
        ],
        asset_service=FakeCreatedAssetService(topics),
    )
    deps.repository.previous_enrich_at = watermark
    deps.references.incoming_relations = [
        {
            "source_fs_entry_id": 10,
            "relation_code": "MENTIONS",
            "created_at": watermark - timedelta(seconds=1),
        },
        {
            "source_fs_entry_id": 11,
            "relation_code": "MENTIONS",
            "created_at": watermark + timedelta(seconds=1),
        },
    ]

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=602,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/beta.md",
            input_checksum="before-enrich",
        )
    )

    assert deps.enricher.topics == topics
    assert deps.asset_service.topic_requests == [
        {
            "knowledge_base_id": 1,
            "fs_entry_id": 30,
            "updated_after": watermark,
        }
    ]
    assert len(deps.search.requests) == 13
    assert all(
        len(
            [
                line
                for line in request.query.splitlines()
                if line.startswith("Beta Topic ")
            ]
        )
        == 1
        for request in deps.search.requests
    )
    assert all(
        request.where["and"][-1]
        == {"gt": {"fieldName": "updatedAt", "value": "2026-08-27T10:00:00Z"}}
        for request in deps.search.requests
    )
    assert {item.document_file_id for item in deps.enricher.evidence} == {11}
    assert deps.enricher.incremental is True
    assert result.result_payload["incremental"] is True
    assert result.result_payload["topicCount"] == 13


@pytest.mark.asyncio
async def test_topic_queries_apply_score_thresholds_independently():
    entity = file_row(
        30,
        "/KnowledgeEntity/beta.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Beta",
        checksum="before-enrich",
    )
    first_source = file_row(10, "/docs/first.md", content_key="first")
    second_source = file_row(11, "/docs/second.md", content_key="second")
    deps = make_worker(
        rows=[entity, first_source, second_source],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): b"# Beta\n\nBaseline.",
            ("original", "first"): b"First evidence.",
            ("original", "second"): b"Second evidence.",
        },
        asset_service=FakeCreatedAssetService(("Topic 0", "Topic 1")),
    )

    class PerBatchSearch(FakeSearch):
        def __init__(self):
            super().__init__()
            self.batches = [
                [
                    SimpleNamespace(
                        kb_code="1",
                        file_path="/docs/first.md",
                        chunk_text="First Beta evidence.",
                        score=0.99,
                    )
                ],
                [
                    SimpleNamespace(
                        kb_code="1",
                        file_path="/docs/second.md",
                        chunk_text="Second Beta evidence.",
                        score=0.60,
                    )
                ],
            ]

        async def search(self, request):
            self.requests.append(request)
            return self.batches[len(self.requests) - 1]

    deps.search = PerBatchSearch()
    deps.worker._search = deps.search

    await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=604,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/beta.md",
            input_checksum="before-enrich",
        )
    )

    assert {item.document_file_id for item in deps.enricher.evidence} == {10, 11}


@pytest.mark.asyncio
async def test_topic_semantic_sources_are_bounded_by_score_per_topic():
    entity = file_row(
        30,
        "/KnowledgeEntity/target.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Target",
    )
    sources = [
        file_row(file_id, f"/docs/{file_id}.md", content_key=str(file_id))
        for file_id in (10, 11, 12)
    ]
    deps = make_worker(
        rows=[entity, *sources],
        objects={
            ("original", "entity"): b"# Target",
            ("markdown", "entity-md"): b"# Target",
            **{
                ("original", str(file_id)): f"source {file_id}".encode()
                for file_id in (10, 11, 12)
            },
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path=f"/docs/{file_id}.md",
                chunk_text=f"evidence {file_id}",
                score=score,
            )
            for file_id, score in ((10, 0.91), (11, 0.99), (12, 0.95))
        ],
        asset_service=FakeCreatedAssetService(("Topic",)),
    )

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=607,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/target.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "Target"),
        existing_markdown="# Target",
        topics=("Topic",),
    )

    assert {item.document_file_id for item in evidence} == {11, 12}


@pytest.mark.asyncio
async def test_relation_source_is_exempt_from_topic_semantic_source_cap():
    entity = file_row(
        30,
        "/KnowledgeEntity/target.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Target",
    )
    sources = [
        file_row(file_id, f"/docs/{file_id}.md", content_key=str(file_id))
        for file_id in (10, 11, 12)
    ]
    deps = make_worker(
        rows=[entity, *sources],
        objects={
            ("original", "entity"): b"# Target",
            ("markdown", "entity-md"): b"# Target",
            **{
                ("original", str(file_id)): f"source {file_id}".encode()
                for file_id in (10, 11, 12)
            },
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path=f"/docs/{file_id}.md",
                chunk_text=f"semantic evidence {file_id}",
                score=score,
            )
            for file_id, score in ((10, 0.80), (11, 0.99), (12, 0.95))
        ],
        asset_service=FakeCreatedAssetService(("Topic",)),
    )
    deps.references.incoming_relations = [
        {
            "source_fs_entry_id": 10,
            "relation_code": "MENTIONS",
            "created_at": datetime.now(UTC),
        }
    ]

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=608,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/target.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "Target"),
        existing_markdown="# Target",
        topics=("Topic",),
    )

    assert {item.document_file_id for item in evidence} == {10, 11}


@pytest.mark.asyncio
async def test_same_semantic_chunk_accumulates_topics_without_duplicate_evidence():
    entity = file_row(
        30,
        "/KnowledgeEntity/OpenClaw.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="OpenClaw",
    )
    source = file_row(10, "/docs/openclaw-analysis.md", content_key="source")
    hit = SimpleNamespace(
        kb_code="1",
        file_path="/docs/openclaw-analysis.md",
        chunk_text="OpenClaw 通过记忆召回控制上下文。",
        score=0.95,
    )
    deps = make_worker(
        rows=[entity, source],
        objects={
            ("original", "entity"): b"# OpenClaw",
            ("markdown", "entity-md"): b"# OpenClaw",
            ("original", "source"): b"OpenClaw source",
        },
        search_hits=[hit],
        asset_service=FakeCreatedAssetService(("记忆搜索", "上下文工程")),
    )

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=606,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/OpenClaw.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "OpenClaw"),
        existing_markdown="# OpenClaw",
        topics=("记忆搜索", "上下文工程"),
    )

    assert len(evidence) == 1
    assert evidence[0].matched_topics == ("记忆搜索", "上下文工程")


@pytest.mark.asyncio
async def test_late_topic_keeps_its_own_semantic_fragment_quota():
    entity = file_row(
        30,
        "/KnowledgeEntity/target.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Target",
    )
    source = file_row(10, "/docs/source.md", content_key="source")
    topics = tuple(f"Topic {index}" for index in range(10))
    deps = make_worker(
        rows=[entity, source],
        objects={
            ("original", "entity"): b"# Target",
            ("markdown", "entity-md"): b"# Target",
            ("original", "source"): b"source",
        },
        asset_service=FakeCreatedAssetService(topics),
    )

    class PerTopicSearch(FakeSearch):
        async def search(self, request):
            self.requests.append(request)
            topic_index = len(self.requests) - 1
            return [
                SimpleNamespace(
                    kb_code="1",
                    file_path="/docs/source.md",
                    chunk_text=f"topic {topic_index} fragment {fragment_index}",
                    score=1.0 - fragment_index / 100,
                )
                for fragment_index in range(4)
            ]

    deps.search = PerTopicSearch()
    deps.worker._search = deps.search

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=609,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/target.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "Target"),
        existing_markdown="# Target",
        topics=topics,
    )

    assert len(evidence) == 30
    assert any(item.matched_topics == (topics[-1],) for item in evidence)


def test_topic_search_query_scopes_each_topic_with_entity_name():
    query = KnowledgeEntityTaskWorker._build_enrichment_search_query(
        "# OpenClaw\n\nExisting fact.",
        names=("OpenClaw", "Moltbot"),
        topics=("上下文工程", "记忆架构"),
    )

    lines = query.splitlines()
    assert "OpenClaw 上下文工程" in lines
    assert "Moltbot 上下文工程" in lines
    assert "OpenClaw 记忆架构" in lines
    assert "上下文工程" not in lines


@pytest.mark.asyncio
async def test_topic_hit_is_not_removed_by_lexical_entity_scope_rules():
    entity = file_row(
        30,
        "/KnowledgeEntity/OpenClaw.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="OpenClaw",
    )
    hermes = file_row(10, "/docs/hermes.md", content_key="hermes")
    deps = make_worker(
        rows=[entity, hermes],
        objects={
            ("original", "entity"): b"# OpenClaw",
            ("markdown", "entity-md"): b"# OpenClaw",
            ("original", "hermes"): b"Hermes source",
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/docs/hermes.md",
                chunk_text="上下文工程包括压缩、修剪与记忆召回。",
                score=1.0,
            )
        ],
        asset_service=FakeCreatedAssetService(("上下文工程",)),
    )

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=605,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/OpenClaw.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "OpenClaw"),
        existing_markdown="# OpenClaw",
        topics=("上下文工程",),
    )

    assert len(evidence) == 1
    assert evidence[0].content == "上下文工程包括压缩、修剪与记忆召回。"
    assert evidence[0].matched_topics == ("上下文工程",)


@pytest.mark.asyncio
async def test_knowledge_entity_evidence_carries_name_and_aliases_for_linking():
    entity = file_row(
        30,
        "/KnowledgeEntity/target.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Target",
    )
    recalled = file_row(
        10,
        "/KnowledgeEntity/GBrain.md",
        content_key="gbrain",
        document_kind="knowledgeEntity",
        entity_name="Garry's Opinionated Agent Brain",
        aliases=["GBrain", "GB"],
        entity_enriched=True,
    )
    deps = make_worker(
        rows=[entity, recalled],
        objects={
            ("original", "entity"): b"# Target",
            ("markdown", "entity-md"): b"# Target",
            ("original", "gbrain"): b"GBrain evidence.",
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/KnowledgeEntity/GBrain.md",
                chunk_text="GBrain uses persistent memory.",
                score=1.0,
            )
        ],
        asset_service=FakeCreatedAssetService(("Memory",)),
    )

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=610,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/target.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "Target"),
        existing_markdown="# Target",
        topics=("Memory",),
    )

    assert len(evidence) == 1
    assert evidence[0].source_entity_name == "Garry's Opinionated Agent Brain"
    assert evidence[0].source_entity_aliases == ("GBrain", "GB")


@pytest.mark.asyncio
async def test_collected_knowledge_entities_add_longest_missing_links_once():
    """Collected entity metadata drives safe linking after an unlinked LLM draft."""

    target = file_row(
        30,
        "/KnowledgeEntity/target.md",
        content_key="target",
        markdown_key="target-md",
        document_kind="knowledgeEntity",
        entity_name="Target",
    )
    ontology = file_row(
        10,
        "/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md",
        content_key="ontology",
        document_kind="knowledgeEntity",
        entity_name="Microsoft Fabric IQ Ontology",
        aliases=["Fabric IQ"],
        entity_enriched=True,
    )
    fabric = file_row(
        11,
        "/KnowledgeEntity/Microsoft-Fabric.md",
        content_key="fabric",
        document_kind="knowledgeEntity",
        entity_name="Microsoft Fabric",
        aliases=["Fabric"],
        entity_enriched=True,
    )
    deps = make_worker(
        rows=[target, ontology, fabric],
        objects={
            ("original", "target"): b"# Target",
            ("markdown", "target-md"): b"# Target",
            ("original", "ontology"): b"Ontology evidence.",
            ("original", "fabric"): b"Fabric evidence.",
        },
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md",
                chunk_text="The ontology provides a semantic model.",
                score=1.0,
            ),
            SimpleNamespace(
                kb_code="1",
                file_path="/KnowledgeEntity/Microsoft-Fabric.md",
                chunk_text="Microsoft Fabric provides the data platform.",
                score=0.95,
            ),
        ],
        asset_service=FakeCreatedAssetService(("Semantic model",)),
    )

    evidence = await deps.worker._collect_evidence(
        KnowledgeEntityTaskContext(
            task_id=611,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/target.md",
        ),
        identity=KnowledgeEntityIdentity(30, 1, "Target"),
        existing_markdown="# Target",
        topics=("Semantic model",),
    )

    class UnlinkedDraftLLM:
        async def complete(self, messages, *, json_mode=False):
            del messages, json_mode
            return json.dumps(
                {
                    "markdown": (
                        "# Target\n\n## Related systems\n\n"
                        "Microsoft Fabric IQ Ontology extends Microsoft Fabric "
                        "with a semantic model. Fabric IQ is mentioned again.\n\n"
                        "The configuration key is `Microsoft Fabric IQ Ontology`."
                    ),
                    "relations": [],
                    "warnings": [],
                }
            )

    result = await KnowledgeEntityEnricher(UnlinkedDraftLLM()).enrich(
        KnowledgeEntityIdentity(30, 1, "Target"),
        evidence,
        topics=("Semantic model",),
    )

    ontology_link = (
        "[Microsoft Fabric IQ Ontology]"
        "(/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md)"
    )
    fabric_link = "[Microsoft Fabric](/KnowledgeEntity/Microsoft-Fabric.md)"
    assert f"{ontology_link} extends {fabric_link}" in result.markdown
    assert (
        result.markdown.count("/KnowledgeEntity/Microsoft-Fabric-IQ-Ontology.md") == 1
    )
    assert result.markdown.count("/KnowledgeEntity/Microsoft-Fabric.md") == 1
    assert "Fabric IQ is mentioned again" in result.markdown
    assert "`Microsoft Fabric IQ Ontology`" in result.markdown
    assert result.quality_audit.untraceable_source_group_ids == ()


@pytest.mark.asyncio
async def test_reenrich_without_new_evidence_keeps_existing_document_unchanged():
    watermark = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)
    entity = file_row(
        30,
        "/KnowledgeEntity/beta.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Beta",
        entity_enriched=True,
        checksum="before-enrich",
    )
    deps = make_worker(
        rows=[entity],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): b"# Beta\n\nStable content. [old](/old.md)",
        },
        asset_service=FakeCreatedAssetService(("Stable topic",)),
    )
    deps.repository.previous_enrich_at = watermark

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=603,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/beta.md",
            input_checksum="before-enrich",
        )
    )

    assert deps.updater.requests == []
    assert deps.ingestion.indexed_paths == []
    assert result.result_payload["actions"][0]["action"] == "SKIPPED_NO_NEW_EVIDENCE"


@pytest.mark.asyncio
async def test_enrich_without_any_evidence_fails_before_document_update(
    monkeypatch: pytest.MonkeyPatch,
):
    failed_logs: list[str] = []

    def capture_failure(message: str, *args, **_kwargs) -> None:
        failed_logs.append(message % args)

    monkeypatch.setattr(worker_module.logger, "exception", capture_failure)
    entity = file_row(
        30,
        "/KnowledgeEntity/beta.md",
        content_key="entity",
        markdown_key="entity-md",
        document_kind="knowledgeEntity",
        entity_name="Beta",
    )
    deps = make_worker(
        rows=[entity],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): b"# Beta",
        },
    )

    with pytest.raises(
        KnowledgeEntityOutputError, match="requires authorized evidence"
    ):
        await deps.worker.run_task(
            KnowledgeEntityTaskContext(
                task_id=701,
                task_type="DOCUMENT_ENRICH",
                kb_code="1",
                knowledge_base_id=1,
                source_file_id=30,
                file_path="/KnowledgeEntity/beta.md",
                input_checksum="checksum-1",
                batch_id="batch-701",
            )
        )

    assert deps.updater.requests == []
    assert deps.references.upserts == []
    assert "batch_id=batch-701" in "\n".join(failed_logs)
    assert "task_id=701" in "\n".join(failed_logs)
