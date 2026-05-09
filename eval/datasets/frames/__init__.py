"""FRAMES benchmark dataset."""

from pathlib import Path

from eval.datasets.base import DatasetSpec, KbConfig
from eval.datasets.frames.loader import load_frames_queries, load_frames_queries_sample

_HERE = Path(__file__).parent


class _FramesSpec(DatasetSpec):
    @property
    def name(self) -> str:
        return "frames"

    @property
    def kb_config(self) -> KbConfig:
        return KbConfig()

    @property
    def ingest_state_path(self) -> Path:
        return _HERE / ".ingest_state.json"

    def load_queries(self):
        return load_frames_queries()

    def load_queries_sample(self, n: int):
        return load_frames_queries_sample(n)

    def download(self, **kwargs) -> None:
        from eval.datasets.frames.download import main as download_main

        download_main(**kwargs)

    def ingest(self, **kwargs) -> None:
        from eval.datasets.frames.ingest import main as ingest_main

        ingest_main(**kwargs)


FRAMES_SPEC = _FramesSpec()
