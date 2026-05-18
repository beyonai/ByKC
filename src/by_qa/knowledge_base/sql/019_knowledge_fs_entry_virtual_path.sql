ALTER TABLE knowledge_fs_entry
    ADD COLUMN IF NOT EXISTS virtual_path text;
