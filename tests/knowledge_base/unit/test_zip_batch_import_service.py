# tests/knowledge_base/unit/test_zip_batch_import_service.py
import io
import zipfile

import pytest

from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    KnowledgeItemUploadRequest,
)
from by_qa.knowledge_base.services.zip_batch_import_service import ZipBatchImportService


class FakeIngestion:
    """Records upload/delete order with a monotonic sequence number."""

    def __init__(self):
        self.uploads: list[tuple[int, KnowledgeItemUploadRequest]] = []
        self.deletes: list[tuple[int, str]] = []
        self.files: set[str] = set()
        self._seq = 0

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    async def upload_file(self, request: KnowledgeItemUploadRequest) -> None:
        seq = self._next()
        self.uploads.append((seq, request))
        self.files.add("/" + request.file_path.strip("/"))

    async def file_exists(self, kb_code: str, full_path: str) -> bool:  # pylint: disable=unused-argument
        return full_path in self.files

    async def delete_knowledge_item(self, request: DeleteKnowledgeItemRequest) -> None:
        seq = self._next()
        self.deletes.append((seq, request.file_path))
        self.files.discard("/" + request.file_path.strip("/"))


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


async def test_import_zip_uploads_non_md_first_then_md_and_rewrites():
    ingestion = FakeIngestion()
    md = "# t\n![alt](images/x.png)\n"
    zip_bytes = _make_zip({"images/x.png": b"\x89PNG fake", "doc.md": md.encode()})
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    seq_by_path = {req.file_path: seq for seq, req in ingestion.uploads}
    assert set(seq_by_path) == {"/target/images/x.png", "/target/doc.md"}
    # phase barrier: every non-md uploaded before the md
    assert seq_by_path["/target/images/x.png"] < seq_by_path["/target/doc.md"]
    # md reference rewritten to KB-absolute path
    md_req = next(
        req for _, req in ingestion.uploads if req.file_path == "/target/doc.md"
    )
    assert b"![alt](/target/images/x.png)" in md_req.file_content
    assert result.summary.total == 2 and result.summary.succeeded == 2


async def test_import_zip_phase_barrier_with_many_non_md():
    ingestion = FakeIngestion()
    entries = {f"a/i{i}.png": b"x" for i in range(5)}
    entries["doc.md"] = b"![a](a/i0.png)\n"
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=3)
    await svc.import_zip(kb_code="kb1", target_dir="/t", zip_bytes=_make_zip(entries))
    non_md_seqs = [
        seq for seq, req in ingestion.uploads if not req.file_path.endswith(".md")
    ]
    md_seqs = [seq for seq, req in ingestion.uploads if req.file_path.endswith(".md")]
    assert max(non_md_seqs) < min(md_seqs)


async def test_import_zip_deletes_existing_file_before_upload():
    ingestion = FakeIngestion()
    ingestion.files.add("/target/real.md")  # pre-existing
    zip_bytes = _make_zip({"real.md": b"# new\n"})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    assert len(ingestion.deletes) == 1
    del_seq, del_path = ingestion.deletes[0]
    up_seq, up_req = ingestion.uploads[0]
    assert del_path == "/target/real.md"
    assert del_seq < up_seq
    assert up_req.file_path == "/target/real.md"
    assert result.summary.succeeded == 1


async def test_import_zip_rewrites_md_to_md_reference_within_batch():
    """A md referencing a sibling md in the same zip is rewritten even though
    the target md is uploaded concurrently (batch-aware existence check)."""
    ingestion = FakeIngestion()
    zip_bytes = _make_zip({"b/b.md": b"# b\n", "a.md": b"see [b](b/b.md)\n"})
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    assert result.summary.succeeded == 2
    a_req = next(req for _, req in ingestion.uploads if req.file_path == "/target/a.md")
    assert b"see [b](/target/b/b.md)" in a_req.file_content


async def test_import_zip_skips_unsafe_path_and_records_failure():
    ingestion = FakeIngestion()
    # ../escape.md resolves to /escape.md, outside the /target dir -> unsafe
    zip_bytes = _make_zip({"../escape.md": b"x"})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    assert result.summary.total == 1 and result.summary.failed == 1
    assert ingestion.uploads == []
    item = result.data[0]
    assert item.success is False
    assert item.error  # non-empty reason


async def test_import_zip_skips_macosx_and_directories():
    ingestion = FakeIngestion()
    zip_bytes = _make_zip({"__MACOSX/._doc.md": b"x", "sub/": b"", "real.md": b"# h\n"})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    assert [req.file_path for _, req in ingestion.uploads] == ["/target/real.md"]
    assert result.summary.total == 1


async def test_import_zip_rejects_non_zip():
    ingestion = FakeIngestion()
    svc = ZipBatchImportService(ingestion_service=ingestion)
    with pytest.raises(Exception):
        await svc.import_zip(
            kb_code="kb1", target_dir="/target", zip_bytes=b"not a zip"
        )
