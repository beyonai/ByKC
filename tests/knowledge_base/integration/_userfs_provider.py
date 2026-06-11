"""UserFS — local filesystem-backed KnowledgeStorageProvider for integration tests.

Paths:  original = {root}/{kb_code}/raw/{file_path}
        markdown = {root}/{kb_code}/md/{file_path}.md

storage_path_bound_to_logical_path = True.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from by_qa.knowledge_base.infrastructure.storage import (
    StorageConfigurationError,
    StorageError,
    StorageLocation,
    StorageNotFoundError,
    StorageOperationError,
    StoredObject,
)


@dataclass
class UserFSProvider:
    root: Path
    provider_name: str = "userfs"
    storage_path_bound_to_logical_path: bool = True

    # -- lifecycle -------------------------------------------------------

    async def ensure_ready(self) -> None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageConfigurationError(
                f"cannot create storage root {self.root}: {exc}"
            ) from exc

    # -- location builders -----------------------------------------------

    def build_original_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path, mime_type
    ):
        _ = knowledge_base_id, fs_entry_id, mime_type
        clean = self._clean(file_path)
        return StorageLocation(namespace=str(self.root), key=f"{kb_code}/raw{clean}")

    def build_markdown_location(
        self, *, kb_code, knowledge_base_id, fs_entry_id, file_path
    ):
        _ = knowledge_base_id, fs_entry_id
        clean = self._clean(file_path)
        return StorageLocation(namespace=str(self.root), key=f"{kb_code}/md{clean}.md")

    # -- i/o -------------------------------------------------------------

    async def write(self, location, content, *, content_type):
        target = self._resolve(location)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as exc:
            raise StorageOperationError(str(exc)) from exc
        return StoredObject(
            location=location, size=len(content), content_type=content_type
        )

    async def read(self, location):
        target = self._resolve(location)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise StorageNotFoundError(str(exc)) from exc
        except OSError as exc:
            raise StorageOperationError(str(exc)) from exc

    async def delete(self, location):
        target = self._resolve(location)
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageOperationError(str(exc)) from exc

    async def delete_quietly(self, location):
        try:
            await self.delete(location)
        except StorageError:
            return

    async def move(self, source, target, *, overwrite=False):
        src = self._resolve(source)
        dst = self._resolve(target)
        if not src.exists():
            raise StorageNotFoundError(f"source missing: {src}")
        if dst.exists() and not overwrite:
            raise StorageOperationError(f"target exists: {dst}")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        except OSError as exc:
            raise StorageOperationError(str(exc)) from exc

    # -- helpers ---------------------------------------------------------

    def _resolve(self, location):
        return Path(location.namespace) / location.key

    @staticmethod
    def _clean(file_path):
        p = file_path.strip()
        if not p.startswith("/"):
            p = "/" + p
        return str(PurePosixPath(p))
