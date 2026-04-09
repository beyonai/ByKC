CREATE INDEX IF NOT EXISTS idx_knowledge_item_kb_id
ON knowledge_item (knowledge_base_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_item_current_version
ON knowledge_item (current_version_id);
