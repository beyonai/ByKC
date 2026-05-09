"""Data models for the eval framework."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalQuery:
    query_id: str
    question: str
    ground_truth: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    query: EvalQuery
    answer: str
    tokens_used: int
    latency_ms: float
    error: str | None = None


@dataclass
class JudgeVerdict:
    query_id: str
    score: int  # 0 or 1
    reasoning: str
    judge_model: str


@dataclass
class EvalReport:
    dataset_name: str
    mode: str
    timestamp: str
    total_queries: int
    correct: int
    accuracy: float
    total_tokens: int
    total_latency_ms: float
    verdicts: list[JudgeVerdict] = field(default_factory=list)
    unscored_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "mode": self.mode,
            "timestamp": self.timestamp,
            "total_queries": self.total_queries,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "unscored_count": self.unscored_count,
            "error_count": self.error_count,
            "verdicts": [
                {
                    "query_id": v.query_id,
                    "score": v.score,
                    "reasoning": v.reasoning,
                    "judge_model": v.judge_model,
                }
                for v in self.verdicts
            ],
        }
