"""Best-effort PowerPoint to PDF preview generation."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from by_qa.core import logger
from by_qa.knowledge_base.infrastructure.storage import StorageLocation


def build_presentation_preview_location(
    storage_provider: Any,
    *,
    kb_code: str,
    knowledge_base_id: int,
    fs_entry_id: int,
    file_path: str,
) -> StorageLocation:
    """Derive a stable PDF sidecar location from the Markdown location."""
    markdown_location = storage_provider.build_markdown_location(
        kb_code=kb_code,
        knowledge_base_id=knowledge_base_id,
        fs_entry_id=fs_entry_id,
        file_path=file_path,
    )
    markdown_key = PurePosixPath(markdown_location.key)
    if markdown_key.name == "markdown.md":
        preview_key = str(markdown_key.with_name("preview.pdf"))
    elif markdown_location.key.endswith(".md"):
        preview_key = f"{markdown_location.key[:-3]}.preview.pdf"
    else:
        preview_key = f"{markdown_location.key}.preview.pdf"
    return StorageLocation(namespace=markdown_location.namespace, key=preview_key)


@dataclass(frozen=True)
class PresentationPreviewService:
    """Convert PPT/PPTX bytes to PDF with an isolated LibreOffice process."""

    binary: str | None = None
    timeout_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> "PresentationPreviewService":
        timeout_value = os.getenv("QA_PPTX_PREVIEW_TIMEOUT_SECONDS", "120").strip()
        try:
            timeout_seconds = max(1.0, float(timeout_value))
        except ValueError:
            timeout_seconds = 120.0
        return cls(
            binary=os.getenv("QA_LIBREOFFICE_BINARY", "").strip() or None,
            timeout_seconds=timeout_seconds,
        )

    def resolve_binary(self) -> str | None:
        candidates = [
            self.binary,
            shutil.which("soffice"),
            shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    async def convert_pptx_to_pdf(
        self,
        content: bytes,
        *,
        filename: str,
        build_task_id: int | None = None,
    ) -> bytes | None:
        """Return PDF bytes, or ``None`` when preview generation is unavailable."""
        binary = self.resolve_binary()
        if binary is None:
            logger.warning(
                "presentation_preview skipped: build_task_id=%s, filename=%s, "
                "reason=libreoffice_not_found",
                build_task_id,
                filename,
            )
            return None

        started = time.perf_counter()
        logger.info(
            "presentation_preview conversion started: build_task_id=%s, "
            "filename=%s, input_bytes=%s, binary=%s",
            build_task_id,
            filename,
            len(content),
            binary,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="byqa-pptx-preview-") as temp_dir:
                workspace = Path(temp_dir)
                source_suffix = PurePosixPath(filename).suffix.lower()
                if source_suffix not in {".ppt", ".pptx"}:
                    source_suffix = ".pptx"
                input_path = workspace / f"presentation{source_suffix}"
                output_path = workspace / "presentation.pdf"
                profile_dir = workspace / "libreoffice-profile"
                input_path.write_bytes(content)
                profile_dir.mkdir()
                process = await asyncio.create_subprocess_exec(
                    binary,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--norestore",
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                    str(workspace),
                    str(input_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(), timeout=self.timeout_seconds
                    )
                except TimeoutError:
                    process.kill()
                    await process.communicate()
                    logger.warning(
                        "presentation_preview conversion failed: build_task_id=%s, "
                        "filename=%s, reason=timeout, timeout_seconds=%s",
                        build_task_id,
                        filename,
                        self.timeout_seconds,
                    )
                    return None

                if process.returncode != 0 or not output_path.is_file():
                    logger.warning(
                        "presentation_preview conversion failed: build_task_id=%s, "
                        "filename=%s, return_code=%s, stdout=%s, stderr=%s",
                        build_task_id,
                        filename,
                        process.returncode,
                        stdout.decode("utf-8", errors="replace").strip()[:1000],
                        stderr.decode("utf-8", errors="replace").strip()[:1000],
                    )
                    return None
                pdf_bytes = output_path.read_bytes()
        except (OSError, asyncio.SubprocessError) as exc:
            logger.warning(
                "presentation_preview conversion failed: build_task_id=%s, "
                "filename=%s, error=%s",
                build_task_id,
                filename,
                exc,
            )
            return None

        logger.info(
            "presentation_preview conversion completed: build_task_id=%s, "
            "filename=%s, output_bytes=%s, elapsed_ms=%.2f",
            build_task_id,
            filename,
            len(pdf_bytes),
            (time.perf_counter() - started) * 1000,
        )
        return pdf_bytes


__all__ = [
    "PresentationPreviewService",
    "build_presentation_preview_location",
]
