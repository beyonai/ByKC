"""Dataset loaders and specs."""

from eval.datasets.base import DatasetSpec

DATASET_REGISTRY: dict[str, DatasetSpec] = {}


def register_dataset(spec: DatasetSpec) -> None:
    DATASET_REGISTRY[spec.name] = spec


def get_dataset(name: str) -> DatasetSpec:
    if name not in DATASET_REGISTRY:
        available = ", ".join(sorted(DATASET_REGISTRY))
        raise ValueError(
            f"Unknown dataset '{name}'. Available: {available or '(none)'}"
        )
    return DATASET_REGISTRY[name]
