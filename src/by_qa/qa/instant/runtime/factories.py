"""Factory helpers for capability-local node wrapping."""

import inspect
from typing import Any, Callable

from by_qa.qa.instant.runtime.hooks import (
    NodeLifecycleCallbacks,
    normalize_node_callbacks,
)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def wrap_node(
    node_name: str, fn: Callable[..., Any], callbacks: NodeLifecycleCallbacks | None
):
    """Wrap a node function with lifecycle callbacks."""
    callback_chain = normalize_node_callbacks(callbacks)
    if not callback_chain:
        return fn

    async def wrapped(state, *args, **kwargs):
        for callback in callback_chain:
            if callback.before is not None:
                await _maybe_await(callback.before(node_name=node_name, state=state))
        try:
            result = await fn(state, *args, **kwargs)
        except Exception as exc:
            for callback in callback_chain:
                if callback.on_error is not None:
                    await _maybe_await(
                        callback.on_error(node_name=node_name, state=state, error=exc)
                    )
            raise
        for callback in callback_chain:
            if callback.after is not None:
                await _maybe_await(
                    callback.after(node_name=node_name, state=state, update=result)
                )
        return result

    return wrapped
