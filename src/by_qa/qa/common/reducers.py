"""Reducer helpers shared inside the QA domain."""

from typing import Any, TypeVar

T = TypeVar("T")


def merge_list_with_mode(existing: list[T] | None, update: Any) -> list[T]:
    """Merge list state updates with optional RESET/ADD mode envelopes."""
    if existing is None:
        existing = []

    if isinstance(update, dict):
        mode = str(update.get("mode", "ADD")).upper()
        data = list(update.get("data", []))
        if mode == "RESET":
            return data
        return existing + data

    if isinstance(update, list):
        return existing + update

    return existing
