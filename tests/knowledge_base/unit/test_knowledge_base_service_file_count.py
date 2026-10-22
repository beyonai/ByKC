"""Tests for knowledge-base file counting and read connection cleanup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from by_qa.knowledge_base.api.schemas import KnowledgeBaseFileCountRequest
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError
from by_qa.knowledge_base.services.knowledge_base_service import KnowledgeBaseService


@pytest.fixture(name="file_count_service")
def _file_count_service():
    cursor = object()
    connection = SimpleNamespace(cursor=lambda: cursor, close=AsyncMock())
    service = KnowledgeBaseService(
        connection_factory=AsyncMock(return_value=connection),
        knowledge_base_repository=SimpleNamespace(
            get_by_code=AsyncMock(return_value={"kid": 7})
        ),
        knowledge_fs_entry_repository=SimpleNamespace(
            count_file_entries_by_knowledge_base_id=AsyncMock(return_value=0)
        ),
    )
    return service, connection, cursor


@pytest.mark.parametrize("count", [0, 12])
async def test_file_count_uses_resolved_knowledge_base_id_and_closes_connection(
    file_count_service, count
):
    service, connection, cursor = file_count_service
    counter = (
        service.knowledge_fs_entry_repository.count_file_entries_by_knowledge_base_id
    )
    counter.return_value = count

    result = await service.file_count(KnowledgeBaseFileCountRequest(knCode="7"))

    assert result == {"fileCount": count}
    service.knowledge_base_repository.get_by_code.assert_awaited_once_with(cursor, "7")
    counter.assert_awaited_once_with(cursor, knowledge_base_id=7)
    connection.close.assert_awaited_once_with()


async def test_file_count_rejects_missing_knowledge_base_and_closes_connection(
    file_count_service,
):
    service, connection, _ = file_count_service
    service.knowledge_base_repository.get_by_code.return_value = None

    with pytest.raises(
        KnowledgeBaseValidationError, match="knowledge base not found: 7"
    ):
        await service.file_count(KnowledgeBaseFileCountRequest(knCode="7"))

    service.knowledge_fs_entry_repository.count_file_entries_by_knowledge_base_id.assert_not_awaited()
    connection.close.assert_awaited_once_with()


async def test_file_count_closes_connection_after_query_failure(file_count_service):
    service, connection, _ = file_count_service
    service.knowledge_fs_entry_repository.count_file_entries_by_knowledge_base_id.side_effect = RuntimeError(
        "query failed"
    )

    with pytest.raises(RuntimeError, match="query failed"):
        await service.file_count(KnowledgeBaseFileCountRequest(knCode="7"))

    connection.close.assert_awaited_once_with()
