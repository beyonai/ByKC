"""Hybrid retrieval service for knowledge-base chunk search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from by_qa.core import logger
from by_qa.knowledge_base.api.schemas import (
    KnowledgeItemSearchHit,
    KnowledgeItemSearchMeta,
    KnowledgeItemSearchRequest,
    KnowledgeItemSearchResponse,
    SearchHit,
    SearchRequest,
)


@dataclass
class KnowledgeItemSearchService:
    """Search knowledge-base chunks with text and vector recall."""

    connection_factory: Callable[[], Any]
    search_repository: Any
    embedding_query_service: Any

    def search(
        self, request: KnowledgeItemSearchRequest
    ) -> KnowledgeItemSearchResponse:
        """Execute hybrid retrieval and return chunk-oriented results."""
        logger.info(
            "knowledge_item_search_service.search started: query=%s, kb_code_count=%s, top_k=%s, vector_top_k=%s, text_top_k=%s",
            request.query,
            len(request.kb_codes),
            request.top_k,
            request.vector_top_k,
            request.text_top_k,
        )
        logger.info(
            "knowledge_item_search_service embedding started: query=%s",
            request.query,
        )
        query_embedding = self.embedding_query_service.embed_query(request.query)
        logger.info(
            "knowledge_item_search_service embedding finished: embedding_dimension=%s",
            len(query_embedding),
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            logger.info(
                "knowledge_item_search_service hybrid retrieval started: text_limit=%s, vector_limit=%s",
                request.text_top_k,
                request.vector_top_k,
            )
            text_hits = self.search_repository.search_text(
                cursor,
                query=request.query,
                kb_codes=request.kb_codes,
                source_codes=request.source_codes,
                type_codes=request.type_codes,
                limit=request.text_top_k,
            )
            vector_hits = self.search_repository.search_vector(
                cursor,
                query_embedding=query_embedding,
                kb_codes=request.kb_codes,
                source_codes=request.source_codes,
                type_codes=request.type_codes,
                limit=request.vector_top_k,
            )
            logger.info(
                "knowledge_item_search_service hybrid retrieval finished: text_hit_count=%s, vector_hit_count=%s",
                len(text_hits),
                len(vector_hits),
            )
        finally:
            connection.close()

        merged = self._merge_hits(text_hits=text_hits, vector_hits=vector_hits)
        logger.info(
            "knowledge_item_search_service merge finished: merged_count=%s",
            len(merged),
        )
        items = [
            KnowledgeItemSearchHit(
                kb_code=item["kb_code"],
                file_code=item["item_code"],
                version=item["version"],
                chunk_no=item["chunk_no"],
                chunk_text=item["chunk_text"],
                score=item["score"],
                text_score=item.get("text_score"),
                vector_score=item.get("vector_score"),
                source_code=item["source_code"],
                type_code=item["type_code"],
                file_path=item["full_path"],
            )
            for item in merged[: request.top_k]
        ]
        logger.info(
            "knowledge_item_search_service search finished: returned_count=%s",
            len(items),
        )
        return KnowledgeItemSearchResponse(
            items=items,
            meta=KnowledgeItemSearchMeta(
                query=request.query,
                top_k=request.top_k,
                vector_top_k=request.vector_top_k,
                text_top_k=request.text_top_k,
                returned_count=len(items),
            ),
        )

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

    def search_v2(self, request: SearchRequest) -> list[SearchHit]:
        """Execute hybrid retrieval and return documented search results."""
        logger.info(
            "knowledge_item_search_service.search_v2 started: query=%s, kb_code_count=%s, top_k=%s, search_mode=%s",
            request.query,
            len(request.kb_codes),
            request.top_k,
            request.search_mode,
        )
        query_embedding = self.embedding_query_service.embed_query(request.query)
        logger.info(
            "knowledge_item_search_service.search_v2 embedding finished: embedding_dimension=%s",
            len(query_embedding),
        )
        connection = self.connection_factory()
        try:
            cursor = connection.cursor()
            text_hits = self.search_repository.search_text_v2(
                cursor,
                query=request.query,
                kb_codes=request.kb_codes,
                file_type_list=request.file_type_list,
                limit=request.top_k * 3,
            )
            vector_hits = self.search_repository.search_vector_v2(
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
            connection.close()

        merged = self._merge_hits(text_hits=text_hits, vector_hits=vector_hits)
        items = [
            SearchHit(
                kn_code=item["kb_code"],
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
