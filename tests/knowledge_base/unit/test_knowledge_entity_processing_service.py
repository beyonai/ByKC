from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from by_qa.knowledge_base.api.knowledge_entity_schemas import (
    EntityDiscoveryRequest,
    EntityEnrichRequest,
    ProcessingBatchStatusRequest,
    ProcessingCapability,
    ProcessingEligibility,
    ProcessingEligibilityRequest,
    ProcessingTaskStatusRequest,
    SemanticRelationsRequest,
)
from by_qa.knowledge_base.services import (
    knowledge_entity_processing_service as processing_module,
)
from by_qa.knowledge_base.services.knowledge_entity_processing_service import (
    KnowledgeEntityProcessingOrchestrator,
)

pytestmark = pytest.mark.asyncio


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.locked_ids = []
        connection = self

        class Cursor:
            async def execute(self, sql, params):
                del self
                if "FOR UPDATE" in sql:
                    connection.locked_ids.append(params["file_id"])

        self.cursor_object = Cursor()

    def cursor(self):
        return self.cursor_object

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def close(self):
        pass


class KBRepo:
    async def get_by_code(self, cursor, kb_code):
        del cursor
        return {"kid": 7} if kb_code == "7" else None


def original(file_id=10, path="/docs/source.md", **updates):
    row = {
        "kid": file_id,
        "knowledge_base_id": 7,
        "file_path": path,
        "checksum": "sha-source",
        "markdown_bucket_name": "markdown",
        "markdown_object_key": f"{file_id}.md",
        "line_count": 3,
        "updated_at": datetime(2026, 8, 17, tzinfo=timezone.utc),
        "mime_type": "text/markdown",
        "document_kind": "original",
        "processing_capabilities": [],
        "processing_capabilities_configured": False,
        "entity_name": None,
        "aliases": [],
        "subject_file_id": None,
    }
    row.update(updates)
    return row


class EntityRepo:
    def __init__(self, files):
        self.files = files

    async def get_file_with_metadata(self, cursor, *, knowledge_base_id, file_path):
        del cursor, knowledge_base_id
        return next((row for row in self.files if row["file_path"] == file_path), None)

    async def list_files_with_metadata(
        self, cursor, *, knowledge_base_id, path_prefix=None
    ):
        del cursor, knowledge_base_id
        if path_prefix is None:
            return list(self.files)
        return [
            row for row in self.files if row["file_path"].startswith(path_prefix + "/")
        ]

    async def get_files_by_ids(self, cursor, *, knowledge_base_id, fs_entry_ids):
        del cursor, knowledge_base_id
        return [row for row in self.files if row["kid"] in fs_entry_ids]


class Tasks:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []

    async def list_processing_tasks(self, cursor, **filters):
        del cursor
        rows = [
            row
            for row in self.rows
            if row["knowledge_base_id"] == filters["knowledge_base_id"]
            and (
                filters.get("fs_entry_id") is None
                or row["fs_entry_id"] == filters["fs_entry_id"]
            )
            and (
                filters.get("task_type") is None
                or row["task_type"] == filters["task_type"]
            )
            and (
                filters.get("batch_id") is None
                or row.get("batch_id") == filters["batch_id"]
            )
            and (not filters.get("statuses") or row["status"] in filters["statuses"])
        ]
        rows.sort(key=lambda row: row["kid"], reverse=True)
        if filters.get("latest_only"):
            selected = {}
            for row in rows:
                selected.setdefault((row["fs_entry_id"], row["task_type"]), row)
            rows = list(selected.values())
        offset = filters.get("offset", 0)
        return rows[offset : offset + filters.get("limit", 50)]

    async def count_processing_tasks(self, cursor, **filters):
        filters = {**filters, "limit": 500, "offset": 0}
        return len(await self.list_processing_tasks(cursor, **filters))

    async def create_processing_task(self, cursor, **values):
        del cursor
        row = {
            "kid": 100 + len(self.rows),
            **values,
            "file_path": values.get("file_path_snapshot", "/docs/source.md"),
            "created_at": datetime.now(timezone.utc),
            "started_at": None,
            "finished_at": None,
            "index_version": None,
        }
        row.setdefault("result_payload", None)
        row.setdefault("error_code", None)
        row.setdefault("error_message", None)
        row.setdefault("failure_kind", None)
        row.setdefault("outcome_uncertain", False)
        self.rows.append(row)
        return row

    async def update_processing_task(self, cursor, *, task_id, task_type, **updates):
        del cursor, task_type
        self.updates.append((task_id, updates))
        row = next(row for row in self.rows if row["kid"] == task_id)
        row.update({key: value for key, value in updates.items() if value is not None})
        return row


