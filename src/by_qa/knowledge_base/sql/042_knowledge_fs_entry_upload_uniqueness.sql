-- Reassert constraints for legacy schemas whose baseline ledger can include
-- migrations that were not actually applied. Duplicate live entries deliberately
-- fail this migration: repairing their children/references requires explicit work.
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_sibling_name_active
    ON knowledge_fs_entry (knowledge_base_id, parent_entry_id, name)
    WHERE is_deleted = false;

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_fs_entry_top_level_sibling_name_active
    ON knowledge_fs_entry (knowledge_base_id, name)
    WHERE parent_entry_id IS NULL
      AND is_root = false
      AND is_deleted = false;
