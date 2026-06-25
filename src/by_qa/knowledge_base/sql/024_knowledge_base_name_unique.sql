CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_base_name_active
ON knowledge_base (kb_name)
WHERE is_deleted = false;
