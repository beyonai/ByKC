from typing import Any

import pytest

from by_qa.knowledge_base.services.markdown_reference_rewriter import (
    MarkdownReferenceRewriter,
)


class FakeReferenceRepository:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def upsert_markdown_relation(
        self, cursor: Any, **kwargs: Any
    ) -> dict[str, Any]:
        del cursor
        target_path = kwargs["normalized_target_path"]
        target_fs_entry_id = kwargs["target_fs_entry_id"]
        row = {
            "kid": len(self.rows) + 1,
            "knowledge_base_id": kwargs["knowledge_base_id"],
            "source_fs_entry_id": kwargs["source_fs_entry_id"],
            "target_fs_entry_id": target_fs_entry_id,
            "relation_code": "MENTIONS",
            "original_target": target_path,
            "target_path": None if target_fs_entry_id is not None else target_path,
            "target_suffix": "",
            "target_kind": "FILE",
            "status": "resolved" if target_fs_entry_id is not None else "unresolved",
            "discovered_by": "MARKDOWN_PARSER",
            "producer_run_id": kwargs["producer_run_id"],
            "evidence_fingerprint": "relation-fingerprint",
            "source_heading_path": None,
            "start_line": None,
            "end_line": None,
            "start_offset": None,
            "end_offset": None,
            "target_locator_type": "KB_PATH",
            "target_locator_value": target_path,
        }
        self.rows.append(row)
        return row

    async def list_by_reference_ids(
        self, cursor: Any, *, reference_ids: list[int]
    ) -> list[dict[str, Any]]:
        del cursor
        return [row for row in self.rows if row["kid"] in reference_ids]


class FakeFsEntryRepository:
    def __init__(
        self,
        *,
        files: dict[str, int] | None = None,
        directories: set[str] | None = None,
    ) -> None:
        self.files = files or {}
        self.directories = directories or set()
        self.file_reference_calls: list[str] = []
        self.directory_calls: list[str] = []

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
        self.file_reference_calls.append(full_path)
        return await self.get_file_by_path(
            cursor,
            knowledge_base_id=knowledge_base_id,
            full_path=full_path,
        )

    async def get_directory_by_path(
        self, cursor: Any, *, knowledge_base_id: int, full_path: str
    ) -> dict[str, Any] | None:
        self.directory_calls.append(full_path)
        if full_path not in self.directories:
            return None
        return {
            "kid": 900,
            "knowledge_base_id": knowledge_base_id,
            "virtual_path": full_path,
            "entry_type": "DIRECTORY",
        }


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
        producer_run_id="markdown-run-1",
    )
    return out, reference_repository


async def test_existing_file_target_creates_resolved_reference_token():
    out, reference_repository = await _rewrite(
        "see ![alt](images/x.png) here",
        files={"/docs/p/images/x.png": 123},
    )

    assert out == "see ![alt](byqa-ref://1) here"
    assertion = reference_repository.rows[0]
    assert assertion["target_fs_entry_id"] == 123
    assert assertion["original_target"] == "/docs/p/images/x.png"
    assert assertion["relation_code"] == "MENTIONS"
    assert assertion["discovered_by"] == "MARKDOWN_PARSER"
    assert assertion["producer_run_id"] == "markdown-run-1"
    assert assertion["target_locator_type"] == "KB_PATH"
    assert assertion["target_locator_value"] == "/docs/p/images/x.png"
    assert assertion["source_heading_path"] is None
    assert assertion["start_line"] is None
    assert assertion["end_line"] is None
    assert assertion["start_offset"] is None
    assert assertion["end_offset"] is None


async def test_missing_file_target_creates_unresolved_reference_token():
    out, reference_repository = await _rewrite("![a](missing.png)")

    assert out == "![a](byqa-ref://1)"
    assertion = reference_repository.rows[0]
    assert assertion["target_fs_entry_id"] is None
    assert assertion["original_target"] == "/docs/p/missing.png"
    assert assertion["target_path"] == "/docs/p/missing.png"
    assert assertion["status"] == "unresolved"
    assert assertion["target_locator_type"] == "KB_PATH"
    assert assertion["target_locator_value"] == "/docs/p/missing.png"


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


async def test_protocol_relative_external_url_remains_original_and_creates_no_reference():
    src = "![cdn](//cdn.example.com/image.png)"

    out, reference_repository = await _rewrite(src)

    assert out == src
    assert reference_repository.rows == []


async def test_target_suffix_is_kept_inline_and_not_stored_on_relation():
    out, reference_repository = await _rewrite(
        "go [doc](a.md?download=1#sec) now",
        files={"/docs/p/a.md": 321},
    )

    assert out == "go [doc](byqa-ref://1?download=1#sec) now"
    assert reference_repository.rows[0]["original_target"] == "/docs/p/a.md"
    assert reference_repository.rows[0]["target_suffix"] == ""
    assert reference_repository.rows[0]["target_fs_entry_id"] == 321
    assert reference_repository.rows[0]["target_path"] is None
    assert reference_repository.rows[0]["target_kind"] == "FILE"
    assert reference_repository.rows[0]["status"] == "resolved"


