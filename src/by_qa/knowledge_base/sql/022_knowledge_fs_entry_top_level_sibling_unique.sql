CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_top_level_sibling_name_active
    ON knowledge_fs_entry (knowledge_base_id, name)
    WHERE parent_entry_id IS NULL
      AND is_root = false
      AND is_deleted = false;
