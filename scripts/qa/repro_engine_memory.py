"""Reproduce QA engine RSS high-water behavior without calling an LLM.

The script runs the real ``BaseQAEngine.stream_search`` and
``FastQAEngine._do_stream_search`` paths, but replaces the production graph with
three deterministic nodes.  The retrieval node creates one large fake chunk;
the default size is 50 MiB.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.util
import gc
import os
import subprocess
import tracemalloc
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from by_qa.config import get_settings
from by_qa.qa.common.models import CoreInput
from by_qa.qa.engines.fast.engine import FastQAEngine
from by_qa.qa.engines.fast.state import FastQAState
from by_qa.qa.engines.fast.types import NodeNames
from by_qa.qa.services.checkpointer_factory import (
    close_checkpointer_async,
    create_checkpointer_async,
)


def _rss_mib() -> float:
    """Return current resident memory, rather than the process peak."""
    rss_kib = int(
        subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            text=True,
        ).strip()
    )
    return rss_kib / 1024


def _report(label: str) -> None:
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    print(
        f"{label:>20} rss={_rss_mib():8.1f} MiB "
        f"traced_current={current / 2**20:8.1f} MiB "
        f"traced_peak={peak / 2**20:8.1f} MiB",
        flush=True,
    )


def _release_allocator_cache() -> bool:
    """Request optional allocator cache release on macOS or glibc Linux."""
    system_lib = ctypes.util.find_library("System")
    if system_lib:
        libsystem = ctypes.CDLL(system_lib)
        relieve = getattr(libsystem, "malloc_zone_pressure_relief", None)
        if relieve is not None:
            relieve.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            relieve.restype = ctypes.c_size_t
            relieve(None, 0)
            return True

    c_lib = ctypes.util.find_library("c")
    if c_lib:
        libc = ctypes.CDLL(c_lib)
        trim = getattr(libc, "malloc_trim", None)
        if trim is not None:
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
            return True
    return False


class FakeRetrievalFastQAEngine(FastQAEngine):
    """Fast engine with real streaming/checkpoint paths and fake graph nodes."""

    def __init__(self, *, checkpointer: Any, chunk_mib: int) -> None:
        super().__init__()
        # False is the LangGraph sentinel for explicitly disabling checkpoints.
        self._checkpointer = checkpointer
        self._payload_bytes = chunk_mib * 2**20

    async def _build_graph(self):
        async def rewrite(state: FastQAState) -> dict[str, Any]:
            return {
                "rewritten_query": state["original_query"],
                "sub_queries": [{"query": state["original_query"]}],
            }

        async def retrieve(state: FastQAState) -> dict[str, Any]:
            prefix = f"{state['original_query']}:"
            content = prefix + ("x" * (self._payload_bytes - len(prefix)))
            return {
                "retrieval_results": [
                    {
                        "content": content,
                        "source": "fake://memory-repro",
                        "score": 1.0,
                    }
                ]
            }

        async def answer(state: FastQAState) -> dict[str, Any]:
            del state
            return {"final_answer": "fake answer"}

        builder = StateGraph(FastQAState)
        builder.add_node(NodeNames.REWRITE.value, rewrite)
        builder.add_node(NodeNames.RETRIEVE.value, retrieve)
        builder.add_node(NodeNames.ANSWER.value, answer)
        builder.add_edge(START, NodeNames.REWRITE.value)
        builder.add_edge(NodeNames.REWRITE.value, NodeNames.RETRIEVE.value)
        builder.add_edge(NodeNames.RETRIEVE.value, NodeNames.ANSWER.value)
        builder.add_edge(NodeNames.ANSWER.value, END)
        return builder.compile(checkpointer=self._checkpointer)


async def _run(args: argparse.Namespace) -> None:
    saver: Any = False
    if args.checkpointer == "opengauss":
        saver = await create_checkpointer_async(
            get_settings(),
            backend="opengauss",
        )

    engine = FakeRetrievalFastQAEngine(
        checkpointer=saver,
        chunk_mib=args.chunk_mib,
    )
    run_prefix = f"qa-memory-repro-{uuid.uuid4()}"
    thread_ids: set[str] = set()
    semaphore = asyncio.Semaphore(args.concurrency)

    async def invoke(call_id: int) -> None:
        async with semaphore:
            session_suffix = (
                call_id if args.unique_sessions else call_id % args.session_count
            )
            session_id = f"{run_prefix}-{session_suffix}"
            thread_ids.add(f"{engine.THREAD_ID_PREFIX}_{session_id}")
            request = CoreInput(
                query=f"fake query {call_id}",
                session_id=session_id,
                message_id=str(uuid.uuid4()),
            )
            async for event in engine.stream_search(request):
                del event

    tracemalloc.start(8)
    _report("baseline")
    for wave_start in range(0, args.calls, args.concurrency):
        wave_end = min(args.calls, wave_start + args.concurrency)
        await asyncio.gather(
            *(invoke(call_id) for call_id in range(wave_start, wave_end))
        )
        _report(f"after {wave_end} calls")

    if saver is not False and args.cleanup:
        for thread_id in thread_ids:
            await saver.adelete_thread(thread_id)
        _report("after DB cleanup")

    engine._graph = None  # pylint: disable=protected-access
    _report("after graph drop")
    await close_checkpointer_async(saver if saver is not False else None)
    engine._checkpointer = None  # pylint: disable=protected-access
    _report("after saver close")

    if args.allocator_release and _release_allocator_cache():
        _report("after allocator release")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpointer",
        choices=["none", "opengauss"],
        default="opengauss",
    )
    parser.add_argument("--chunk-mib", type=int, default=50)
    parser.add_argument("--calls", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--session-count",
        type=int,
        default=1,
        help="number of sessions to reuse in round-robin order",
    )
    parser.add_argument("--unique-sessions", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--allocator-release", action="store_true")
    args = parser.parse_args()
    if args.session_count < 1:
        parser.error("--session-count must be at least 1")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