async def test_rewrite_drops_surrounding_target_whitespace_but_keeps_suffix():
    out, reference_repository = await _rewrite(
        "prefix [doc]( a.md#sec ) suffix",
        files={"/docs/p/a.md": 321},
    )

    assert out == "prefix [doc](byqa-ref://1#sec) suffix"
    assert reference_repository.rows[0]["original_target"] == "/docs/p/a.md"
    assert reference_repository.rows[0]["target_suffix"] == ""
    assert reference_repository.rows[0]["target_fs_entry_id"] == 321
    assert reference_repository.rows[0]["target_path"] is None


async def test_percent_decoded_target_path_is_stored_as_canonical_relation_path():
    out, reference_repository = await _rewrite(
        "![a](b%20c.png)",
        files={"/docs/p/b c.png": 55},
    )

    assert out == "![a](byqa-ref://1)"
    assert reference_repository.rows[0]["original_target"] == "/docs/p/b c.png"
    assert reference_repository.rows[0]["target_fs_entry_id"] == 55


async def test_skips_when_reference_count_exceeds_cap():
    src = "".join(
        f"![a](x{i}.png)\n" for i in range(MarkdownReferenceRewriter.MAX_REFERENCES + 1)
    )

    out, reference_repository = await _rewrite(src)

    assert out == src
    assert reference_repository.rows == []


async def test_does_not_record_heading_line_or_offsets_for_markdown_relation():
    out, reference_repository = await _rewrite(
        "# Guide\n\n## Assets\n\nSee [diagram](image.png).\n",
        files={"/docs/p/image.png": 123},
    )

    assert "byqa-ref://1" in out
    assertion = reference_repository.rows[0]
    assert assertion["source_heading_path"] is None
    assert assertion["start_line"] is None
    assert assertion["end_line"] is None
    assert assertion["start_offset"] is None
    assert assertion["end_offset"] is None


async def test_repeated_target_uses_one_relation_and_preserves_each_suffix():
    reference_repository = FakeReferenceRepository()
    fs_entry_repository = FakeFsEntryRepository(files={"/docs/p/a.md": 321})

    out = await MarkdownReferenceRewriter().rewrite(
        "[one](a.md#intro) [two](./a.md?download=1#api) ![img](a.md#diagram)",
        source_dir="/docs/p",
        knowledge_base_id=7,
        source_fs_entry_id=42,
        cursor=object(),
        reference_repository=reference_repository,
        fs_entry_repository=fs_entry_repository,
        producer_run_id="markdown-run-1",
    )

    assert out == (
        "[one](byqa-ref://1#intro) "
        "[two](byqa-ref://1?download=1#api) "
        "![img](byqa-ref://1#diagram)"
    )
    assert len(reference_repository.rows) == 1
    assert reference_repository.rows[0]["original_target"] == "/docs/p/a.md"
    assert fs_entry_repository.file_reference_calls == ["/docs/p/a.md"]
    assert fs_entry_repository.directory_calls == []


async def test_materializes_existing_token_to_current_path_before_deletion():
    repository = FakeReferenceRepository()
    repository.rows = [
        {
            "kid": 41,
            "target_virtual_path": "/moved/image.png",
            "target_path": None,
            "target_suffix": "#preview",
            "original_target": "old.png#preview",
            "target_locator_type": "KB_PATH",
            "target_locator_value": "/docs/old.png",
        },
        {
            "kid": 42,
            "target_virtual_path": None,
            "target_path": None,
            "target_suffix": "",
            "original_target": "gone.png",
            "target_locator_type": "KB_PATH",
            "target_locator_value": "/docs/gone.png",
        },
        {
            "kid": 43,
            "target_virtual_path": "/moved/doc.md",
            "target_path": None,
            "target_suffix": "",
            "original_target": "/docs/doc.md",
            "target_locator_type": "KB_PATH",
            "target_locator_value": "/docs/doc.md",
        },
    ]

    out = await MarkdownReferenceRewriter().materialize_existing_tokens(
        "![a](byqa-ref://41) [b](byqa-ref://42) [c](byqa-ref://43?download=1#intro)",
        cursor=object(),
        reference_repository=repository,
    )

    assert out == (
        "![a](/moved/image.png#preview) [b](/docs/gone.png) "
        "[c](/moved/doc.md?download=1#intro)"
    )


async def test_rejects_unknown_token_instead_of_persisting_dangling_reference():
    repository = FakeReferenceRepository()

    with pytest.raises(ValueError, match="cannot materialize reference tokens: 999"):
        await MarkdownReferenceRewriter().materialize_existing_tokens(
            "![missing](byqa-ref://999)",
            cursor=object(),
            reference_repository=repository,
        )