class Batches:
    def __init__(self, tasks):
        self.tasks = tasks
        self.rows = {}

    async def create_batch(self, cursor, **values):
        del cursor
        values.setdefault("completed_count", 0)
        values.setdefault("version", 0)
        completed = values["completed_count"] == values["total_count"]
        row = {
            **values,
            "status": "completed" if completed else "processing",
            "completed_at": datetime.now(timezone.utc) if completed else None,
            "created_at": datetime.now(timezone.utc),
        }
        self.rows[values["batch_id"]] = row
        return row

    async def advance_batch(self, cursor, *, batch_id, completed_delta=1):
        del cursor
        row = self.rows[batch_id]
        row["completed_count"] += completed_delta
        row["version"] += completed_delta
        if row["completed_count"] == row["total_count"]:
            row["status"] = "completed"
            row["completed_at"] = datetime.now(timezone.utc)
        return dict(row)

    async def count_tasks_by_status(self, cursor, *, batch_id):
        del cursor
        counts = {}
        for row in self.tasks.rows:
            if row.get("batch_id") == batch_id:
                counts[row["status"]] = counts.get(row["status"], 0) + 1
        return counts

    async def get_batch(self, cursor, *, batch_id, knowledge_base_id=None):
        del cursor
        row = self.rows.get(batch_id)
        if row is None or (
            knowledge_base_id is not None
            and row["knowledge_base_id"] != knowledge_base_id
        ):
            return None
        return dict(row)


class Relations:
    def __init__(self, outgoing=None, incoming=None, markdown_sources=None):
        self.outgoing = outgoing or []
        self.incoming = incoming or []
        self.markdown_sources = markdown_sources or []
        self.target_queries = []

    async def list_relations_by_source(self, cursor, *, limit, offset, **kwargs):
        del cursor, kwargs
        return self.outgoing[offset : offset + limit]

    async def list_relations_by_target(self, cursor, *, limit, offset, **kwargs):
        del cursor, kwargs
        return self.incoming[offset : offset + limit]

    async def list_recent_assertions_by_target(
        self, cursor, *, limit, offset, **kwargs
    ):
        del cursor
        self.target_queries.append({"limit": limit, "offset": offset, **kwargs})
        return self.incoming[offset : offset + limit]

    async def count_relations_by_source(self, cursor, **kwargs):
        del cursor, kwargs
        return len(self.outgoing)

    async def count_relations_by_target(self, cursor, **kwargs):
        del cursor, kwargs
        return len(self.incoming)

    async def list_sources_by_target(self, cursor, **kwargs):
        del cursor, kwargs
        return self.markdown_sources


class Worker:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.contexts = []

    async def run_task(self, context):
        self.contexts.append(context)
        if self.error:
            raise self.error
        return self.result


class Scheduler:
    def __init__(self):
        self.factories = []

    def schedule(self, task_factory):
        self.factories.append(task_factory)

    async def run_all(self):
        for factory in self.factories:
            await factory()


def make_service(files, *, tasks=None, relations=None, worker=None, scheduler=None):
    del scheduler
    connection = Connection()
    task_repository = tasks or Tasks()

    async def connection_factory():
        return connection

    return (
        KnowledgeEntityProcessingOrchestrator(
            connection_factory=connection_factory,
            knowledge_base_repository=KBRepo(),
            knowledge_entity_repository=EntityRepo(files),
            knowledge_semantic_processing_task_repository=task_repository,
            knowledge_file_reference_repository=relations or Relations(),
            worker=worker or Worker(),
            knowledge_semantic_processing_batch_repository=Batches(task_repository),
        ),
        connection,
    )


