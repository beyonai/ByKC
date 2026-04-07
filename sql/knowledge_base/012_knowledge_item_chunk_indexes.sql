CREATE INDEX IF NOT EXISTS idx_knowledge_item_chunk_item_id
ON knowledge_item_chunk (knowledge_item_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_item_chunk_version_id
ON knowledge_item_chunk (knowledge_item_version_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_item_chunk_version_line
ON knowledge_item_chunk (knowledge_item_version_id, start_line, end_line);

CREATE INDEX IF NOT EXISTS idx_knowledge_item_chunk_search_text
ON knowledge_item_chunk
USING GIN (search_text);
