from __future__ import annotations

from types import SimpleNamespace

import pytest

from by_qa.knowledge_base.services import knowledge_entity_task_worker as worker_module
from by_qa.knowledge_base.services.knowledge_entity_discovery import (
    DiscoveryDocumentContext,
    EntityCandidate,
    EntityDiscoveryResult,
)
from by_qa.knowledge_base.services.knowledge_entity_enrichment import (
    EnrichmentResult,
    SemanticRelation,
    format_source_reference,
    organize_evidence,
)
from by_qa.knowledge_base.services.knowledge_entity_intelligence import (
    IdentityScope,
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


class FakeEnricher:
    def __init__(self, relations=()) -> None:
        self.relations = tuple(relations)
        self.evidence = []
        self.targets = ()
        self.log_context = None
        self.existing_markdown = ""

    async def enrich(
        self,
        identity,
        evidence,
        *,
        existing_markdown,
        relation_targets,
        log_context=None,
    ) -> EnrichmentResult:
        self.existing_markdown = existing_markdown
        self.evidence = list(evidence)
        self.targets = relation_targets
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
        entity_enriched=True,
        aliases=["A"],
    )
    cross_kb = file_row(
        99,
        "/KnowledgeEntity/cross.md",
        kb_id=2,
        content_key="cross",
        markdown_key="cross-md",
        document_kind="knowledgeEntity",
        entity_name="Cross",
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
    assert all(request.process_front_matter for request in deps.ingestion.uploads)
    assert any(
        action.get("entityFileId") == 20
        and action.get("entityName") == "Alpha"
        and action["action"] == "ANCHORED"
        for action in result.result_payload["actions"]
    )
    paths = [request.file_path for request in deps.ingestion.uploads]
    assert paths == [
        "/KnowledgeEntity/Beta-Unsafe.md",
        "/KnowledgeEntity/Alpha-Worker.md",
    ]

    metadata = [
        parse_front_matter(request.file_content) for request in deps.ingestion.uploads
    ]
    assert metadata[0]["documentKind"] == "knowledgeEntity"
    assert metadata[0]["entityEnriched"] is False
    assert "definitionVersion" not in metadata[0]
    assert metadata[1]["subjectFileId"] == 20
    assert "Worker" in metadata[1]["aliases"]
    mention_targets = {item["target_fs_entry_id"] for item in deps.references.upserts}
    assert mention_targets == set(result.target_file_ids)
    assert 20 in mention_targets
    assert 99 not in mention_targets
    assert all(item["relation_code"] == "MENTIONS" for item in deps.references.upserts)
    assert {item["source_fs_entry_id"] for item in deps.references.upserts} == {10}
    assert all(item["target_fs_entry_id"] != 10 for item in deps.references.upserts)
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
    for request in deps.ingestion.uploads:
        _, body = split_front_matter(request.file_content)
        spans = detect_reference_spans(body.decode("utf-8"))
        assert len(spans) == 1
        assert {span[3] for span in spans} == {"/docs/source.md"}
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
    found_log = next(message for message in log_messages if "entities found" in message)
    assert (
        'source_document={"knowledgeBaseId":1,"kbCode":"1","fileId":10,'
        '"filePath":"/docs/source.md"}' in found_log
    )
    assert '"entityName":"A"' in found_log
    assert '"entityName":"Alpha-Worker"' in found_log
    assert '"identityScope":"subject"' in found_log
    assert "Worker belongs to Alpha" not in found_log
    changes_log = next(
        message for message in log_messages if "entity changes" in message
    )
    assert (
        'source_document={"knowledgeBaseId":1,"kbCode":"1","fileId":10,'
        '"filePath":"/docs/source.md"}' in changes_log
    )
    assert "new_entities=" in changes_log
    assert '"entityName":"Beta/Unsafe"' in changes_log
    assert "existing_entities=" in changes_log
    assert '"entityName":"Alpha"' in changes_log
    assert "dropped_entities=" in changes_log
    assert '"entityName":"Missing-Child"' in changes_log
    assert "relation_replacement_count=3" in rendered_logs
    assert "batch_id=batch-501" in rendered_logs
    assert "Alpha and Cross are mentioned" not in rendered_logs


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
    deps = make_worker(
        rows=[source, occupied],
        objects={
            ("original", "source"): b"Alpha Beta is mentioned.",
            ("original", "occupied"): b"# Alpha/Beta",
            ("markdown", "occupied-md"): b"# Alpha/Beta",
        },
        discovery=FakeDiscovery(
            (
                EntityCandidate(
                    entity_name="Alpha/Beta",
                    local_name="Alpha/Beta",
                    identity_scope=IdentityScope.GLOBAL,
                    evidence="Alpha Beta is a stable component.",
                ),
            )
        ),
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
    assert result.result_payload["actions"] == [
        {
            "action": "ANCHORED",
            "entityName": "Alpha/Beta",
            "entityFileId": 11,
            "filePath": "/KnowledgeEntity/Alpha-Beta.md",
        }
    ]
    assert len(deps.references.upserts) == 1
    assert deps.references.upserts[0]["source_fs_entry_id"] == 10
    assert deps.references.upserts[0]["target_fs_entry_id"] == 11


@pytest.mark.asyncio
async def test_discovery_does_not_anchor_incidental_ac_match_omitted_by_salience() -> (
    None
):
    source = file_row(10, "/docs/source.md", content_key="source")
    incidental = file_row(
        20,
        "/KnowledgeEntity/alpha.md",
        content_key="alpha",
        markdown_key="alpha-md",
        document_kind="knowledgeEntity",
        entity_name="Alpha",
        entity_enriched=True,
    )
    deps = make_worker(
        rows=[source, incidental],
        objects={
            ("original", "source"): b"Alpha appears only in a footnote.",
            ("original", "alpha"): b"# Alpha",
            ("markdown", "alpha-md"): b"# Alpha",
        },
        discovery=FakeDiscovery(()),
    )

    result = await deps.worker.run_task(
        KnowledgeEntityTaskContext(
            task_id=504,
            task_type="ENTITY_DISCOVERY",
            kb_code="1",
            knowledge_base_id=1,
            source_file_id=10,
            file_path="/docs/source.md",
        )
    )

    assert deps.discovery.known_matches
    assert result.target_file_ids == ()
    assert result.result_payload["actions"] == []
    assert deps.references.upserts == []


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
            (
                EntityCandidate(
                    entity_name="Alpha/Beta",
                    local_name="Alpha/Beta",
                    identity_scope=IdentityScope.GLOBAL,
                    evidence="Alpha Beta is a stable component.",
                ),
            )
        ),
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