async def test_eligibility_uses_content_fingerprint_and_reports_fresh_task():
    file_row = original()
    service, _ = make_service([file_row])
    stale = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=file_row["file_path"], capability="entityDiscovery"
        )
    )
    assert stale.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert stale.reason_code == "NEVER_PROCESSED"

    fingerprint = service._fingerprint(
        file_row, ProcessingCapability.ENTITY_DISCOVERY, []
    )
    service.knowledge_semantic_processing_task_repository.rows.append(
        {
            "kid": 80,
            "knowledge_base_id": 7,
            "fs_entry_id": 10,
            "file_path": file_row["file_path"],
            "task_type": "ENTITY_DISCOVERY",
            "status": "succeeded",
            "input_fingerprint": fingerprint,
            "method_version": processing_module.DISCOVERY_METHOD_VERSION,
            "finished_at": datetime.now(timezone.utc),
        }
    )
    fresh = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=file_row["file_path"], capability="entityDiscovery"
        )
    )
    assert fresh.eligibility == ProcessingEligibility.ELIGIBLE_BUT_FRESH
    assert fresh.last_successful_task_id == "80"


async def test_discovery_reports_method_version_change_from_task_method() -> None:
    file_row = original()
    tasks = Tasks(
        [
            {
                "kid": 81,
                "knowledge_base_id": 7,
                "fs_entry_id": 10,
                "file_path": file_row["file_path"],
                "task_type": "ENTITY_DISCOVERY",
                "status": "succeeded",
                "input_fingerprint": "old-method-fingerprint",
                "method_version": "discovery/1.2",
                "finished_at": datetime.now(timezone.utc),
            }
        ]
    )
    service, _ = make_service([file_row], tasks=tasks)

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=file_row["file_path"],
            capability="entityDiscovery",
        )
    )

    assert result.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert result.reason_code == "METHOD_VERSION_CHANGED"


async def test_enrich_fingerprint_canonicalizes_latest_relation_timestamp():
    service, _ = make_service([original()])
    entity = original(
        21,
        "/KnowledgeEntity/child.md",
        document_kind="knowledgeEntity",
        entity_name="Child",
        aliases=["Child alias"],
    )
    utc_timestamp = datetime(2026, 8, 17, 9, 30, 0, 123456, tzinfo=timezone.utc)
    local_timestamp = utc_timestamp.astimezone(timezone(timedelta(hours=8)))

    fingerprints = {
        service._fingerprint(
            entity,
            ProcessingCapability.ENTITY_ENRICH,
            [{"kid": 31, "created_at": timestamp}],
        )
        for timestamp in (utc_timestamp, local_timestamp)
    }

    assert len(fingerprints) == 1


async def test_enrich_fingerprint_changes_with_latest_relation_watermark():
    service, _ = make_service([original()])
    entity = original(
        21,
        "/KnowledgeEntity/child.md",
        document_kind="knowledgeEntity",
        entity_name="Child",
    )
    first = service._fingerprint(
        entity,
        ProcessingCapability.ENTITY_ENRICH,
        [{"kid": 31, "created_at": datetime(2026, 8, 17, tzinfo=timezone.utc)}],
    )
    second = service._fingerprint(
        entity,
        ProcessingCapability.ENTITY_ENRICH,
        [{"kid": 32, "created_at": datetime(2026, 8, 18, tzinfo=timezone.utc)}],
    )
    assert first != second


async def test_invalid_subject_file_id_is_ineligible_instead_of_json_failure():
    entity = original(
        21,
        "/KnowledgeEntity/child.md",
        document_kind="knowledgeEntity",
        entity_name="Child",
        aliases=[],
        subject_file_id="21.5",
    )
    service, _ = make_service([entity])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=entity["file_path"],
            capability="entityEnrich",
        )
    )

    assert result.eligibility == ProcessingEligibility.INELIGIBLE
    assert result.reason_code == "IDENTITY_METADATA_INCOMPLETE"


@pytest.mark.parametrize(
    "raw_database_value",
    [Decimal("21"), datetime.now(timezone.utc), uuid4()],
)
async def test_canonical_json_rejects_database_types_without_domain_normalization(
    raw_database_value,
):
    with pytest.raises(TypeError, match="normalize database types explicitly"):
        processing_module._canonical_json({"value": raw_database_value})


