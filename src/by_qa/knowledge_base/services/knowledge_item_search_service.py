"""Hybrid retrieval service for knowledge-base chunk search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.metadata_schemas import (
    AgentSearchHit,
    AgentSearchRequest,
    SearchFileHit,
    SearchFileRequest,
)
from by_qa.knowledge_base.api.schemas import SearchHit, SearchRequest
from by_qa.knowledge_base.dsl.compiler import compile_where_to_sql
from by_qa.knowledge_base.dsl.validator import validate_where_clause
from by_qa.knowledge_base.metadata_types import SYSTEM_FIELD_VALUE_TYPES
from by_qa.knowledge_base.services.metadata_search_service import (
    _build_known_fields,
    _collect_field_names,
)


@dataclass
class KnowledgeItemSearchService:
    """Search knowledge-base chunks with text and vector recall."""

    connection_factory: Callable[[], Any]
    search_repository: Any
    embedding_query_service: Any
    metadata_property_repository: Any = None
    metadata_search_repository: Any = None

    def _merge_hits(
        self,
        *,
        text_hits: list[dict[str, Any]],
        vector_hits: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[int, dict[str, Any]] = {}

        for hit in text_hits:
            merged[hit["chunk_id"]] = {
                **hit,
                "vector_score": None,
            }

        for hit in vector_hits:
            existing = merged.get(hit["chunk_id"])
            if existing:
                existing["vector_score"] = hit.get("vector_score")
            else:
                merged[hit["chunk_id"]] = {
                    **hit,
                    "text_score": None,
                }

        for item in merged.values():
            text_score = item.get("text_score") or 0.0
            vector_score = item.get("vector_score") or 0.0
            dual_hit_bonus = 0.05 if text_score and vector_score else 0.0
            item["score"] = vector_score * 0.6 + text_score * 0.4 + dual_hit_bonus

        return sorted(
            merged.values(), key=lambda item: (-item["score"], -item["chunk_id"])
        )

    async def search_v2(self, request: SearchRequest) -> list[SearchHit]:
        """Execute hybrid retrieval and return documented search results."""
        logger.info(
            "knowledge_item_search_service.search_v2 started: query=%s, kb_code_count=%s, top_k=%s, search_mode=%s",
            request.query,
            len(request.kb_codes),
            request.top_k,
            request.search_mode,
        )
        query_embedding = await self.embedding_query_service.embed_query(request.query)
        logger.info(
            "knowledge_item_search_service.search_v2 embedding finished: embedding_dimension=%s",
            len(query_embedding),
        )
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()
            text_hits = await self.search_repository.search_text(
                cursor,
                query=request.query,
                kb_codes=request.kb_codes,
                file_type_list=request.file_type_list,
                limit=request.top_k * 3,
            )
            vector_hits = await self.search_repository.search_vector(
                cursor,
                query_embedding=query_embedding,
                kb_codes=request.kb_codes,
                file_type_list=request.file_type_list,
                limit=request.top_k * 4,
            )
            logger.info(
                "knowledge_item_search_service.search_v2 retrieval finished: text_hit_count=%s, vector_hit_count=%s",
                len(text_hits),
                len(vector_hits),
            )
        finally:
            await connection.close()

        merged = self._merge_hits(text_hits=text_hits, vector_hits=vector_hits)
        items = [
            SearchHit(
                kb_code=item["kb_code"],
                file_path="/" + item["full_path"],
                chunk_no=item["chunk_no"],
                chunk_id=item["chunk_id"],
                chunk_text=item["chunk_text"],
                score=item["score"],
                image_path="",
                start_line=item["start_line"],
                end_line=item["end_line"],
            )
            for item in merged[: request.top_k]
        ]
        logger.info(
            "knowledge_item_search_service.search_v2 finished: returned_count=%s",
            len(items),
        )
        return items

    async def search_with_dsl(
        self, request: AgentSearchRequest
    ) -> list[AgentSearchHit]:
        """Execute hybrid retrieval with optional DSL metadata filtering."""
        logger.info(
            "knowledge_item_search_service.search_with_dsl started: query=%s, top_k=%s",
            request.query,
            request.top_k,
        )
        query_embedding = await self.embedding_query_service.embed_query(request.query)
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()

            kb_codes = request.kb_code_list or []
            property_map = await self._build_property_map(cursor, request.where)

            if request.where:
                validate_where_clause(
                    request.where,
                    known_fields=_build_known_fields(property_map),
                )

            where_sql, where_params = compile_where_to_sql(
                request.where, property_map=property_map
            )

            text_hits = await self.search_repository.search_text(
                cursor,
                query=request.query,
                kb_codes=kb_codes,
                where_sql=where_sql,
                where_params=where_params,
                limit=request.top_k * 3,
            )
            vector_hits = await self.search_repository.search_vector(
                cursor,
                query_embedding=query_embedding,
                kb_codes=kb_codes,
                where_sql=where_sql,
                where_params=where_params,
                limit=request.top_k * 4,
            )

            merged = self._merge_hits(text_hits=text_hits, vector_hits=vector_hits)
            top_items = merged[: request.top_k]

            metadata_map: dict[int, dict] = {}
            if request.metadata_field_list and top_items:
                fs_entry_ids = list(
                    {
                        item.get("fs_entry_id")
                        for item in top_items
                        if item.get("fs_entry_id")
                    }
                )
                if fs_entry_ids and self.metadata_search_repository:
                    metadata_map = (
                        await self.metadata_search_repository.backfill_metadata(
                            cursor,
                            fs_entry_ids=fs_entry_ids,
                            property_names=request.metadata_field_list,
                        )
                    )

            results = [
                AgentSearchHit(
                    kb_code=item["kb_code"],
                    file_path="/" + item["full_path"],
                    chunk_no=item["chunk_no"],
                    chunk_id=item["chunk_id"],
                    chunk_text=item["chunk_text"],
                    score=item["score"],
                    start_line=item["start_line"],
                    end_line=item["end_line"],
                    metadata=metadata_map.get(item.get("fs_entry_id")),
                )
                for item in top_items
            ]
            logger.info(
                "knowledge_item_search_service.search_with_dsl finished: count=%s",
                len(results),
            )
            return results
        finally:
            await connection.close()

    async def search_file_with_dsl(
        self, request: SearchFileRequest
    ) -> list[SearchFileHit]:
        """File-level semantic search: retrieve chunks then aggregate to file level."""
        logger.info(
            "knowledge_item_search_service.search_file started: query=%s, top_k=%s",
            request.query,
            request.top_k,
        )
        query_embedding = await self.embedding_query_service.embed_query(request.query)
        connection = await self.connection_factory()
        try:
            cursor = connection.cursor()

            kb_codes = request.kb_code_list or []
            property_map = await self._build_property_map(cursor, request.where)

            if request.where:
                validate_where_clause(
                    request.where,
                    known_fields=_build_known_fields(property_map),
                )

            where_sql, where_params = compile_where_to_sql(
                request.where, property_map=property_map
            )

            text_hits = await self.search_repository.search_text(
                cursor,
                query=request.query,
                kb_codes=kb_codes,
                where_sql=where_sql,
                where_params=where_params,
                limit=request.top_k * 50,
            )
            vector_hits = await self.search_repository.search_vector(
                cursor,
                query_embedding=query_embedding,
                kb_codes=kb_codes,
                where_sql=where_sql,
                where_params=where_params,
                limit=request.top_k * 50,
            )

            merged = self._merge_hits(text_hits=text_hits, vector_hits=vector_hits)

            # Aggregate to file level
            file_scores: dict[str, dict[str, Any]] = {}
            for item in merged:
                file_key = f"{item['kb_code']}:{item['full_path']}"
                if file_key not in file_scores:
                    file_scores[file_key] = {
                        "kb_code": item["kb_code"],
                        "full_path": item["full_path"],
                        "score": item["score"],
                        "fs_entry_id": item.get("fs_entry_id"),
                    }
                else:
                    existing = file_scores[file_key]
                    if item["score"] > existing["score"]:
                        existing["score"] = item["score"]

            sorted_files = sorted(file_scores.values(), key=lambda x: -x["score"])[
                : request.top_k
            ]

            metadata_map: dict[int, dict] = {}
            if request.metadata_field_list and sorted_files:
                fs_entry_ids = [
                    f["fs_entry_id"] for f in sorted_files if f.get("fs_entry_id")
                ]
                if fs_entry_ids and self.metadata_search_repository:
                    metadata_map = (
                        await self.metadata_search_repository.backfill_metadata(
                            cursor,
                            fs_entry_ids=fs_entry_ids,
                            property_names=request.metadata_field_list,
                        )
                    )

            results = [
                SearchFileHit(
                    kb_code=f["kb_code"],
                    file_path="/" + f["full_path"],
                    score=f["score"],
                    metadata=metadata_map.get(f.get("fs_entry_id")),
                )
                for f in sorted_files
            ]
            logger.info(
                "knowledge_item_search_service.search_file finished: count=%s",
                len(results),
            )
            return results
        finally:
            await connection.close()

    async def _build_property_map(
        self, cursor: Any, where: dict[str, Any] | None
    ) -> dict[str, dict[str, Any]]:
        if where is None or self.metadata_property_repository is None:
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
