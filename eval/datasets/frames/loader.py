"""FRAMES dataset query loader.

Loads queries from the HuggingFace dataset `google/frames-benchmark`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.models import EvalQuery


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
    from datasets import load_dataset

    dataset = load_dataset("google/frames-benchmark", split="test")
    queries: list[EvalQuery] = []
    for i, record in enumerate(dataset):
        queries.append(_parse_query(record, i))
    return queries


def load_frames_queries_sample(n: int) -> list[EvalQuery]:
    all_queries = load_frames_queries()
    return all_queries[:n]