async def test_explicit_empty_capabilities_disable_default_and_content_must_be_ready():
    disabled = original(
        path="/docs/disabled.pdf",
        mime_type="application/pdf",
        processing_capabilities_configured=True,
    )
    service, _ = make_service([disabled])
    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=disabled["file_path"], capability="entityDiscovery"
        )
    )
    assert result.reason_code == "CAPABILITY_DISABLED"

    not_ready = original(markdown_object_key=None)
    service, _ = make_service([not_ready])
    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=not_ready["file_path"], capability="entityDiscovery"
        )
    )
    assert result.reason_code == "CONTENT_NOT_READY"


@pytest.mark.parametrize(
    ("path", "mime_type"),
    [
        ("/docs/source.md", "application/pdf"),
        ("/docs/source.MD", None),
        ("/docs/source.txt", "text/plain; charset=utf-8"),
        ("/docs/source.html", "text/html"),
        ("/docs/source.csv", "text/csv"),
        ("/docs/legacy.markdown", None),
        ("/docs/legacy.htm", "application/octet-stream"),
    ],
)
async def test_discovery_accepts_supported_text_suffixes(path, mime_type):
    file_row = original(path=path, mime_type=mime_type)
    service, _ = make_service([file_row])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=path, capability="entityDiscovery"
        )
    )

    assert result.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert result.reason_code == "NEVER_PROCESSED"


async def test_suffixless_text_mime_is_supported_but_suffix_is_authoritative():
    suffixless = original(path="/docs/README", mime_type="text/plain")
    disguised_pdf = original(
        11,
        "/docs/disguised.pdf",
        mime_type="text/plain",
    )
    service, _ = make_service([suffixless, disguised_pdf])

    allowed = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=suffixless["file_path"],
            capability="entityDiscovery",
        )
    )
    rejected = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=disguised_pdf["file_path"],
            capability="entityDiscovery",
        )
    )

    assert allowed.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert rejected.reason_code == "UNSUPPORTED_FILE_FORMAT"


async def test_pdf_with_ready_markdown_sidecar_is_not_discovery_input():
    pdf = original(path="/docs/source.pdf", mime_type="application/pdf")
    service, _ = make_service([pdf])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7", filePath=pdf["file_path"], capability="entityDiscovery"
        )
    )

    assert pdf["markdown_object_key"]
    assert pdf["line_count"] > 0
    assert result.eligibility == ProcessingEligibility.INELIGIBLE
    assert result.reason_code == "UNSUPPORTED_FILE_FORMAT"


async def test_content_type_rejection_precedes_content_readiness_and_enrich_evidence():
    entity_pdf = original(
        20,
        "/KnowledgeEntity/entity.pdf",
        mime_type="application/pdf",
        markdown_bucket_name=None,
        markdown_object_key=None,
        line_count=0,
        document_kind="knowledgeEntity",
        entity_name="Entity",
        aliases=[],
    )
    service, _ = make_service([entity_pdf])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=entity_pdf["file_path"],
            capability="entityEnrich",
        )
    )

    assert result.eligibility == ProcessingEligibility.INELIGIBLE
    assert result.reason_code == "UNSUPPORTED_CONTENT_TYPE"


async def test_enrich_requires_markdown_in_reserved_entity_directory():
    entity_txt = original(
        20,
        "/KnowledgeEntity/entity.txt",
        mime_type="text/plain",
        document_kind="knowledgeEntity",
        entity_name="Entity",
        aliases=[],
    )
    misplaced_entity = original(
        21,
        "/docs/entity.txt",
        mime_type="text/plain",
        document_kind="knowledgeEntity",
        entity_name="Misplaced",
        aliases=[],
    )
    service, _ = make_service([entity_txt, misplaced_entity])

    unsupported = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=entity_txt["file_path"],
            capability="entityEnrich",
        )
    )
    misplaced = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=misplaced_entity["file_path"],
            capability="entityEnrich",
        )
    )

    assert unsupported.reason_code == "UNSUPPORTED_CONTENT_TYPE"
    assert misplaced.reason_code == "KNOWLEDGE_ENTITY_PATH_REQUIRED"


