from typing import Any

from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)


class FakeReferenceRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def create_reference(self, cursor: Any, **kwargs: Any) -> dict[str, Any]:
        row = {"kid": len(self.rows) + 1, **kwargs}
        self.rows.append(row)
        return row


class FakeFsEntryRepository:
    def __init__(
        self,
        *,
        files: dict[str, int] | None = None,
        directories: set[str] | None = None,
    ) -> None:
        self.files = files or {}
        self.directories = directories or set()

    async def get_file_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        if full_path not in self.files:
            return None
        return {
            "kid": self.files[full_path],
            "knowledge_base_id": knowledge_base_id,
            "virtual_path": full_path,
            "entry_type": "FILE",
        }

    async def get_file_reference_target_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        return await self.get_file_by_path(
            cursor,
            knowledge_base_id=knowledge_base_id,
            full_path=full_path,
        )

    async def get_directory_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        if full_path not in self.directories:
            return None
        return {
            "kid": 900,
            "knowledge_base_id": knowledge_base_id,
            "virtual_path": full_path,
            "entry_type": "DIRECTORY",
        }


async def _exists(found: set[str]):
    async def _check(kb_code: str, paths: frozenset[str]) -> frozenset[str]:
        del kb_code
        return frozenset(path for path in paths if path in found)

    return _check


async def _rewrite(
    text: str,
    *,
    source_dir: str = "/docs/p",
    files: dict[str, int] | None = None,
    directories: set[str] | None = None,
) -> tuple[str, FakeReferenceRepository]:
    reference_repository = FakeReferenceRepository()
    fs_entry_repository = FakeFsEntryRepository(files=files, directories=directories)
    out = await MarkdownReferenceRewriter().rewrite(
        text,
        source_dir=source_dir,
        knowledge_base_id=7,
        source_fs_entry_id=42,
        cursor=object(),
        reference_repository=reference_repository,
        fs_entry_repository=fs_entry_repository,
    )
    return out, reference_repository


async def test_existing_file_target_creates_resolved_reference_token():
    out, reference_repository = await _rewrite(
        "see ![alt](images/x.png) here",
        files={"/docs/p/images/x.png": 123},
    )

    assert out == "see ![alt](byqa-ref://1) here"
    assert reference_repository.rows == [
        {
            "kid": 1,
            "knowledge_base_id": 7,
            "source_fs_entry_id": 42,
            "target_fs_entry_id": 123,
            "original_target": "images/x.png",
            "target_path": None,
            "target_suffix": "",
            "target_kind": "FILE",
            "status": "resolved",
        }
    ]


async def test_missing_file_target_creates_unresolved_reference_token():
    out, reference_repository = await _rewrite("![a](missing.png)")

    assert out == "![a](byqa-ref://1)"
    assert reference_repository.rows == [
        {
            "kid": 1,
            "knowledge_base_id": 7,
            "source_fs_entry_id": 42,
            "target_fs_entry_id": None,
            "original_target": "missing.png",
            "target_path": "/docs/p/missing.png",
            "target_suffix": "",
            "target_kind": "FILE",
            "status": "unresolved",
        }
    ]


async def test_ineligible_targets_remain_original_and_create_no_references():
    src = "\n".join(
        [
            "[anchor](#sec)",
            "![empty]()",
            "[external](https://host/x.png)",
            "![escape](../../../x.png)",
            "[dir](assets)",
        ]
    )

    out, reference_repository = await _rewrite(
        src,
        directories={"/docs/p/assets"},
    )

    assert out == src
    assert reference_repository.rows == []


async def test_root_directory_target_remains_original_and_creates_no_reference():
    out, reference_repository = await _rewrite("[root](/)")

    assert out == "[root](/)"
    assert reference_repository.rows == []


async def test_legacy_rewrite_preserves_absolute_path_behavior_for_existing_target():
    rewriter = MarkdownReferenceRewriter(
        exists_check=await _exists({"/docs/p/images/x.png"})
    )

    out = await rewriter.rewrite(
        "see ![alt](images/x.png) here",
        "/docs/p",
        "kb1",
    )

    assert out == "see ![alt](/docs/p/images/x.png) here"


async def test_legacy_rewrite_leaves_missing_target_original():
    rewriter = MarkdownReferenceRewriter(exists_check=await _exists(set()))

    out = await rewriter.rewrite(
        "see ![alt](missing.png) here",
        "/docs/p",
        "kb1",
    )

    assert out == "see ![alt](missing.png) here"


async def test_target_suffix_stored_separately_and_original_target_preserved():
    out, reference_repository = await _rewrite(
        "go [doc](a.md?download=1#sec) now",
        files={"/docs/p/a.md": 321},
    )

    assert out == "go [doc](byqa-ref://1) now"
    assert reference_repository.rows[0]["original_target"] == "a.md?download=1#sec"
    assert reference_repository.rows[0]["target_suffix"] == "?download=1#sec"
    assert reference_repository.rows[0]["target_fs_entry_id"] == 321
    assert reference_repository.rows[0]["target_path"] is None
    assert reference_repository.rows[0]["target_kind"] == "FILE"
    assert reference_repository.rows[0]["status"] == "resolved"


async def test_original_target_preserves_surrounding_whitespace_with_suffix():
    out, reference_repository = await _rewrite(
        "prefix [doc]( a.md#sec ) suffix",
        files={"/docs/p/a.md": 321},
    )

    assert out == "prefix [doc](byqa-ref://1) suffix"
    assert reference_repository.rows[0]["original_target"] == " a.md#sec "
    assert reference_repository.rows[0]["target_suffix"] == "#sec"
    assert reference_repository.rows[0]["target_fs_entry_id"] == 321
    assert reference_repository.rows[0]["target_path"] is None


async def test_percent_decoded_target_path_preserves_original_target():
    out, reference_repository = await _rewrite(
        "![a](b%20c.png)",
        files={"/docs/p/b c.png": 55},
    )

    assert out == "![a](byqa-ref://1)"
    assert reference_repository.rows[0]["original_target"] == "b%20c.png"
    assert reference_repository.rows[0]["target_fs_entry_id"] == 55


async def test_skips_when_reference_count_exceeds_cap():
    src = "".join(
        f"![a](x{i}.png)\n" for i in range(MarkdownReferenceRewriter.MAX_REFERENCES + 1)
    )

    out, reference_repository = await _rewrite(src)

    assert out == src
    assert reference_repository.rows == []
