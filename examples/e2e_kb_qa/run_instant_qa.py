"""Run instant QA against the knowledge-base example imported by run_kb_flow."""

from __future__ import annotations

import argparse
import asyncio
import os

from common import (
    ExampleError,
    load_example_kb_state,
    pretty_print,
    require_environment,
    runtime_dir,
)


class EventRenderer:
    """Render stream events with token-first CLI output."""

    def __init__(self, *, stream_tokens: bool, verbose_events: bool):
        self.stream_tokens = stream_tokens
        self.verbose_events = verbose_events
        self._saw_token = False
        self._line_open = False

    def _ensure_line_break(self) -> None:
        if self._line_open:
            print()
            self._line_open = False

    def render(self, event) -> None:
        """Render one event to stdout."""
        from by_qa.qa.common.models import StreamEventType

        if event.type == StreamEventType.TOKEN:
            if not self.stream_tokens:
                return
            content = event.data.get("content", "")
            if content:
                print(content, end="", flush=True)
                self._saw_token = True
                self._line_open = True
            return

        if event.type == StreamEventType.ANSWER:
            if self._saw_token:
                return
            self._ensure_line_break()
            pretty_print("Final Answer", event.data.get("content", ""))
            return

        if event.type == StreamEventType.ERROR:
            self._ensure_line_break()
            pretty_print("Instant QA Error", event.data)
            return

        if not self.verbose_events:
            return

        self._ensure_line_break()
        if event.type == StreamEventType.SEARCH_RESULT_CHUNKS:
            pretty_print(
                "Retrieved Chunks",
                {"role": event.role, "chunk_count": len(event.data.get("chunks", []))},
            )
            return

        pretty_print("Instant QA Event", {"type": event.type.value, "role": event.role})

    def finish(self) -> None:
        """Close any open token stream line."""
        self._ensure_line_break()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the instant QA example."""
    parser = argparse.ArgumentParser(
        description="Ask one instant-QA question against the packaged knowledge-base example."
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help="Directory used by the example scripts for saved state and checkpoints.",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Question to ask through InstantQAEngine.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Top K forwarded to the retrieval runtime.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable token-by-token streaming and only print the final answer.",
    )
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="Print retrieval and node events in addition to the answer stream.",
    )
    return parser


async def _run_async(args: argparse.Namespace) -> None:
    """Create the engine and stream one answer."""
    from by_qa.qa.instant.runtime.operation_registry import OperationType

    root = runtime_dir(args.runtime_dir)
    kb_code, kb_name = load_example_kb_state(root)

    require_environment(["LLM_API_KEY"])
    os.environ.setdefault("AGENT_DATA_PATH", str(root / "agent_data"))

    from by_qa.qa.common.models import CoreInput
    from by_qa.qa.instant.engine import InstantQAEngine

    renderer = EventRenderer(
        stream_tokens=not args.no_stream,
        verbose_events=args.verbose_events,
    )
    engine = InstantQAEngine(
        config={
            "retrieval": {
                "knowledge_bases": [
                    {
                        "kb_code": kb_code,
                        "kb_name": kb_name,
                        "kb_description": "Packaged end-to-end example knowledge base.",
                        "service_name": os.getenv("SERVICE_NAME", "by-qa-manager"),
                        "operations": {
                            OperationType.SEARCH: "/api/v1/knowledgeItems/search",
                        },
                    }
                ],
                "top_k": args.top_k,
                "vector_top_k": max(args.top_k, 20),
                "text_top_k": max(args.top_k, 20),
            }
        }
    )
    request = CoreInput(query=args.query)
    async for event in engine.stream_search(request):
        renderer.render(event)
    renderer.finish()


def main() -> None:
    """Run the packaged instant-QA example."""
    args = build_parser().parse_args()
    try:
        asyncio.run(_run_async(args))
    except ExampleError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
