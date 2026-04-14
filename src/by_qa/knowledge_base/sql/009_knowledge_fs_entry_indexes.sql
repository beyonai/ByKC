CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_root_per_kb
ON knowledge_fs_entry (knowledge_base_id)
WHERE is_root = true AND is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_parent_name
ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, name);

CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_parent_type_name
ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, entry_type, name);

CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_path_ltree
ON knowledge_fs_entry
USING GIST (path_ltree);

CREATE INDEX IF NOT EXISTS idx_knowledge_fs_entry_name_trgm
ON knowledge_fs_entry
USING GIN (name gin_trgm_ops);
