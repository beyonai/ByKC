"""Build, import, and browse knowledge-base files from one directory."""

from __future__ import annotations

import argparse
import time

import httpx
from common import (
    build_example_kb_name,
    list_supported_input_files,
    normalized_base_url,
    post_api,
    post_multipart_api,
    pretty_print,
    resolve_input_directory,
    runtime_dir,
    save_example_kb_state,
    wait_for_health,
)


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the packaged KB flow example."""
    parser = argparse.ArgumentParser(
        description="Run fileToMarkdownIndex, KB import, listDir, and glob in one flow."
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
    root = runtime_dir(args.runtime_dir)
    base_url = normalized_base_url(args.base_url)
    kb_name = build_example_kb_name()
    input_dir = resolve_input_directory(args.dir)
    input_files = list_supported_input_files(input_dir)

    health = wait_for_health(base_url)
    pretty_print("Health Check", health)

    with httpx.Client(timeout=120.0) as client:
        create_result = post_api(
            client,
            base_url=base_url,
            path="/api/v1/knowledgeBases/create",
            payload={
                "knName": kb_name,
                "knDescription": "用于演示知识构建、knowledge_base 与即时问答的端到端样例。",
            },
        )
        kb_code = create_result["knCode"]
        save_example_kb_state(root, kb_code=kb_code, kb_name=kb_name)
        pretty_print("Create Knowledge Base", create_result)

        imported_files: list[dict[str, str | int]] = []
        timestamp = int(time.time())
        for index, input_path in enumerate(input_files, start=1):
            file_name = input_path.name
            file_path = f"/imports/{timestamp}-{index}-{file_name}"

            import_result = post_multipart_api(
                client,
                base_url=base_url,
                path="/api/v1/knowledgeItems/import",
                data={
                    "knCode": kb_code,
                    "filePath": file_path,
                    "fileDescription": f"端到端示例导入文件: {file_name}",
                },
                file_field_name="fileContent",
                file_path=input_path,
            )
            pretty_print(f"Knowledge Import Result: {file_name}", import_result)

            build_result = post_api(
                client,
                base_url=base_url,
                path="/api/v1/fileToMarkdownIndex",
                payload={"knCode": kb_code, "filePath": file_path},
            )
            pretty_print(
                f"Knowledge Build Triggered: {file_name}",
                {"knCode": kb_code, "filePath": file_path, **build_result},
            )

            imported_files.append(
                {
                    "file_path": file_path,
                    "input_file": str(input_path),
                }
            )

        root_listing = post_api(
            client,
            base_url=base_url,
            path="/api/v1/listDir",
            payload={"knCode": kb_code, "directoryPath": "/"},
        )
        pretty_print("listDir /", root_listing)

        kb_listing = post_api(
            client,
            base_url=base_url,
            path="/api/v1/listDir",
            payload={"knCode": kb_code, "directoryPath": "/imports"},
        )
        pretty_print("listDir /imports", kb_listing)

        first_level_glob = post_api(
            client,
            base_url=base_url,
            path="/api/v1/glob",
            payload={"knCode": kb_code, "pathRule": "/imports/*"},
        )
        pretty_print("glob /imports/*", first_level_glob)

    pretty_print(
        "Import Summary",
        {
            "input_dir": str(input_dir),
            "kb_code": kb_code,
            "kb_name": kb_name,
            "imported_file_count": len(imported_files),
            "next_step": "run_instant_qa",
        },
    )


if __name__ == "__main__":
    main()
