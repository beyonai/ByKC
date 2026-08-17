from __future__ import annotations

from types import SimpleNamespace

import pytest

from by_qa.knowledge_base.services import knowledge_entity_task_worker as worker_module
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    DiscoveryDocumentContext,
    EnrichmentResult,
    EntityCandidate,
    EntityDiscoveryResult,
    IdentityScope,
    KnowledgeEntityOutputError,
    RelationCode,
    SemanticRelation,
    organize_evidence,
)
from by_qa.knowledge_base.services.knowledge_entity_task_worker import (
    KnowledgeEntityTaskContext,
    KnowledgeEntityTaskWorker,
)
from by_qa.knowledge_base.services.markdown_front_matter import (
    parse_front_matter,
    split_front_matter,
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
    definition_version: str | None = None,
    enrich_version: str | None = None,
    entity_type: str | None = None,
    checksum: str = "checksum-1",
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
        "definition_version": definition_version,
        "enrich_version": enrich_version,
        "entity_type": entity_type,
    }


class FakeEntityRepository:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = {int(row["kid"]): row for row in rows}

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
        return [
            {
                "kid": row["kid"],
                "knowledge_base_id": row["knowledge_base_id"],
                "name": row["name"],
                "file_path": row["file_path"],
                "entity_name": row["entity_name"],
                "aliases": list(row["aliases"]),
                "subject_file_id": row["subject_file_id"],
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
            definition_version=metadata["definitionVersion"],
            entity_type=metadata.get("entityType"),
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
        self.incoming_mentions: list[dict] = []
        self.markdown_sources: list[dict] = []

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

    async def list_relations_by_target(self, cursor, **kwargs) -> list[dict]:
        del cursor, kwargs
        return list(self.incoming_mentions)

    async def list_sources_by_target(self, cursor, **kwargs) -> list[dict]:
        del cursor, kwargs
        return list(self.markdown_sources)


class FakeDiscovery:
    def __init__(self, candidates: tuple[EntityCandidate, ...]) -> None:
        self.candidates = candidates
        self.known_matches = ()
        self.log_context = None

    async def discover(
        self, markdown, *, known_matches, max_entities, log_context=None
    ):
        assert markdown
        assert max_entities == 12
        self.known_matches = known_matches
        self.log_context = log_context
        return EntityDiscoveryResult(
            candidates=self.candidates,
            warnings=(),
            attempts=1,
            context=DiscoveryDocumentContext(markdown, (), False),
        )


class FakeSearch:
    def __init__(self, hits=()) -> None:
        self.hits = list(hits)
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return list(self.hits)


class FakeEnricher:
    def __init__(self, relations=()) -> None:
        self.relations = tuple(relations)
        self.evidence = []
        self.targets = ()
        self.log_context = None

    async def enrich(
        self,
        identity,
        evidence,
        *,
        existing_markdown,
        relation_targets,
        log_context=None,
    ) -> EnrichmentResult:
        del existing_markdown
        self.evidence = list(evidence)
        self.targets = relation_targets
        self.log_context = log_context
        bundle = organize_evidence(self.evidence, target_file_id=identity.file_id)
        if not bundle.fragments:
            raise KnowledgeEntityOutputError("enrichment requires authorized evidence")
        return EnrichmentResult(
            markdown=f"# {identity.entity_name}\n\n## 核心事实\n\n已富化内容。",
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
    )


@pytest.mark.asyncio
async def test_discovery_anchors_only_unique_current_kb_and_creates_fixed_entities(
    monkeypatch: pytest.MonkeyPatch,
):
    log_messages: list[str] = []

    def capture_log(message: str, *args, **_kwargs) -> None:
        log_messages.append(message % args)

    monkeypatch.setattr(worker_module.logger, "info", capture_log)
    source = file_row(10, "/docs/source.md", content_key="source")
    owner = file_row(
        20,
        "/KnowledgeEntity/alpha.md",
        content_key="alpha",
        markdown_key="alpha-md",
        document_kind="knowledgeEntity",
        entity_name="Alpha",
        aliases=["A"],
        definition_version="v1",
    )
    cross_kb = file_row(
        99,
        "/KnowledgeEntity/cross.md",
        kb_id=2,
        content_key="cross",
        markdown_key="cross-md",
        document_kind="knowledgeEntity",
        entity_name="Cross",
        definition_version="v1",
    )
    candidates = (
        EntityCandidate(
            entity_name="A",
            local_name="A",
            identity_scope=IdentityScope.GLOBAL,
            evidence="The exact alias resolves to Alpha.",
        ),
        EntityCandidate(
            entity_name="Beta/Unsafe",
            local_name="Beta/Unsafe",
            identity_scope=IdentityScope.GLOBAL,
            evidence="Beta is a stable component.",
            aliases=("Beta",),
        ),
        EntityCandidate(
            entity_name="Alpha-Worker",
            local_name="Worker",
            identity_scope=IdentityScope.SUBJECT,
            subject_entity_name="Alpha",
            evidence="Worker belongs to Alpha.",
        ),
        EntityCandidate(
            entity_name="Missing-Child",
            local_name="Child",
            identity_scope=IdentityScope.SUBJECT,
            subject_entity_name="Missing",
            evidence="The unresolved child is mentioned.",
        ),
    )
    deps = make_worker(
        rows=[source, owner, cross_kb],
        objects={
            ("original", "source"): b"Alpha and Cross are mentioned.",
            ("original", "alpha"): b"# Alpha",
            ("markdown", "alpha-md"): b"# Alpha",
            ("original", "cross"): b"# Cross",
            ("markdown", "cross-md"): b"# Cross",
        },
        discovery=FakeDiscovery(candidates),
    )

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=501,
            task_type="ENTITY_DISCOVERY",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=10,
            file_path="/docs/source.md",
            definition_version="v2",
            batch_id="batch-501",
        )
    )

    anchored = {
        posting.entity_file_id
        for match in deps.discovery.known_matches
        for posting in match.anchorable_postings
    }
    assert anchored == {20}
    assert len(deps.ingestion.uploads) == 2
    assert any(
        action.get("entityFileId") == 20
        and action.get("entityName") == "Alpha"
        and action["action"] == "ANCHORED"
        for action in result.result_payload["actions"]
    )
    paths = [request.file_path for request in deps.ingestion.uploads]
    assert all(path.startswith("/KnowledgeEntity/") for path in paths)
    assert all(path.count("/") == 2 for path in paths)
    assert all("Unsafe/" not in path for path in paths)

    metadata = [
        parse_front_matter(request.file_content) for request in deps.ingestion.uploads
    ]
    assert metadata[0]["documentKind"] == "knowledgeEntity"
    assert metadata[0]["definitionVersion"] == "v2"
    assert metadata[1]["subjectFileId"] == 20
    assert "Worker" in metadata[1]["aliases"]
    mention_targets = {item["target_fs_entry_id"] for item in deps.references.upserts}
    assert mention_targets == set(result.target_file_ids)
    assert 20 in mention_targets
    assert 99 not in mention_targets
    assert all(item["relation_code"] == "MENTIONS" for item in deps.references.upserts)
    assert all(item["source_task_id"] == 501 for item in deps.references.upserts)
    assert deps.references.deletes == [
        {
            "knowledge_base_id": 1,
            "source_fs_entry_id": 10,
            "relation_code": "MENTIONS",
            "discovered_by": "ENTITY_DISCOVERY",
        }
    ]
    assert len(result.index_version) == 16
    assert result.index_version != "v2"
    assert {action["action"] for action in result.result_payload["actions"]} == {
        "ANCHORED",
        "CREATED",
        "DROPPED",
    }
    assert "[来源文档](</docs/source.md>)".encode() in (
        deps.ingestion.uploads[0].file_content
    )
    assert deps.discovery.log_context == {
        "batch_id": "batch-501",
        "task_id": 501,
        "kb_code": "1",
        "source_file_id": 10,
        "file_path": "/docs/source.md",
        "task_type": "ENTITY_DISCOVERY",
    }
    rendered_logs = "\n".join(log_messages)
    assert "knowledge_entity_task_worker started" in rendered_logs
    assert "knowledge_entity_discovery model completed" in rendered_logs
    assert "relation_replacement_count=3" in rendered_logs
    assert "batch_id=batch-501" in rendered_logs
    assert "Alpha and Cross are mentioned" not in rendered_logs


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
        definition_version="v2",
        checksum="before-enrich",
    )
    target = file_row(
        20,
        "/KnowledgeEntity/alpha.md",
        content_key="alpha",
        markdown_key="alpha-md",
        document_kind="knowledgeEntity",
        entity_name="Alpha",
        definition_version="v1",
    )
    other_kb_target = file_row(
        99,
        "/KnowledgeEntity/remote.md",
        kb_id=2,
        content_key="remote",
        markdown_key="remote-md",
        document_kind="knowledgeEntity",
        entity_name="Remote",
        definition_version="v1",
    )
    direct = file_row(10, "/docs/direct.md", content_key="direct")
    explicit = file_row(11, "/docs/reference.md", content_key="reference")
    recalled = file_row(12, "/docs/recalled.md", content_key="recalled")
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
        rows=[entity, target, other_kb_target, direct, explicit, recalled],
        objects={
            ("original", "entity"): b"# Beta",
            ("markdown", "entity-md"): b"# Beta\n\nOld content.",
            ("original", "alpha"): b"# Alpha",
            ("markdown", "alpha-md"): b"# Alpha",
            ("original", "remote"): b"# Remote",
            ("markdown", "remote-md"): b"# Remote",
            ("original", "direct"): b"Direct Beta evidence.",
            ("original", "reference"): b"Explicit [[Beta]] evidence.",
            ("original", "recalled"): b"Recall source.",
        },
        enricher=FakeEnricher((valid, cross_kb, invalid_code)),
        search_hits=[
            SimpleNamespace(
                kb_code="1",
                file_path="/docs/recalled.md",
                chunk_text="Semantic Beta evidence.",
                score=0.72,
            )
        ],
    )
    deps.references.incoming_mentions = [{"source_fs_entry_id": 10}]
    deps.references.markdown_sources = [{"source_fs_entry_id": 11}]

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=601,
            task_type="DOCUMENT_ENRICH",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=30,
            file_path="/KnowledgeEntity/beta.md",
            definition_version="v2",
            enrich_version="e3",
            input_checksum="before-enrich",
            request_params={"evidenceKnCodeList": ["1"], "topK": 5},
            batch_id="batch-601",
        )
    )

    assert {item.document_file_id for item in deps.enricher.evidence} == {10, 11, 12}
    assert {target.file_id for target in deps.enricher.targets} == {20}
    assert deps.search.requests[0].search_mode == "mixedRecall"
    assert deps.search.requests[0].top_k == 5

    update = deps.updater.requests[0]
    assert update.refer_signature == "before-enrich"
    metadata = parse_front_matter(update.file_content)
    assert metadata == {
        "documentKind": "knowledgeEntity",
        "processingCapabilities": ["entityEnrich"],
        "entityName": "Beta",
        "aliases": ["B"],
        "definitionVersion": "v2",
        "enrichVersion": "e3",
    }
    assert b"# Beta" in update.file_content
    assert deps.ingestion.indexed_paths == ["/KnowledgeEntity/beta.md"]
    generated = deps.updater.generated_assertions[0]
    assert len(generated) == 1
    assert generated[0].target_fs_entry_id == 20
    assert generated[0].relation_code == "PART_OF"
    assert generated[0].source_task_id == 601
    assert generated[0].definition_version == "v2"
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
        definition_version="v1",
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
