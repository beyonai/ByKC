DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_item'
          AND constraint_name = 'fk_knowledge_item_current_version'
    ) THEN
        ALTER TABLE knowledge_item
            ADD CONSTRAINT fk_knowledge_item_current_version
            FOREIGN KEY (current_version_id)
            REFERENCES knowledge_item_version(kid)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;
