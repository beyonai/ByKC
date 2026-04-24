"""Types for the fast QA capability."""

from enum import Enum


class NodeNames(str, Enum):
    """Fast QA graph node names."""

    REWRITE = "rewrite"
    RETRIEVE = "retrieve"
    ANSWER = "answer"


__all__ = ["NodeNames"]
