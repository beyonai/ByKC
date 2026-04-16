"""Tests for periodic fetched-file cache cleanup."""

from pathlib import Path

from by_qa.knowledge_base.services.knowledge_fetch_cache_cleanup_service import (
    KnowledgeFetchCacheCleanupService,
)


class FakeCursor:
    async def execute(self, *args, **kwargs):
        pass


class FakeConnection:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return FakeCursor()

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def close(self):
        return None


class FakeKnowledgeFetchCacheRepository:
    def __init__(self, candidates=None):
        self.calls = []
        self.candidates = list(candidates or [])

    async def mark_expired_ready_entries_as_evicting(self, cursor, *, batch_size):
        self.calls.append(
            ("mark_expired_ready_entries_as_evicting", {"batch_size": batch_size})
        )

    async def list_cleanup_candidates(self, cursor, *, batch_size):
        self.calls.append(("list_cleanup_candidates", {"batch_size": batch_size}))
        return list(self.candidates)

    async def delete_cache_entry(self, cursor, *, cache_entry_id):
        self.calls.append(("delete_cache_entry", {"cache_entry_id": cache_entry_id}))

    async def mark_cache_entry_error(self, cursor, *, cache_entry_id, error):
        self.calls.append(
            (
                "mark_cache_entry_error",
                {"cache_entry_id": cache_entry_id, "error": error},
            )
        )


async def test_cleanup_cycle_deletes_expired_cache_file_and_row(tmp_path):
    """Cleanup should evict expired rows and remove their local cache files."""
    cache_file = tmp_path / "kb_cache" / "Integration KB" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("hello\n", encoding="utf-8")
    connection = FakeConnection()
    repository = FakeKnowledgeFetchCacheRepository(
        candidates=[{"kid": 301, "cache_file_path": str(cache_file)}]
    )

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
        cleanup_batch_size=100,
    )

    await service.run_cleanup_cycle()

    assert not cache_file.exists()
    assert repository.calls == [
        ("mark_expired_ready_entries_as_evicting", {"batch_size": 100}),
        ("list_cleanup_candidates", {"batch_size": 100}),
        ("delete_cache_entry", {"cache_entry_id": 301}),
    ]
    assert connection.committed == 1


async def test_cleanup_cycle_deletes_row_when_local_file_is_already_missing(tmp_path):
    """Missing files should still clear the corresponding cache index row."""
    cache_file = tmp_path / "kb_cache" / "Integration KB" / "dir1" / "missing.md"
    connection = FakeConnection()
    repository = FakeKnowledgeFetchCacheRepository(
        candidates=[{"kid": 302, "cache_file_path": str(cache_file)}]
    )

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
        cleanup_batch_size=100,
    )

    await service.run_cleanup_cycle()

    assert repository.calls[-1] == ("delete_cache_entry", {"cache_entry_id": 302})


async def test_cleanup_cycle_marks_error_when_file_cannot_be_deleted(
    tmp_path, monkeypatch
):
    """Filesystem deletion failures should move the row into ERROR for the next cycle."""
    cache_file = tmp_path / "kb_cache" / "Integration KB" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("hello\n", encoding="utf-8")
    connection = FakeConnection()
    repository = FakeKnowledgeFetchCacheRepository(
        candidates=[{"kid": 303, "cache_file_path": str(cache_file)}]
    )

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
        cleanup_batch_size=100,
    )

    monkeypatch.setattr(
        Path, "unlink", lambda self: (_ for _ in ()).throw(OSError("boom"))
    )

    await service.run_cleanup_cycle()

    assert repository.calls[-1][0] == "mark_cache_entry_error"
    assert repository.calls[-1][1]["cache_entry_id"] == 303


async def test_cleanup_cycle_logs_cleanup_summary(monkeypatch, tmp_path):
    """Cleanup should log how many cache files were deleted and how many failed."""
    cache_file = tmp_path / "kb_cache" / "Integration KB" / "dir1" / "doc.md"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("hello\n", encoding="utf-8")
    connection = FakeConnection()
    repository = FakeKnowledgeFetchCacheRepository(
        candidates=[{"kid": 301, "cache_file_path": str(cache_file)}]
    )

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
        cleanup_batch_size=100,
    )
    info_messages: list[str] = []

    monkeypatch.setattr(
        "by_qa.knowledge_base.services.knowledge_fetch_cache_cleanup_service.logger.info",
        lambda message, *args, **kwargs: info_messages.append(
            message % args if args else message
        ),
    )

    await service.run_cleanup_cycle()

    assert any("deleted_count=1" in message for message in info_messages)
    assert any("failed_count=0" in message for message in info_messages)


async def test_cleanup_service_start_and_stop_lifecycle():
    """start/stop should create and cancel the asyncio task."""
    repository = FakeKnowledgeFetchCacheRepository()
    connection = FakeConnection()

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
    )
    await service.start()
    assert service._task is not None
    assert not service._task.done()
    await service.stop()
    assert service._task is None


async def test_cleanup_service_start_is_idempotent():
    """Calling start twice should not create a second task."""
    repository = FakeKnowledgeFetchCacheRepository()
    connection = FakeConnection()

    async def connection_factory():
        return connection

    service = KnowledgeFetchCacheCleanupService(
        connection_factory=connection_factory,
        knowledge_fetch_cache_repository=repository,
        cleanup_interval_seconds=300,
    )
    await service.start()
    task1 = service._task
    await service.start()
    task2 = service._task
    assert task1 is task2
    await service.stop()