async def test_whole_kb_discovery_schedules_only_text_documents():
    scheduler = Scheduler()
    files = [
        original(10, "/docs/a.md", mime_type="text/markdown"),
        original(11, "/docs/b.txt", mime_type="text/plain"),
        original(12, "/docs/c.html", mime_type="text/html"),
        original(13, "/docs/d.csv", mime_type="text/csv"),
        original(14, "/docs/e.pdf", mime_type="application/pdf"),
        original(
            15,
            "/docs/f.docx",
            mime_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        ),
    ]
    service, _ = make_service(files, scheduler=scheduler)

    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(knCode="7")
    )

    assert accepted.eligible_count == 4
    assert accepted.accepted_count == 4
    assert accepted.skipped_count == 2
    assert scheduler.factories == []
    assert [
        row["status"]
        for row in service.knowledge_semantic_processing_task_repository.rows
    ].count("pending") == 4
    assert [
        row["status"]
        for row in service.knowledge_semantic_processing_task_repository.rows
    ].count("skipped") == 2


async def test_whole_kb_enrich_schedules_only_markdown_entities():
    source = original(10, "/docs/source.md")
    markdown_entity = original(
        20,
        "/KnowledgeEntity/a.md",
        document_kind="knowledgeEntity",
        entity_name="A",
        aliases=[],
    )
    text_entity = original(
        21,
        "/KnowledgeEntity/b.txt",
        mime_type="text/plain",
        document_kind="knowledgeEntity",
        entity_name="B",
        aliases=[],
    )
    markdown_entity_long_suffix = original(
        22,
        "/KnowledgeEntity/c.MARKDOWN",
        mime_type=None,
        document_kind="knowledgeEntity",
        entity_name="C",
        aliases=[],
    )
    scheduler = Scheduler()
    relations = Relations(
        incoming=[
            {
                "kid": 501,
                "source_fs_entry_id": 10,
                "target_fs_entry_id": 20,
                "relation_code": "MENTIONS",
                "updated_at": datetime.now(timezone.utc),
            }
        ]
    )
    service, _ = make_service(
        [source, markdown_entity, text_entity, markdown_entity_long_suffix],
        relations=relations,
        scheduler=scheduler,
    )

    accepted = await service.enrich_knowledge_entities(
        EntityEnrichRequest(
            knCode="7",
            extParams={"requestId": "legacy-enrich"},
        )
    )

    assert accepted.eligible_count == 2
    assert accepted.accepted_count == 2
    assert accepted.skipped_count == 1
    assert scheduler.factories == []
    assert [
        row["status"]
        for row in service.knowledge_semantic_processing_task_repository.rows
    ].count("pending") == 2
    assert [
        row["status"]
        for row in service.knowledge_semantic_processing_task_repository.rows
    ].count("skipped") == 1
    assert all(
        "extParams" not in row["request_params"]
        for row in service.knowledge_semantic_processing_task_repository.rows
    )


async def test_missing_metadata_defaults_ordinary_document_to_discovery_input():
    unclassified = original(document_kind=None)
    service, _ = make_service([unclassified])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=unclassified["file_path"],
            capability="entityDiscovery",
        )
    )

    assert result.document_kind == "original"
    assert result.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert result.reason_code == "NEVER_PROCESSED"


async def test_whole_kb_discovery_accepts_unclassified_ordinary_files_only():
    scheduler = Scheduler()
    files = [
        original(10, "/docs/legacy.md", document_kind=None),
        original(11, "/KnowledgeEntity/legacy-entity.md", document_kind=None),
    ]
    service, _ = make_service(files, scheduler=scheduler)

    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(knCode="7")
    )

    assert accepted.eligible_count == 1
    assert accepted.accepted_count == 1
    assert accepted.skipped_count == 1
    assert scheduler.factories == []


async def test_missing_kind_in_entity_directory_never_defaults_to_original():
    entity = original(
        20,
        "/KnowledgeEntity/incomplete.md",
        document_kind=None,
    )
    service, _ = make_service([entity])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=entity["file_path"],
            capability="entityEnrich",
        )
    )

    assert result.document_kind == "knowledgeEntity"
    assert result.eligibility == ProcessingEligibility.INELIGIBLE
    assert result.reason_code == "IDENTITY_METADATA_INCOMPLETE"


