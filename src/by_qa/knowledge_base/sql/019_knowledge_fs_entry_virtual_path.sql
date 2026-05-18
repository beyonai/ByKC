DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_fs_entry'
          AND column_name = 'virtual_path'
    ) THEN
        ALTER TABLE knowledge_fs_entry
            ADD COLUMN virtual_path text;
    END IF;
END $$;
