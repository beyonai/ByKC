# tests/knowledge_base/unit/test_zip_batch_import_service.py
import io
import zipfile

import pytest

from by_qa.knowledge_base.api.schemas import (
    DeleteKnowledgeItemRequest,
    KnowledgeItemUploadRequest,
)
from by_qa.knowledge_base.services import zip_batch_import_service as zbmod
from by_qa.knowledge_base.services.zip_batch_import_service import ZipBatchImportService


class FakeIngestion:
    """Records upload/delete order with a monotonic sequence number."""

    def __init__(self, *, fail_upload_for: str | None = None):
        self.uploads: list[tuple[int, KnowledgeItemUploadRequest]] = []
        self.deletes: list[tuple[int, str]] = []
        self.files: set[str] = set()
        self._seq = 0
        self.fail_upload_for = (
            "/" + fail_upload_for.strip("/") if fail_upload_for else None
        )

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    async def upload_file(self, request: KnowledgeItemUploadRequest) -> None:
        seq = self._next()
        self.uploads.append((seq, request))
        normalized = "/" + request.file_path.strip("/")
        if self.fail_upload_for is not None and normalized == self.fail_upload_for:
            raise RuntimeError(f"forced upload failure for {normalized}")
        self.files.add(normalized)

    async def file_exists(self, kb_code: str, full_path: str) -> bool:  # pylint: disable=unused-argument
        return full_path in self.files

    async def files_exist(  # pylint: disable=unused-argument
        self, kb_code: str, paths: frozenset[str]
    ) -> set[str]:
        return {p for p in paths if p in self.files}

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


def _make_nonutf8_zip(entries: list[tuple[bytes, bytes]]) -> bytes:
    """Build a zip whose filenames are the given raw bytes with NO UTF-8 flag.

    Mimics zips from Windows Explorer / WinRAR on Chinese Windows: GBK-encoded
    names without flag bit 0x800, which Python's zipfile would otherwise decode
    as CP437 mojibake.
    """
    import struct
    import zlib

    local = b""
    central = b""
    offset = 0
    for fname_bytes, data in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        mod_time, mod_date = 0, 0x21
        lh = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            0,
            mod_time,
            mod_date,
            crc,
            len(data),
            len(data),
            len(fname_bytes),
            0,
        )
        local_body = fname_bytes + data
        local += lh + local_body
        ch = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            0,
            0,
            mod_time,
            mod_date,
            crc,
            len(data),
            len(data),
            len(fname_bytes),
            0,
            0,
            0,
            0,
            0,
            offset,
        )
        central += ch + fname_bytes
        offset += len(lh) + len(local_body)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(entries),
        len(entries),
        len(central),
        len(local),
        0,
    )
    return local + central + eocd


async def test_import_zip_uploads_non_md_first_then_md_without_pre_rewrite():
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
    # ZIP service leaves markdown bytes untouched; ingestion owns tokenization.
    md_req = next(
        req for _, req in ingestion.uploads if req.file_path == "/target/doc.md"
    )
    assert b"![alt](images/x.png)" in md_req.file_content
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


async def test_import_zip_preserves_md_to_md_reference_for_ingestion():
    """A md referencing a sibling md in the same zip is uploaded as-is.

    The ingestion service handles transactional reference tokenization.
    """
    ingestion = FakeIngestion()
    zip_bytes = _make_zip({"b/b.md": b"# b\n", "a.md": b"see [b](b/b.md)\n"})
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    assert result.summary.succeeded == 2
    a_req = next(req for _, req in ingestion.uploads if req.file_path == "/target/a.md")
    assert b"see [b](b/b.md)" in a_req.file_content


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


async def test_import_zip_duplicate_paths_recorded_as_failure():
    """Two zip entries resolving to the same KB path: first wins, second is a
    recorded failure with an error mentioning 'duplicate'."""
    ingestion = FakeIngestion()
    zip_bytes = _make_zip({"a.md": b"# a\n", "sub/../a.md": b"# dup\n"})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    result = await svc.import_zip(kb_code="kb1", target_dir="/t", zip_bytes=zip_bytes)
    assert result.summary.total == 2
    assert result.summary.succeeded == 1
    assert result.summary.failed == 1
    # both report the same resolved path
    assert {item.file_path for item in result.data} == {"/t/a.md"}
    dup_items = [item for item in result.data if not item.success]
    assert len(dup_items) == 1
    assert "duplicate" in (dup_items[0].error or "")
    # only one upload actually happened
    assert len(ingestion.uploads) == 1


