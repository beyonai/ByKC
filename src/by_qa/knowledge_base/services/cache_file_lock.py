"""Helpers for per-cache-file process locks."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def lock_path_for_cache_file(cached_file_path: Path) -> Path:
    """Return the lock-file path for one cached file."""
    return cached_file_path.parent / f"{cached_file_path.name}.lock"


@contextmanager
def acquire_cache_file_lock(cached_file_path: Path) -> Iterator[None]:
    """Take an exclusive flock for one cached file."""
    lock_path = lock_path_for_cache_file(cached_file_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
