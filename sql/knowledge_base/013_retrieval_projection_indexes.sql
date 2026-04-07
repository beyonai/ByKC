CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_kb_version
ON knowledge_item_chunk_retrieval_mv (kb_code, version);

CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_kb_status
ON knowledge_item_chunk_retrieval_mv (kb_code, knowledge_base_status, knowledge_item_status);

CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_kb_source_type
ON knowledge_item_chunk_retrieval_mv (kb_code, source_code, type_code);

CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_full_path
ON knowledge_item_chunk_retrieval_mv (knowledge_base_id, full_path);

CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_search_text
ON knowledge_item_chunk_retrieval_mv
USING GIN (search_text);
