# src/by_qa/knowledge_common/kb_path_utils.py
"""Pure KB virtual-path normalization for reference resolution.

No filesystem access: `..` is collapsed by walking segments so it cannot be
used to escape the KB root. `PurePosixPath.resolve()` is deliberately avoided
because it does not collapse `..` without a real filesystem.
"""

from __future__ import annotations


def normalize_kb_path(base_dir: str, ref: str) -> str | None:
    """Resolve `ref` against `base_dir` to an absolute KB virtual path.

    Returns the path as `/a/b/c`, or None if `..` escapes the KB root.
    Absolute refs (leading `/`) resolve from the KB root.
    """
    base = (base_dir or "").strip("/")
    ref = (ref or "").strip()
    if ref.startswith("/"):
        combined = ref.strip("/")
    else:
        combined = f"{base}/{ref}" if base else ref
    stack: list[str] = []
    for seg in combined.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(seg)
    return "/" + "/".join(stack)
