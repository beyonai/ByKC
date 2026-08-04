import sys
from pathlib import Path

import pytest

from by_qa.knowledge_base.infrastructure.storage import StorageLocation
from by_qa.knowledge_base.services.presentation_preview_service import (
    PresentationPreviewService,
    build_presentation_preview_location,
)


class FakeStorageProvider:
    def __init__(self, key: str):
        self.key = key

    def build_markdown_location(self, **kwargs):
        return StorageLocation(namespace="kb", key=self.key)


def test_build_preview_location_for_id_and_path_bound_storage():
    id_bound = build_presentation_preview_location(
        FakeStorageProvider("kb/7/fs-entry/71/markdown.md"),
        kb_code="7",
        knowledge_base_id=7,
        fs_entry_id=71,
        file_path="slides/demo.pptx",
    )
    path_bound = build_presentation_preview_location(
        FakeStorageProvider("agent_data/raw/markdown/slides/demo.pptx.md"),
        kb_code="7",
        knowledge_base_id=7,
        fs_entry_id=71,
        file_path="slides/demo.pptx",
    )

    assert id_bound.key == "kb/7/fs-entry/71/preview.pdf"
    assert path_bound.key == "agent_data/raw/markdown/slides/demo.pptx.preview.pdf"


@pytest.mark.asyncio
async def test_convert_pptx_to_pdf_returns_generated_bytes(monkeypatch):
    class FakeProcess:
        returncode = 0

        def __init__(self, output_path: Path):
            self.output_path = output_path

        async def communicate(self):
            self.output_path.write_bytes(b"%PDF-1.7\npreview")
            return b"converted", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        outdir = Path(args[args.index("--outdir") + 1])
        return FakeProcess(outdir / "presentation.pdf")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    service = PresentationPreviewService(binary=sys.executable, timeout_seconds=5)

    result = await service.convert_pptx_to_pdf(
        b"pptx-bytes", filename="demo.pptx", build_task_id=91
    )

    assert result == b"%PDF-1.7\npreview"
