CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_full_path
ON knowledge_chunk_retrieval_mv (knowledge_base_id, full_path);

CREATE INDEX IF NOT EXISTS idx_chunk_retrieval_mv_search_text
ON knowledge_chunk_retrieval_mv
USING GIN (search_text);
