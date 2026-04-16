"""Periodic cleanup for fetched-file local cache entries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.services.cache_file_lock import acquire_cache_file_lock


@dataclass
class KnowledgeFetchCacheCleanupService:
    """Delete expired local cache files tracked by the cache index table."""

    connection_factory: Callable[[], Any]
    knowledge_fetch_cache_repository: Any
    cleanup_interval_seconds: int = 300
    cleanup_batch_size: int = 100

    def __post_init__(self) -> None:
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background cleanup task once."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(), name="kb-fetch-cache-cleanup"
        )

    async def stop(self) -> None:
        """Stop the background cleanup task."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run_cleanup_cycle(self) -> None:
        """Run one cleanup sweep."""
        connection = await self.connection_factory()
        deleted_count = 0
        failed_count = 0
        candidate_count = 0
        try:
            cursor = connection.cursor()
            await self.knowledge_fetch_cache_repository.mark_expired_ready_entries_as_evicting(
                cursor,
                batch_size=self.cleanup_batch_size,
            )
            candidates = (
                await self.knowledge_fetch_cache_repository.list_cleanup_candidates(
                    cursor,
                    batch_size=self.cleanup_batch_size,
                )
            )
            candidate_count = len(candidates)
            for candidate in candidates:
                cache_file_path = Path(str(candidate["cache_file_path"]))
                with acquire_cache_file_lock(cache_file_path):
                    try:
                        if cache_file_path.exists():
                            cache_file_path.unlink()
                        await self.knowledge_fetch_cache_repository.delete_cache_entry(
                            cursor,
                            cache_entry_id=int(candidate["kid"]),
                        )
                        deleted_count += 1
                    except Exception as exc:
                        await self.knowledge_fetch_cache_repository.mark_cache_entry_error(
                            cursor,
                            cache_entry_id=int(candidate["kid"]),
                            error=str(exc),
                        )
                        failed_count += 1
            await connection.commit()
            logger.info(
                "knowledge_fetch_cache_cleanup_service cycle finished: candidate_count=%s, deleted_count=%s, failed_count=%s",
                candidate_count,
                deleted_count,
                failed_count,
            )
        except Exception:
            await connection.rollback()
            logger.warning("knowledge_fetch_cache_cleanup_service cycle rolled back")
            raise
        finally:
            await connection.close()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_cleanup_cycle()
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                logger.warning(
                    "knowledge_fetch_cache_cleanup_service cycle failed: error=%s",
                    exc,
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.cleanup_interval_seconds
                )
                break
            except asyncio.TimeoutError:
                pass
