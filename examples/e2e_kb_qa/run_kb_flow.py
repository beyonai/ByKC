"""Build, import, and browse knowledge-base files from one directory."""

from __future__ import annotations

import argparse
import time

import httpx
from common import (
    DEFAULT_FILE_VERSION,
    DEFAULT_SOURCE_CODE,
    example_kb_identity,
    infer_build_file_type,
    list_supported_input_files,
    normalized_base_url,
    post_api,
    pretty_print,
    read_file_base64,
    resolve_input_directory,
    runtime_dir,
    wait_for_health,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the packaged KB flow example."""
    parser = argparse.ArgumentParser(
        description="Run knowledge build, KB import, list_dir, and glob in one flow."
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help="Directory used for generated sample files and saved example outputs.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base URL for the running by-qa service.",
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="Directory to import. All supported files in this directory will be imported.",
    )
    return parser


def main() -> None:
    """Execute the full build -> import -> browse -> search example chain."""
    args = build_parser().parse_args()
    runtime_dir(args.runtime_dir)
    base_url = normalized_base_url(args.base_url)
    kb_code, kb_name = example_kb_identity()
    input_dir = resolve_input_directory(args.dir)
    input_files = list_supported_input_files(input_dir)

    health = wait_for_health(base_url)
    pretty_print("Health Check", health)

    with httpx.Client(timeout=120.0) as client:
        create_result = post_api(
            client,
            base_url=base_url,
            path="/api/v1/knowledge-bases/create",
            payload={
                "kb_code": kb_code,
                "kb_name": kb_name,
                "kb_description": "用于演示 knowledge_build、knowledge_base 与即时问答的端到端样例。",
                "status": "ACTIVE",
                "metadata": {"scenario": "packaged-e2e-example"},
            },
            allowed_error_codes={"KB_CODE_CONFLICT", "KB_CODE_SOFT_DELETED_CONFLICT"},
        )
        pretty_print("Create Knowledge Base", create_result)

        imported_files: list[dict[str, str | int]] = []
        timestamp = int(time.time())
        for index, input_path in enumerate(input_files, start=1):
            file_code = f"{timestamp}-{index}"
            file_name = input_path.name
            file_path = f"{timestamp}-{index}-{file_name}"
            file_content = read_file_base64(input_path)
            file_type = infer_build_file_type(input_path)

            build_result = post_api(
                client,
                base_url=base_url,
                path="/api/v1/file-to-markdown-index",
                payload={"content": file_content, "type": file_type},
            )
            pretty_print(
                f"Knowledge Build Output: {file_name}",
                {
                    "md_preview": build_result["md_content"][:400],
                    "chunk_count": len(build_result["chunks"]),
                },
            )

            import_result = post_api(
                client,
                base_url=base_url,
                path="/api/v1/knowledge-items/import",
                payload={
                    "kb_code": kb_code,
                    "file_code": file_code,
                    "file_path": file_path,
                    "file_description": f"端到端示例导入文件: {file_name}",
                    "file_content": file_content,
                    "version": DEFAULT_FILE_VERSION,
                    "source_code": DEFAULT_SOURCE_CODE,
                    "status": "ACTIVE",
                    "metadata": {
                        "scenario": "packaged-e2e-example",
                        "file_name": file_name,
                    },
                    "markdown_content": build_result["md_content"],
                    "chunks": build_result["chunks"],
                },
            )
            pretty_print(f"Knowledge Import Result: {file_name}", import_result)
            imported_files.append(
                {
                    "file_code": file_code,
                    "file_path": file_path,
                    "input_file": str(input_path),
                    "input_file_type": file_type,
                    "chunk_count": len(build_result["chunks"]),
                }
            )

        root_listing = post_api(
            client,
            base_url=base_url,
            path="/api/v1/list_dir",
            payload={"kb_codes": [kb_code], "path": "/"},
        )
        pretty_print("list_dir /", root_listing)

        kb_listing = post_api(
            client,
            base_url=base_url,
            path="/api/v1/list_dir",
            payload={"kb_codes": [kb_code], "path": f"{kb_name}/"},
        )
        pretty_print(f"list_dir {kb_name}/", kb_listing)

        first_level_glob = post_api(
            client,
            base_url=base_url,
            path="/api/v1/glob",
            payload={"kb_codes": [kb_code], "path": f"{kb_name}/*"},
        )
        pretty_print(f"glob {kb_name}/*", first_level_glob)

    pretty_print(
        "Import Summary",
        {
            "input_dir": str(input_dir),
            "imported_file_count": len(imported_files),
            "next_step": "run_instant_qa",
        },
    )


if __name__ == "__main__":
    main()