async def test_identity_metadata_outside_entity_directory_defaults_to_original():
    ordinary = original(
        document_kind=None,
        entity_name="legacy-name",
        aliases=["legacy-alias"],
        subject_file_id="42",
    )
    service, _ = make_service([ordinary])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=ordinary["file_path"],
            capability="entityDiscovery",
        )
    )

    assert result.document_kind == "original"
    assert result.eligibility == ProcessingEligibility.ELIGIBLE_AND_STALE
    assert result.reason_code == "NEVER_PROCESSED"


async def test_explicit_blank_document_kind_remains_invalid_instead_of_defaulting():
    invalid = original(
        document_kind="",
        document_kind_configured=True,
    )
    service, _ = make_service([invalid])

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=invalid["file_path"],
            capability="entityDiscovery",
        )
    )

    assert result.document_kind == "unknown"
    assert result.eligibility == ProcessingEligibility.INELIGIBLE
    assert result.reason_code == "DOCUMENT_KIND_MISMATCH"


async def test_enrich_accepts_resolved_markdown_reference_as_evidence():
    entity = original(
        20,
        "/KnowledgeEntity/A.md",
        document_kind="knowledgeEntity",
        entity_name="A",
        aliases=[],
    )
    source = original(10)
    relations = Relations(
        incoming=[
            {
                "kid": 1,
                "source_fs_entry_id": 10,
                "relation_code": "MENTIONS",
                "created_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            }
        ]
    )
    service, _ = make_service([source, entity], relations=relations)
    accepted = await service.enrich_knowledge_entities(
        EntityEnrichRequest(knCode="7", filePath="/KnowledgeEntity/A.md")
    )
    assert accepted.eligible_count == 1
    assert accepted.accepted_count == 1


@pytest.mark.parametrize(
    ("relation_created_at", "expected", "reason"),
    [
        (
            datetime(2026, 8, 19, tzinfo=timezone.utc),
            ProcessingEligibility.ELIGIBLE_AND_STALE,
            "NEW_RELATION",
        ),
        (
            datetime(2026, 8, 17, tzinfo=timezone.utc),
            ProcessingEligibility.ELIGIBLE_BUT_FRESH,
            "NO_NEW_RELATIONS",
        ),
    ],
)
async def test_enrich_staleness_uses_any_recent_relation_creation_time(
    relation_created_at, expected, reason
):
    entity = original(
        20,
        "/KnowledgeEntity/A.md",
        document_kind="knowledgeEntity",
        entity_name="A",
        aliases=[],
        updated_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    source = original(10)
    relations = Relations(
        incoming=[
            {
                "kid": 9,
                "source_fs_entry_id": 10,
                "relation_code": "DEPENDS_ON",
                "created_at": relation_created_at,
            }
        ]
    )
    tasks = Tasks(
        [
            {
                "kid": 80,
                "knowledge_base_id": 7,
                "fs_entry_id": 20,
                "file_path": entity["file_path"],
                "task_type": "DOCUMENT_ENRICH",
                "status": "succeeded",
                "input_fingerprint": "previous",
                "finished_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
            }
        ]
    )
    service, _ = make_service([source, entity], relations=relations, tasks=tasks)

    result = await service.evaluate_processing_eligibility(
        ProcessingEligibilityRequest(
            knCode="7",
            filePath=entity["file_path"],
            capability="entityEnrich",
        )
    )

    assert result.eligibility == expected
    assert result.reason_code == reason
    assert relations.target_queries[0]["relation_code"] is None


async def test_accept_persists_pending_task_without_request_scheduler():
    scheduler = Scheduler()
    worker = Worker()
    tasks = Tasks()
    service, connection = make_service(
        [original()], tasks=tasks, worker=worker, scheduler=scheduler
    )
    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(
            knCode="7",
            filePath="/docs/source.md",
            extParams={"requestId": "legacy-1", "nested": {"keep": True}},
        ),
    )
    assert accepted.accepted_count == 1
    assert accepted.tasks[0].status.value == "PENDING"
    assert scheduler.factories == []
    assert worker.contexts == []
    assert tasks.rows[0]["status"] == "pending"
    assert "extra_params" not in tasks.rows[0]
    assert "ext_params" not in tasks.rows[0]
    assert "extParams" not in tasks.rows[0]["request_params"]
    assert "extraParams" not in tasks.rows[0]["request_params"]
    assert connection.locked_ids == [10]
    assert connection.rollbacks == 0


async def test_acceptance_logs_correlation(monkeypatch):
    captured = []

    def capture(level):
        def record(message, *args, **kwargs):
            del kwargs
            captured.append((level, message % args if args else message))

        return record

    monkeypatch.setattr(processing_module.logger, "debug", capture("debug"))
    monkeypatch.setattr(processing_module.logger, "info", capture("info"))
    monkeypatch.setattr(processing_module.logger, "warning", capture("warning"))
    scheduler = Scheduler()
    service, _ = make_service([original()], scheduler=scheduler)

    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(
            knCode="7",
            filePath="/docs/source.md",
        ),
    )

    rendered = "\n".join(message for _, message in captured)
    assert f"batch_id={accepted.batch_id}" in rendered
    assert "task_id=100" in rendered
    assert "knowledge_base_id=7" in rendered
    assert "file_path=/docs/source.md" in rendered
    assert "task_type=ENTITY_DISCOVERY" in rendered
    assert "batch accepted" in rendered
    assert "sha-source" not in rendered


