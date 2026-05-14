"""Service for global metadata property definition management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.metadata_schemas import (
    BatchCreateMetadataPropertyRequest,
    CreateMetadataPropertyRequest,
    DeleteMetadataPropertyRequest,
    ListMetadataPropertyRequest,
    MetadataPropertyResponse,
)
from by_qa.knowledge_base.repositories.metadata_property_repository import (
    SYSTEM_FIELD_NAMES,
    MetadataPropertyRepository,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class MetadataPropertyService:
    """CRUD operations for global metadata property definitions."""

    connection_factory: Callable[[], Any]
    metadata_property_repository: MetadataPropertyRepository

    def _check_system_field_conflict(self, property_name: str) -> None:
        """Reject property names that collide with system field names."""
        if property_name in SYSTEM_FIELD_NAMES:
            raise KnowledgeBaseValidationError(
                f"property name conflicts with system field: {property_name}"
            )

    async def create_property(
        self, request: CreateMetadataPropertyRequest
    ) -> MetadataPropertyResponse:
        logger.info(
            "metadata_property_service.create started: property_name=%s",
            request.property_name,
        )
        self._check_system_field_conflict(request.property_name)
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            existing = await self.metadata_property_repository.get_by_name(
                cursor, request.property_name
            )
            if existing is not None:
                raise KnowledgeBaseValidationError(
                    f"metadata property already exists: {request.property_name}"
                )
            created = await self.metadata_property_repository.create(
                cursor,
                property_name=request.property_name,
                value_type=request.value_type,
                description=request.description,
                ext_params=request.ext_params,
            )
            if created is None:
                raise KnowledgeBaseValidationError("failed to create metadata property")
            await connection.commit()
            logger.info(
                "metadata_property_service.create committed: kid=%s",
                created["kid"],
            )
            return MetadataPropertyResponse(
                property_name=created["property_name"],
                value_type=created["value_type"],
                description=created["description"],
                ext_params=created["ext_params"],
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def batch_create(
        self, request: BatchCreateMetadataPropertyRequest
    ) -> list[MetadataPropertyResponse]:
        logger.info(
            "metadata_property_service.batch_create started: count=%s",
            len(request.property_list),
        )
        for item in request.property_list:
            self._check_system_field_conflict(item.property_name)
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            results: list[MetadataPropertyResponse] = []
            for item in request.property_list:
                existing = await self.metadata_property_repository.get_by_name(
                    cursor, item.property_name
                )
                if existing is not None:
                    raise KnowledgeBaseValidationError(
                        f"metadata property already exists: {item.property_name}"
                    )
                created = await self.metadata_property_repository.create(
                    cursor,
                    property_name=item.property_name,
                    value_type=item.value_type,
                    description=item.description,
                    ext_params=item.ext_params,
                )
                if created is None:
                    raise KnowledgeBaseValidationError(
                        f"failed to create metadata property: {item.property_name}"
                    )
                results.append(
                    MetadataPropertyResponse(
                        property_name=created["property_name"],
                        value_type=created["value_type"],
                        description=created["description"],
                        ext_params=created["ext_params"],
                    )
                )
            await connection.commit()
            logger.info(
                "metadata_property_service.batch_create committed: count=%s",
                len(results),
            )
            return results
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def delete_property(self, request: DeleteMetadataPropertyRequest) -> None:
        logger.info(
            "metadata_property_service.delete started: property_name=%s",
            request.property_name,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            existing = await self.metadata_property_repository.get_by_name(
                cursor, request.property_name
            )
            if existing is None:
                raise KnowledgeBaseValidationError(
                    f"metadata property not found: {request.property_name}"
                )
            if existing.get("is_system"):
                raise KnowledgeBaseValidationError(
                    f"cannot delete system metadata property: {request.property_name}"
                )
            ref_count = await self.metadata_property_repository.count_references(
                cursor, property_def_id=existing["kid"]
            )
            if ref_count > 0:
                raise KnowledgeBaseValidationError(
                    f"metadata property is still referenced: {request.property_name}"
                )
            await self.metadata_property_repository.soft_delete(
                cursor, property_name=request.property_name
            )
            await connection.commit()
            logger.info(
                "metadata_property_service.delete committed: property_name=%s",
                request.property_name,
            )
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def list_properties(
        self, request: ListMetadataPropertyRequest
    ) -> list[MetadataPropertyResponse]:
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            rows = await self.metadata_property_repository.list_properties(
                cursor, property_names=request.property_name_list
            )
            return [
                MetadataPropertyResponse(
                    property_name=row["property_name"],
                    value_type=row["value_type"],
                    description=row["description"],
                    ext_params=row["ext_params"],
                )
                for row in rows
            ]
        finally:
            await connection.close()
