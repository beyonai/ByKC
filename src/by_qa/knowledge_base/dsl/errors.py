"""Structured error types for DSL validation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DslValidationDetail:
    path: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class DslValidationError(Exception):
    """Raised when Agent DSL fails structural or semantic validation."""

    error_list: list[DslValidationDetail] = field(default_factory=list)

    def __str__(self) -> str:
        return "request validation failed"

    def to_result_object(self) -> dict:
        return {
            "errorCode": "DSL_VALIDATION_ERROR",
            "errorList": [e.to_dict() for e in self.error_list],
        }