async def test_same_fingerprint_succeeded_task_is_reused_without_scheduling():
    file_row = original()
    service, _ = make_service([file_row])
    fingerprint = service._fingerprint(
        file_row, ProcessingCapability.ENTITY_DISCOVERY, []
    )
    service.knowledge_semantic_processing_task_repository.rows.append(
        {
            "kid": 81,
            "knowledge_base_id": 7,
            "fs_entry_id": 10,
            "file_path": file_row["file_path"],
            "task_type": "ENTITY_DISCOVERY",
            "status": "succeeded",
            "input_fingerprint": fingerprint,
            "method_version": processing_module.DISCOVERY_METHOD_VERSION,
            "finished_at": datetime.now(timezone.utc),
        }
    )
    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(knCode="7", filePath="/docs/source.md")
    )
    assert accepted.accepted_count == 0
    assert accepted.reused_count == 1
    assert accepted.tasks[0].task_id != "81"
    assert accepted.tasks[0].status.value == "SKIPPED"
    created = service.knowledge_semantic_processing_task_repository.rows[-1]
    assert created["result_payload"] == {
        "reasonCode": "INPUT_UNCHANGED",
        "reusedTaskId": "81",
    }


async def test_pending_same_fingerprint_is_reused_under_file_lock():
    file_row = original()
    service, connection = make_service([file_row])
    fingerprint = service._fingerprint(
        file_row, ProcessingCapability.ENTITY_DISCOVERY, []
    )
    service.knowledge_semantic_processing_task_repository.rows.append(
        {
            "kid": 82,
            "knowledge_base_id": 7,
            "fs_entry_id": 10,
            "file_path": file_row["file_path"],
            "task_type": "ENTITY_DISCOVERY",
            "status": "pending",
            "input_fingerprint": fingerprint,
            "method_version": processing_module.DISCOVERY_METHOD_VERSION,
            "finished_at": None,
        }
    )
    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(knCode="7", filePath="/docs/source.md")
    )
    assert accepted.reused_count == 1
    assert accepted.tasks[0].status.value == "SKIPPED"
    assert connection.locked_ids == [10]


async def test_force_reuses_active_task_even_when_fingerprint_changed():
    file_row = original()
    service, connection = make_service([file_row])
    service.knowledge_semantic_processing_task_repository.rows.append(
        {
            "kid": 83,
            "knowledge_base_id": 7,
            "fs_entry_id": 10,
            "file_path": file_row["file_path"],
            "task_type": "ENTITY_DISCOVERY",
            "status": "running",
            "input_fingerprint": "older-input",
            "finished_at": None,
        }
    )

    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(knCode="7", filePath="/docs/source.md", force=True)
    )

    assert accepted.accepted_count == 0
    assert accepted.reused_count == 1
    assert accepted.tasks[0].task_id != "83"
    assert accepted.tasks[0].status.value == "SKIPPED"
    created = service.knowledge_semantic_processing_task_repository.rows[-1]
    assert created["result_payload"] == {
        "reasonCode": "ALREADY_PROCESSING",
        "activeTaskId": "83",
    }
    assert connection.locked_ids == [10]


