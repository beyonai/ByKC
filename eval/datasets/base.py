"""Base types for dataset specs."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eval.models import EvalQuery


@dataclass
class KbConfig:
    kb_base_url: str | None = None
    kb_service_name: str = "by-qa-manager"
    kb_search_url: str = "/api/v1/knowledgeItems/search"


class DatasetSpec(ABC):
    """Abstract spec for a QA evaluation dataset."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def kb_config(self) -> KbConfig: ...

    @property
    @abstractmethod
    def ingest_state_path(self) -> Path: ...

    @property
    def data_dir(self) -> Path:
        """Dataset root directory for output files (inference results, reports)."""
        return Path(f"datasets/{self.name.upper()}")

    @abstractmethod
    def load_queries(self) -> list[EvalQuery]: ...

    @abstractmethod
    def load_queries_sample(self, n: int) -> list[EvalQuery]: ...

    @abstractmethod
    def download(self, **kwargs) -> None: ...

    @abstractmethod
    def ingest(self, **kwargs) -> None: ...

    def get_kb_code(self) -> str:
        if not self.ingest_state_path.exists():
            raise FileNotFoundError(
                f"Ingest state not found at {self.ingest_state_path}. "
                f"Run 'python -m eval.cli ingest {self.name}' first."
            )
        state = json.loads(self.ingest_state_path.read_text(encoding="utf-8"))
        return state["kb_code"]

    def get_kb_name(self) -> str:
        if not self.ingest_state_path.exists():
            raise FileNotFoundError(
                f"Ingest state not found at {self.ingest_state_path}."
            )
        state = json.loads(self.ingest_state_path.read_text(encoding="utf-8"))
        return state.get("kb_name", self.name)
