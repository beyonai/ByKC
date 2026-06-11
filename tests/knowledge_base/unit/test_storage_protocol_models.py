"""Unit tests for storage protocol value objects and errors."""

import pytest

from by_qa.knowledge_base.infrastructure.storage import (
    StorageAuthenticationError,
    StorageConfigurationError,
    StorageConflictError,
    StorageError,
    StorageLocation,
    StorageNotFoundError,
    StorageOperationError,
    StoredObject,
)


def test_storage_location_is_frozen_dataclass():
    location = StorageLocation(namespace="bucket-a", key="path/to/object")

    assert location.namespace == "bucket-a"
    assert location.key == "path/to/object"
    with pytest.raises(Exception):
        location.namespace = "other"  # frozen


def test_stored_object_defaults_optional_fields_to_none():
    location = StorageLocation(namespace="b", key="k")
    stored = StoredObject(location=location)

    assert stored.location == location
    assert stored.size is None
    assert stored.checksum is None
    assert stored.content_type is None


def test_storage_error_subclasses_are_exception_subclasses():
    assert issubclass(StorageConfigurationError, StorageError)
    assert issubclass(StorageAuthenticationError, StorageError)
    assert issubclass(StorageNotFoundError, StorageError)
    assert issubclass(StorageConflictError, StorageError)
    assert issubclass(StorageOperationError, StorageError)
    assert StorageError.retryable is False