async def test_status_filters_by_optional_file_and_hides_details():
    now = datetime.now(timezone.utc)
    tasks = Tasks(
        [
            {
                "kid": 90,
                "knowledge_base_id": 7,
                "fs_entry_id": 10,
                "file_path": "/docs/source.md",
                "task_type": "ENTITY_DISCOVERY",
                "batch_id": "ed-1",
                "status": "failed",
                "current_stage": "failed",
                "progress": 100,
                "index_version": None,
                "created_at": now,
                "started_at": now,
                "finished_at": now,
                "result_payload": {"private": True},
                "error_code": "X",
                "error_message": "boom",
            }
        ]
    )
    service, _ = make_service([original()], tasks=tasks)
    page = await service.get_processing_task_status(
        ProcessingTaskStatusRequest(knCode="7", filePath="/docs/source.md")
    )
    assert page.total == 1
    assert page.data[0].result is None
    assert page.data[0].error is None


async def test_batch_status_reports_per_file_progress_without_extra_params():
    service, _ = make_service([original()])
    accepted = await service.discover_knowledge_entities(
        EntityDiscoveryRequest(
            knCode="7",
            filePath="/docs/source.md",
        )
    )

    result = await service.get_processing_batch_status(
        ProcessingBatchStatusRequest(knCode="7", batchId=accepted.batch_id)
    )

    assert result.total_count == 1
    assert result.completed_count == 0
    assert result.pending_count == 1
    assert result.progress == 0
    assert not hasattr(result, "extra_params")
    assert not hasattr(result.data[0], "extra_params")


async def test_both_relation_direction_merges_by_relation_id_before_paging():
    files = [
        original(10, "/docs/source.md"),
        original(11, "/KnowledgeEntity/A.md", document_kind="knowledgeEntity"),
        original(12, "/KnowledgeEntity/B.md", document_kind="knowledgeEntity"),
    ]

    def edge(kid, source, target):
        return {
            "kid": kid,
            "source_fs_entry_id": source,
            "target_fs_entry_id": target,
            "relation_code": "MENTIONS",
            "confidence": 1,
            "discovered_by": "AC_EXACT",
            "source_task_id": 2,
        }

    relations = Relations(outgoing=[edge(3, 10, 12)], incoming=[edge(1, 11, 10)])
    service, _ = make_service(files, relations=relations)
    page = await service.get_semantic_relations(
        SemanticRelationsRequest(
            knCode="7", filePath="/docs/source.md", pageNum=1, pageSize=1
        )
    )
    assert page.total == 2
    assert page.data[0].relation_id.startswith("lr_")
    assert page.data[0].source.file_id == "11"
    assert page.data[0].direction.value == "INCOMING"


async def test_relation_preserves_zero_confidence_and_evidence_fields():
    files = [
        original(10, document_kind=None),
        original(11, "/KnowledgeEntity/A.md", document_kind=None),
    ]
    edge = {
        "kid": 1,
        "source_fs_entry_id": 10,
        "target_fs_entry_id": 11,
        "relation_code": "MENTIONS",
        "confidence": 0,
        "discovered_by": "LLM",
        "assertion_count": 2,
        "producer_run_id": "markdown:7",
        "evidence_fingerprint": "fingerprint-1",
        "source_heading_path": "Guide / Concepts",
        "start_line": 8,
        "end_line": 8,
        "start_offset": 100,
        "end_offset": 120,
        "source_task_id": None,
    }
    service, _ = make_service(files, relations=Relations(outgoing=[edge]))
    page = await service.get_semantic_relations(
        SemanticRelationsRequest(
            knCode="7",
            filePath="/docs/source.md",
            direction="OUTGOING",
        )
    )
    assert page.data[0].confidence == 0
    assert page.data[0].assertion_count == 2
    assert page.data[0].source.document_kind == "original"
    assert page.data[0].target.document_kind == "knowledgeEntity"
    assert page.data[0].representative_evidence.source_heading_path == (
        "Guide / Concepts"
    )
    assert page.data[0].representative_evidence.start_line == 8
