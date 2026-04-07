"""Periodic cleanup for fetched-file local cache entries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
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
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start the background cleanup thread once."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(
            target=self._run_loop, name="kb-fetch-cache-cleanup", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background cleanup thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.cleanup_interval_seconds))
            self._thread = None

    def run_cleanup_cycle(self) -> None:
        """Run one cleanup sweep."""
        connection = self.connection_factory()
        deleted_count = 0
        failed_count = 0
        candidate_count = 0
        try:
            cursor = connection.cursor()
            self.knowledge_fetch_cache_repository.mark_expired_ready_entries_as_evicting(
                cursor,
                batch_size=self.cleanup_batch_size,
            )
            candidates = self.knowledge_fetch_cache_repository.list_cleanup_candidates(
                cursor,
                batch_size=self.cleanup_batch_size,
            )
            candidate_count = len(candidates)
            for candidate in candidates:
                cache_file_path = Path(str(candidate["cache_file_path"]))
                with acquire_cache_file_lock(cache_file_path):
                    try:
                        if cache_file_path.exists():
                            cache_file_path.unlink()
                        self.knowledge_fetch_cache_repository.delete_cache_entry(
                            cursor,
                            cache_entry_id=int(candidate["kid"]),
                        )
                        deleted_count += 1
                    except Exception as exc:
                        self.knowledge_fetch_cache_repository.mark_cache_entry_error(
                            cursor,
                            cache_entry_id=int(candidate["kid"]),
                            error=str(exc),
                        )
                        failed_count += 1
            connection.commit()
            logger.info(
                "knowledge_fetch_cache_cleanup_service cycle finished: candidate_count=%s, deleted_count=%s, failed_count=%s",
                candidate_count,
                deleted_count,
                failed_count,
            )
        except Exception:
            connection.rollback()
            logger.warning("knowledge_fetch_cache_cleanup_service cycle rolled back")
            raise
        finally:
            connection.close()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_cleanup_cycle()
            except Exception as exc:  # pragma: no cover - defensive runtime logging
                logger.warning(
                    "knowledge_fetch_cache_cleanup_service cycle failed: error=%s",
                    exc,
                )
            if self._stop_event.wait(self.cleanup_interval_seconds):
                break
