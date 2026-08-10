"""YAML front matter parsing helpers for stored Markdown documents."""

from __future__ import annotations

from typing import Any

import yaml


def split_front_matter(content: bytes) -> tuple[dict[str, Any], bytes]:
    """Return valid YAML front matter and the Markdown body without it."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {}, content

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, content

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing_index is None:
        return {}, content

    try:
        parsed = yaml.safe_load("".join(lines[1:closing_index]))
    except yaml.YAMLError:
        return {}, content
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}, content
    return parsed, "".join(lines[closing_index + 1 :]).encode("utf-8")


def parse_front_matter(content: bytes) -> dict[str, Any]:
    """Extract a valid YAML front matter mapping from Markdown content."""
    return split_front_matter(content)[0]
