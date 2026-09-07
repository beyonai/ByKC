"""Task fencing performed by knowledge-base deletion."""

import pytest

from by_qa.knowledge_base.api.schemas import DeleteKnowledgeBaseRequest
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


class Connection:
    def __init__(self):
        self.committed = False

    def cursor(self):
        return self

    async def execute(self, sql, params=None):
        del sql, params

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass

    async def close(self):
        pass


class KnowledgeBases:
    async def get_by_code(self, cursor, kb_code):
        del cursor, kb_code
        return {"kid": 7}

    async def soft_delete_by_code(self, cursor, *, kb_code):
        del cursor, kb_code


class Files:
    async def soft_delete_by_knowledge_base_id(self, cursor, *, knowledge_base_id):
        del cursor, knowledge_base_id


class BuildMutation:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    async def terminate_active(self, cursor, **kwargs):
        del cursor
        self.calls.append(("terminate", kwargs))
        return ([{"kid": 11}], ["fb-1"])

    async def publish(self, tasks, batch_ids):
        assert self.connection.committed
        self.calls.append(("publish", (tasks, batch_ids)))


class SemanticMutation:
    def __init__(self, connection):
        self.connection = connection
        self.calls = []

    async def terminate_for_knowledge_base(self, cursor, **kwargs):
        del cursor
        self.calls.append(("terminate", kwargs))
        return ([{"kid": 21}], {"ed-1": ({}, {})})

    async def publish(self, tasks, batches):
        assert self.connection.committed
        self.calls.append(("publish", (tasks, batches)))


@pytest.mark.asyncio
async def test_delete_knowledge_base_fences_both_task_pools_before_commit():
    connection = Connection()
    build_mutation = BuildMutation(connection)
    semantic_mutation = SemanticMutation(connection)

    async def connection_factory():
        return connection

    service = KnowledgeBaseService(
        connection_factory=connection_factory,
        knowledge_base_repository=KnowledgeBases(),
        knowledge_fs_entry_repository=Files(),
        file_build_mutation_service=build_mutation,
        semantic_task_mutation_service=semantic_mutation,
    )

    await service.delete_knowledge_base(DeleteKnowledgeBaseRequest(knCode="7"))

    assert build_mutation.calls == [
        (
            "terminate",
            {
                "knowledge_base_id": 7,
                "error_code": "KNOWLEDGE_BASE_DELETED",
                "error_message": "Knowledge base was deleted",
            },
        ),
        ("publish", ([{"kid": 11}], ["fb-1"])),
    ]
    assert semantic_mutation.calls == [
        ("terminate", {"knowledge_base_id": 7}),
        ("publish", ([{"kid": 21}], {"ed-1": ({}, {})})),
    ]
