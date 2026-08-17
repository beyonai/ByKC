-- Idempotent, additive upgrade of the Markdown-only 026 schema.
-- Both fresh and existing databases receive semantic fields only from this file.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'reference_type'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN reference_type varchar(16) NOT NULL DEFAULT 'MARKDOWN';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'relation_code'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN relation_code varchar(32) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'confidence'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN confidence numeric(5,4) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'discovered_by'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN discovered_by varchar(32) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'definition_version'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN definition_version varchar(64) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'source_task_id'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN source_task_id bigint NULL
            REFERENCES knowledge_semantic_processing_task(kid) ON DELETE SET NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_type'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_knowledge_file_reference_type
            CHECK (reference_type IN ('MARKDOWN', 'SEMANTIC'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_semantic_state'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_knowledge_file_reference_semantic_state
            CHECK (
                (
                    reference_type = 'MARKDOWN'
                    AND relation_code IS NULL
                    AND confidence IS NULL
                    AND discovered_by IS NULL
                    AND definition_version IS NULL
                    AND source_task_id IS NULL
                )
                OR
                (
                    reference_type = 'SEMANTIC'
                    AND status = 'resolved'
                    AND target_fs_entry_id IS NOT NULL
                    AND target_path IS NULL
                    AND target_suffix = ''
                    AND target_kind = 'FILE'
                    AND relation_code IN ('MENTIONS', 'PART_OF', 'IS_A', 'DEPENDS_ON')
                    AND source_fs_entry_id <> target_fs_entry_id
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_confidence'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_knowledge_file_reference_confidence
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_kfr_semantic_relation
    ON knowledge_file_reference (
        source_fs_entry_id,
        relation_code,
        target_fs_entry_id
    )
    WHERE reference_type = 'SEMANTIC';

CREATE INDEX IF NOT EXISTS idx_kfr_semantic_source
    ON knowledge_file_reference (
        knowledge_base_id,
        source_fs_entry_id,
        relation_code,
        kid
    )
    WHERE reference_type = 'SEMANTIC';

CREATE INDEX IF NOT EXISTS idx_kfr_semantic_target
    ON knowledge_file_reference (
        knowledge_base_id,
        target_fs_entry_id,
        relation_code,
        kid
    )
    WHERE reference_type = 'SEMANTIC';

CREATE INDEX IF NOT EXISTS idx_kfr_semantic_source_task
    ON knowledge_file_reference (source_task_id, kid)
    WHERE reference_type = 'SEMANTIC'
      AND source_task_id IS NOT NULL;
