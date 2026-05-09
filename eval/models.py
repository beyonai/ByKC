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
class InferenceResult:
    """Self-contained inference result for JSONL serialization."""

    query_id: str
    question: str
    ground_truth: str
    answer: str
    tokens_used: int
    latency_ms: float
    error: str | None = None
    score: int | None = None
    reasoning: str | None = None
    judge_model: str | None = None

    @classmethod
    def from_query_result(cls, result: QueryResult) -> "InferenceResult":
        return cls(
            query_id=result.query.query_id,
            question=result.query.question,
            ground_truth=result.query.ground_truth,
            answer=result.answer,
            tokens_used=result.tokens_used,
            latency_ms=result.latency_ms,
            error=result.error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "question": self.question,
            "ground_truth": self.ground_truth,
            "answer": self.answer,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "score": self.score,
            "reasoning": self.reasoning,
            "judge_model": self.judge_model,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "InferenceResult":
        return cls(
            query_id=d["query_id"],
            question=d["question"],
            ground_truth=d["ground_truth"],
            answer=d.get("answer", ""),
            tokens_used=d.get("tokens_used", 0),
            latency_ms=d.get("latency_ms", 0),
            error=d.get("error"),
            score=d.get("score"),
            reasoning=d.get("reasoning"),
            judge_model=d.get("judge_model"),
        )


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
