"""Configurable heading pattern templates for chunk structure inference."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HeadingPattern:
    """Single heading template used for per-document hierarchy inference."""

    name: str
    regex: str
    explicit_level: int | None = None
    markdown_only: bool = False
    reject_if_contains_colon: bool = False

    def to_dict(self) -> dict[str, str | int | bool | None]:
        """Serialize a heading pattern for JSON-based configuration."""
        return {
            "name": self.name,
            "regex": self.regex,
            "explicit_level": self.explicit_level,
            "markdown_only": self.markdown_only,
            "reject_if_contains_colon": self.reject_if_contains_colon,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "HeadingPattern":
        """Build a heading pattern from a JSON-style mapping."""
        return cls(
            name=payload["name"],
            regex=payload["regex"],
            explicit_level=payload.get("explicit_level"),
            markdown_only=payload.get("markdown_only", False),
            reject_if_contains_colon=payload.get("reject_if_contains_colon", False),
        )


DEFAULT_HEADING_PATTERNS: tuple[HeadingPattern, ...] = (
    HeadingPattern(
        name="markdown_h1",
        regex=r"^#\s+\S",
        explicit_level=1,
        markdown_only=True,
    ),
    HeadingPattern(
        name="markdown_h2",
        regex=r"^##\s+\S",
        explicit_level=2,
        markdown_only=True,
    ),
    HeadingPattern(
        name="markdown_h3",
        regex=r"^###\s+\S",
        explicit_level=3,
        markdown_only=True,
    ),
    HeadingPattern(
        name="markdown_h4",
        regex=r"^####\s+\S",
        explicit_level=4,
        markdown_only=True,
    ),
    HeadingPattern(
        name="chapter_style",
        regex=r"^第[一二三四五六七八九十百千万零〇0-9]+[章节部分篇]",
    ),
    HeadingPattern(
        name="cn_enumeration",
        regex=r"^[一二三四五六七八九十百千万零〇]+、",
    ),
    HeadingPattern(
        name="cn_paren_enumeration",
        regex=r"^[（(][一二三四五六七八九十百千万零〇]+[）)]",
    ),
    HeadingPattern(
        name="numeric_nested",
        regex=r"^\d+(?:\.\d+)+\s*\S",
        reject_if_contains_colon=True,
    ),
    HeadingPattern(
        name="numeric_dot",
        regex=r"^\d+(?:\.\d+)*[.、]\s*\S",
        reject_if_contains_colon=True,
    ),
)


def load_heading_patterns(config_path: str | Path) -> list[HeadingPattern]:
    """Load heading pattern definitions from a JSON config file."""
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array of heading patterns")
    return [HeadingPattern.from_dict(item) for item in payload]


def dump_heading_patterns(
    patterns: list[HeadingPattern],
) -> list[dict[str, str | int | bool | None]]:
    """Convert heading patterns into JSON-serializable dictionaries."""
    return [pattern.to_dict() for pattern in patterns]
