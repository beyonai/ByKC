from typing import Any

from by_qa.knowledge_base.services.markdown_reference_resolver import (
    MarkdownReferenceResolver,
)


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = object()
        self.closed = False

    def cursor(self) -> object:
        return self.cursor_obj

    async def close(self) -> None:
        self.closed = True


class FakeReferenceRepository:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[dict[str, Any]] = []

    async def list_by_reference_ids(
        self, cursor: Any, *, reference_ids: list[int]
    ) -> list[dict[str, Any]]:
        self.calls.append({"cursor": cursor, "reference_ids": reference_ids})
        requested = set(reference_ids)
        return [row for row in self.rows if int(row["kid"]) in requested]


async def _resolve(
    texts: list[str],
    rows: list[dict[str, Any]],
) -> tuple[list[str], FakeReferenceRepository]:
    connection = FakeConnection()
    repository = FakeReferenceRepository(rows)
    resolver = MarkdownReferenceResolver(
        connection_factory=lambda: connection,
        reference_repository=repository,
    )

    resolved = await resolver.resolve_texts(knowledge_base_id=7, texts=texts)

    assert connection.closed is True
    return resolved, repository


async def test_resolved_visible_target_returns_virtual_path_with_suffix():
    resolved, repository = await _resolve(
        ["see byqa-ref://11"],
        [
            {
                "kid": 11,
                "knowledge_base_id": 7,
                "status": "resolved",
                "original_target": "old.md#original",
                "target_suffix": "#section",
                "target_virtual_path": "/docs/current.md",
                "target_is_deleted": False,
            }
        ],
    )

    assert resolved == ["see /docs/current.md#section"]
    assert repository.calls[0]["reference_ids"] == [11]


async def test_resolved_deleted_target_returns_original_target():
    resolved, repository = await _resolve(
        ["see byqa-ref://12"],
        [
            {
                "kid": 12,
                "knowledge_base_id": 7,
                "status": "resolved",
                "original_target": "deleted.md#old",
                "target_suffix": "#new",
                "target_virtual_path": "/docs/deleted.md",
                "target_is_deleted": True,
            }
        ],
    )

    assert repository.calls[0]["reference_ids"] == [12]
    assert resolved == ["see deleted.md#old"]


async def test_resolved_missing_joined_target_returns_original_target():
    resolved, repository = await _resolve(
        ["see byqa-ref://13"],
        [
            {
                "kid": 13,
                "knowledge_base_id": 7,
                "status": "resolved",
                "original_target": "missing.md",
                "target_suffix": "",
                "target_virtual_path": None,
                "target_is_deleted": None,
            }
        ],
    )

    assert repository.calls[0]["reference_ids"] == [13]
    assert resolved == ["see missing.md"]


async def test_unresolved_returns_original_target_without_appending_suffix_again():
    resolved, repository = await _resolve(
        ["see byqa-ref://14"],
        [
            {
                "kid": 14,
                "knowledge_base_id": 7,
                "status": "unresolved",
                "original_target": "draft.md#already",
                "target_suffix": "#already",
                "target_virtual_path": None,
                "target_is_deleted": None,
            }
        ],
    )

    assert repository.calls[0]["reference_ids"] == [14]
    assert resolved == ["see draft.md#already"]


async def test_broken_returns_original_target_without_appending_suffix_again():
    resolved, repository = await _resolve(
        ["see byqa-ref://15"],
        [
            {
                "kid": 15,
                "knowledge_base_id": 7,
                "status": "broken",
                "original_target": "gone.md?download=1",
                "target_suffix": "?download=1",
                "target_virtual_path": "/docs/gone.md",
                "target_is_deleted": True,
            }
        ],
    )

    assert repository.calls[0]["reference_ids"] == [15]
    assert resolved == ["see gone.md?download=1"]


async def test_unknown_reference_id_keeps_original_token():
    resolved, repository = await _resolve(["see byqa-ref://99"], [])

    assert repository.calls[0]["reference_ids"] == [99]
    assert resolved == ["see byqa-ref://99"]


async def test_texts_without_tokens_do_not_query_repository():
    connection = FakeConnection()
    repository = FakeReferenceRepository()
    resolver = MarkdownReferenceResolver(
        connection_factory=lambda: connection,
        reference_repository=repository,
    )

    resolved = await resolver.resolve_texts(
        knowledge_base_id=7,
        texts=["plain text", "![img](file.png)"],
    )

    assert resolved == ["plain text", "![img](file.png)"]
    assert repository.calls == []
    assert connection.closed is False


async def test_multiple_texts_resolve_with_one_repository_call_for_all_unique_ids():
    resolved, repository = await _resolve(
        ["a byqa-ref://21 b byqa-ref://22", "again byqa-ref://21"],
        [
            {
                "kid": 21,
                "knowledge_base_id": 7,
                "status": "resolved",
                "original_target": "a.md",
                "target_suffix": "",
                "target_virtual_path": "/docs/a.md",
                "target_is_deleted": False,
            },
            {
                "kid": 22,
                "knowledge_base_id": 7,
                "status": "unresolved",
                "original_target": "b.md#x",
                "target_suffix": "#x",
                "target_virtual_path": None,
                "target_is_deleted": None,
            },
        ],
    )

    assert resolved == ["a /docs/a.md b b.md#x", "again /docs/a.md"]
    assert len(repository.calls) == 1
    assert repository.calls[0]["reference_ids"] == [21, 22]
