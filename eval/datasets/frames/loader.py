"""FRAMES dataset query loader.

Reads queries from the local JSONL file saved by the download step.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.models import EvalQuery

DEFAULT_QUERIES_PATH = Path("datasets/FRAMES/frames_wiki_pages/frames_queries.jsonl")


def _resolve_queries_path() -> Path:
    env_path = os.environ.get("FRAMES_QUERIES_PATH", "")
    if env_path:
        return Path(env_path)
    return DEFAULT_QUERIES_PATH


def _parse_query(record: dict, index: int) -> EvalQuery:
    from eval.models import EvalQuery

    query_id = str(record.get("Unnamed: 0", index))
    question = record.get("Prompt", "")
    ground_truth = record.get("Answer", "")

    metadata = {
        k: v for k, v in record.items() if k not in ("Unnamed: 0", "Prompt", "Answer")
    }

    return EvalQuery(
        query_id=query_id,
        question=question,
        ground_truth=ground_truth,
        metadata=metadata,
    )


def load_frames_queries() -> list[EvalQuery]:
    path = _resolve_queries_path()
    if not path.exists():
        raise FileNotFoundError(
            f"FRAMES queries file not found at {path.resolve()}. "
            "Run 'python -m eval.cli download frames' first."
        )

    queries: list[EvalQuery] = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            queries.append(_parse_query(json.loads(line), i))
    return queries


def load_frames_queries_sample(n: int) -> list[EvalQuery]:
    all_queries = load_frames_queries()
    return all_queries[:n]
