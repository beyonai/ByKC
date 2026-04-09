DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_base'
          AND constraint_name = 'fk_knowledge_base_root_entry'
    ) THEN
        ALTER TABLE knowledge_base
            ADD CONSTRAINT fk_knowledge_base_root_entry
            FOREIGN KEY (root_entry_id)
            REFERENCES knowledge_fs_entry(kid)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