async def test_import_zip_malformed_md_preserves_existing():
    """A malformed md (invalid UTF-8) must NOT delete the pre-existing file:
    decode happens before delete (H1 reorder)."""
    ingestion = FakeIngestion()
    ingestion.files.add("/t/a.md")  # pre-existing original
    zip_bytes = _make_zip({"a.md": b"\xff\xfe not utf8"})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    result = await svc.import_zip(kb_code="kb1", target_dir="/t", zip_bytes=zip_bytes)
    assert result.summary.total == 1 and result.summary.failed == 1
    item = result.data[0]
    assert item.success is False
    # original preserved: delete was NOT called and file is still present
    assert ingestion.deletes == []
    assert "/t/a.md" in ingestion.files


async def test_import_zip_keeps_reference_to_failed_phase1_for_ingestion():
    """A non-md failure does not change the markdown upload bytes."""
    ingestion = FakeIngestion(fail_upload_for="/t/images/x.png")
    md = "# t\n![alt](images/x.png)\n"
    zip_bytes = _make_zip({"images/x.png": b"\x89PNG fake", "doc.md": md.encode()})
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    await svc.import_zip(kb_code="kb1", target_dir="/t", zip_bytes=zip_bytes)
    # the png failed; the md uploaded with its original reference intact
    md_uploads = [req for _, req in ingestion.uploads if req.file_path == "/t/doc.md"]
    assert len(md_uploads) == 1
    assert b"![alt](images/x.png)" in md_uploads[0].file_content
    assert b"![alt](/t/images/x.png)" not in md_uploads[0].file_content


async def test_import_zip_rejects_oversized_entry(monkeypatch: pytest.MonkeyPatch):
    """The per-entry cap rejects a single entry whose ACTUAL decompressed bytes
    exceed the limit (not the spoofable header-declared file_size)."""
    ingestion = FakeIngestion()
    # Lower the per-entry cap to a small value so the test is fast and does not
    # allocate megabytes. Restore is automatic via monkeypatch.
    monkeypatch.setattr(zbmod, "_MAX_ENTRY_UNCOMPRESSED", 256)
    # One entry whose decompressed content (1000 bytes) exceeds the 256 cap.
    zip_bytes = _make_zip({"big.md": b"x" * 1000})
    svc = ZipBatchImportService(ingestion_service=ingestion)
    with pytest.raises(ValueError, match="zip too large"):
        await svc.import_zip(kb_code="kb1", target_dir="/t", zip_bytes=zip_bytes)


async def test_import_zip_decodes_gbk_chinese_filenames():
    """Zips with GBK names and no UTF-8 flag must be stored under the real name."""
    ingestion = FakeIngestion()
    entries = [
        ("中文文档.md", b"# title\n"),
        ("图片/中文图.png", b"\x89PNG fake"),
    ]
    zip_bytes = _make_nonutf8_zip([(n.encode("gbk"), d) for n, d in entries])
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    uploaded = {req.file_path for _, req in ingestion.uploads}
    assert "/target/中文文档.md" in uploaded
    assert "/target/图片/中文图.png" in uploaded
    assert all(item.success for item in result.data)
    reported = {item.file_path for item in result.data}
    assert "/target/中文文档.md" in reported
    assert "/target/图片/中文图.png" in reported


async def test_import_zip_keeps_utf8_flagged_chinese_filenames():
    """Zips written by zipfile (UTF-8 flag set) must stay correct after the fix."""
    ingestion = FakeIngestion()
    md = "# t\n![alt](图片/中文图.png)\n"
    zip_bytes = _make_zip(
        {"图片/中文图.png": b"\x89PNG fake", "中文文档.md": md.encode()}
    )
    svc = ZipBatchImportService(ingestion_service=ingestion, max_concurrency=2)
    result = await svc.import_zip(
        kb_code="kb1", target_dir="/target", zip_bytes=zip_bytes
    )
    uploaded = {req.file_path for _, req in ingestion.uploads}
    assert "/target/图片/中文图.png" in uploaded
    assert "/target/中文文档.md" in uploaded
    # md reference is preserved using the real decoded sibling path
    md_req = next(
        req for _, req in ingestion.uploads if req.file_path == "/target/中文文档.md"
    )
    assert "![alt](图片/中文图.png)".encode() in md_req.file_content
    assert all(item.success for item in result.data)


async def test_decode_zip_name_helper_handles_ascii_and_gbk():
    info_ascii = zipfile.ZipInfo("plain.txt")
    assert zbmod._decode_zip_name(info_ascii) == "plain.txt"
    info_gbk = zipfile.ZipInfo("中文.txt".encode("gbk").decode("cp437"))
    info_gbk.flag_bits = 0  # no UTF-8 flag
    assert zbmod._decode_zip_name(info_gbk) == "中文.txt"
