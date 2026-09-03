"""Tests for stable File Build domain values and profile hashing."""

import json

import pytest
from pydantic import ValidationError

from by_qa.knowledge_base.services.file_build_models import (
    ChunkingBuildProfile,
    EmbeddingBuildProfile,
    FileBuildProfile,
)


def test_build_profile_hash_uses_canonical_sorted_json_with_defaults():
    profile = FileBuildProfile(
        embedding=EmbeddingBuildProfile(model="bge-m3", dimension=1024)
    )
    reconstructed = FileBuildProfile.model_validate(
        {
            "retrieval": {"schema_version": 1},
            "embedding": {"dimension": 1024, "model": "bge-m3"},
            "chunking": {"overlap": 64, "size": 512},
            "parser": {"version": "1", "name": "document_chunking_service"},
            "profile_version": 1,
        }
    )

    assert profile.sha256() == reconstructed.sha256()
    assert profile.canonical_json() == json.dumps(
        profile.storage_value(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert profile.storage_value()["profileVersion"] == 1
    assert profile.storage_value()["chunking"] == {"size": 512, "overlap": 64}
    assert profile.storage_value()["retrieval"] == {"schemaVersion": 1}


def test_build_profile_strictly_rejects_wrong_types():
    with pytest.raises(ValidationError):
        FileBuildProfile(
            embedding={"model": "bge-m3", "dimension": "1024"}  # type: ignore[arg-type]
        )


def test_chunk_overlap_must_be_smaller_than_chunk_size():
    with pytest.raises(ValidationError, match="overlap"):
        ChunkingBuildProfile(size=64, overlap=64)
