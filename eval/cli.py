"""CLI entry point for the eval framework.

Usage:
  uv run python -m eval.cli download frames
  uv run python -m eval.cli ingest frames --kb-base-url http://127.0.0.1:8000
  uv run python -m eval.cli run frames --mode instant
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QA evaluation framework",
        prog="python -m eval.cli",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- download ---
    dl_parser = subparsers.add_parser("download", help="Download dataset files")
    dl_parser.add_argument("dataset", help="Dataset name (e.g., frames)")
    dl_parser.add_argument(
        "--concurrency", type=int, default=4, help="Download concurrency"
    )

    # --- ingest ---
    ing_parser = subparsers.add_parser(
        "ingest", help="Ingest dataset into knowledge base"
    )
    ing_parser.add_argument("dataset", help="Dataset name (e.g., frames)")
    ing_parser.add_argument(
        "--kb-base-url",
        default=os.environ.get("BY_QA_KB_BASE_URL", ""),
        help="Knowledge base service URL (or BY_QA_KB_BASE_URL env)",
    )
    ing_parser.add_argument(
        "--concurrency", type=int, default=4, help="Ingest concurrency"
    )
    ing_parser.add_argument(
        "--retry-failed", action="store_true", help="Retry previously failed files"
    )
    ing_parser.add_argument(
        "--sync-status", action="store_true", help="Sync build status for stale files"
    )

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Run evaluation")
    run_parser.add_argument("dataset", help="Dataset name (e.g., frames)")
    run_parser.add_argument(
        "--mode", choices=["instant", "fast"], default="instant", help="QA engine mode"
    )
    run_parser.add_argument(
        "--language", default=None, help="Fixed output language (e.g., English)"
    )
    run_parser.add_argument(
        "--show-tokens", action="store_true", help="Print token stream"
    )
    run_parser.add_argument(
        "--sample", type=int, default=None, help="Run only N queries"
    )
    run_parser.add_argument(
        "--judge-model", default=None, help="Override judge model type"
    )
    run_parser.add_argument(
        "--query-ids", default=None, help="Comma-separated query IDs to run"
    )

    return parser


def _cmd_download(args: argparse.Namespace) -> None:
    from eval.datasets import get_dataset

    spec = get_dataset(args.dataset)
    spec.download(concurrency=args.concurrency)


def _cmd_ingest(args: argparse.Namespace) -> None:
    if not args.kb_base_url:
        print("Error: --kb-base-url is required (or set BY_QA_KB_BASE_URL env var)")
        sys.exit(1)
    from eval.datasets import get_dataset

    spec = get_dataset(args.dataset)
    spec.ingest(
        base_url=args.kb_base_url,
        concurrency=args.concurrency,
        retry_failed=args.retry_failed,
        sync_status=args.sync_status,
    )


def _cmd_run(args: argparse.Namespace) -> None:
    from eval.datasets import get_dataset
    from eval.runner import run_eval

    spec = get_dataset(args.dataset)
    query_ids = None
    if args.query_ids:
        query_ids = [qid.strip() for qid in args.query_ids.split(",") if qid.strip()]

    asyncio.run(
        run_eval(
            spec=spec,
            mode=args.mode,
            language=args.language,
            show_tokens=args.show_tokens,
            sample=args.sample,
            judge_model=args.judge_model,
            query_ids=query_ids,
        )
    )


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "download":
        _cmd_download(args)
    elif args.command == "ingest":
        _cmd_ingest(args)
    elif args.command == "run":
        _cmd_run(args)


if __name__ == "__main__":
    main()
