"""Service for pure metadata search (no semantic retrieval)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.metadata_schemas import (
    MetadataSearchHit,
    MetadataSearchRequest,
)
from by_qa.knowledge_base.dsl.compiler import compile_where_to_sql
from by_qa.knowledge_base.dsl.validator import validate_where_clause
from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES
from by_qa.knowledge_base.repositories.knowledge_base_repository import (
    KnowledgeBaseRepository,
)
from by_qa.knowledge_base.repositories.metadata_property_repository import (
    MetadataPropertyRepository,
)
from by_qa.knowledge_base.repositories.metadata_search_repository import (
    MetadataSearchRepository,
)
from by_qa.knowledge_base.services.errors import KnowledgeBaseValidationError


@dataclass
class MetadataSearchService:
    """Pure metadata search: filter files by metadata, no semantic retrieval."""

    connection_factory: Callable[[], Any]
    knowledge_base_repository: KnowledgeBaseRepository
    metadata_property_repository: MetadataPropertyRepository
    metadata_search_repository: MetadataSearchRepository

    async def search(self, request: MetadataSearchRequest) -> list[MetadataSearchHit]:
        logger.info(
            "metadata_search_service.search started: top_k=%s, has_where=%s",
            request.top_k,
            request.where is not None,
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()

            kb_ids: list[int] = []
            if request.kb_code_list:
                for code in request.kb_code_list:
                    kb = await self.knowledge_base_repository.get_by_code(cursor, code)
                    if kb is None:
                        raise KnowledgeBaseValidationError(
                            f"knowledge base not found: {code}"
                        )
                    kb_ids.append(kb["kid"])

            property_map = await self._build_property_map(cursor, request.where)

            if request.where:
                known_fields = _build_known_fields(property_map)
                validate_where_clause(request.where, known_fields=known_fields)

            where_sql, where_params = compile_where_to_sql(
                request.where, property_map=property_map
            )

            file_rows = await self.metadata_search_repository.search_files(
                cursor,
                kb_ids=kb_ids,
                where_sql=where_sql,
                where_params=where_params,
                limit=request.top_k,
            )

            results: list[MetadataSearchHit] = []
            if file_rows and request.metadata_field_list:
                fs_entry_ids = [row["kid"] for row in file_rows]
                metadata_map = await self.metadata_search_repository.backfill_metadata(
                    cursor,
                    fs_entry_ids=fs_entry_ids,
                    property_names=request.metadata_field_list,
                )
                for row in file_rows:
                    results.append(
                        MetadataSearchHit(
                            kb_code=row["kb_code"],
                            file_path="/" + row["full_path"],
                            metadata=metadata_map.get(row["kid"]),
                        )
                    )
            else:
                for row in file_rows:
                    results.append(
                        MetadataSearchHit(
                            kb_code=row["kb_code"],
                            file_path="/" + row["full_path"],
                            metadata=None,
                        )
                    )

            logger.info(
                "metadata_search_service.search finished: result_count=%s",
                len(results),
            )
            return results
        finally:
            await connection.close()

    async def _build_property_map(
        self, cursor: Any, where: dict[str, Any] | None
    ) -> dict[str, dict[str, Any]]:
        if where is None:
            return {}
        field_names = _collect_field_names(where)
        if not field_names:
            return {}
        custom_names = [n for n in field_names if n not in SYSTEM_FIELD_VALUE_TYPES]
        if not custom_names:
            return {}
        rows = await self.metadata_property_repository.list_properties(
            cursor, property_names=custom_names
        )
        return {
            row["property_name"]: {
                "def_id": row["kid"],
                "value_type": row["value_type"],
            }
            for row in rows
        }


def _build_known_fields(property_map: dict[str, dict[str, Any]]) -> dict[str, str]:
    known: dict[str, str] = dict(SYSTEM_FIELD_VALUE_TYPES)
    for name, info in property_map.items():
        known[name] = info["value_type"]
    return known


def _collect_field_names(node: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    if not isinstance(node, dict):
        return names
    for key, value in node.items():
        if key in ("and", "or"):
            for child in value:
                names.update(_collect_field_names(child))
        elif key == "not":
            names.update(_collect_field_names(value))
        elif isinstance(value, dict) and "fieldName" in value:
            names.add(value["fieldName"])
    return names
