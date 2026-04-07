"""Hook models for the instant-search capability runtime."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

NodeHook = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass
class NodeLifecycleCallbacks:
    """Callbacks invoked around node execution."""

    before: NodeHook | None = None
    after: NodeHook | None = None
    on_error: NodeHook | None = None


def normalize_node_callbacks(
    callbacks: (
        NodeLifecycleCallbacks
        | list[NodeLifecycleCallbacks]
        | tuple[NodeLifecycleCallbacks, ...]
        | None
    ),
) -> list[NodeLifecycleCallbacks]:
    """Normalize node callbacks into an ordered list."""
    if callbacks is None:
        return []
    if isinstance(callbacks, NodeLifecycleCallbacks):
        return [callbacks]
    return list(callbacks)


__all__ = ["NodeLifecycleCallbacks", "normalize_node_callbacks"]
