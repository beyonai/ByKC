-- Additive upgrade from path references to unified relation assertions.
-- Historical 026 remains unchanged; every row becomes a relation assertion.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'relation_code'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN relation_code varchar(32) NOT NULL DEFAULT 'MENTIONS';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'confidence'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN confidence numeric(5,4) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'discovered_by'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN discovered_by varchar(32)
            NOT NULL DEFAULT 'MARKDOWN_PARSER';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'producer_run_id'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN producer_run_id varchar(64) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'evidence_fingerprint'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN evidence_fingerprint varchar(128) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'source_heading_path'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN source_heading_path text NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'start_line'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN start_line integer NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'end_line'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN end_line integer NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'start_offset'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN start_offset bigint NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'end_offset'
    ) THEN
        ALTER TABLE knowledge_file_reference ADD COLUMN end_offset bigint NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'target_locator_type'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN target_locator_type varchar(32) NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'target_locator_value'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN target_locator_value text NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'definition_version'
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD COLUMN definition_version varchar(64) NULL;
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

-- The first semantic-relation draft used a reference_type discriminator and
-- kept relation_code/discovered_by nullable for Markdown rows.  Remove its
-- checks before normalizing every historical row into one assertion contract.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_type'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            DROP CONSTRAINT chk_knowledge_file_reference_type;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_semantic_state'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            DROP CONSTRAINT chk_knowledge_file_reference_semantic_state;
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_knowledge_file_reference_confidence'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            DROP CONSTRAINT chk_knowledge_file_reference_confidence;
    END IF;
END $$;

-- Preserve producer provenance from the discriminator draft before dropping
-- reference_type.  Dynamic SQL is required because fresh 026 schemas do not
-- contain that column.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'reference_type'
    ) THEN
        EXECUTE '
            UPDATE knowledge_file_reference
            SET relation_code = COALESCE(relation_code, ''MENTIONS''),
                discovered_by = COALESCE(
                    discovered_by,
                    CASE
                        WHEN reference_type = ''SEMANTIC''
                        THEN ''KNOWLEDGE_ENTITY''
                        ELSE ''MARKDOWN_PARSER''
                    END
                )
        ';
    END IF;
END $$;

UPDATE knowledge_file_reference
SET relation_code = COALESCE(relation_code, 'MENTIONS'),
    discovered_by = COALESCE(discovered_by, 'MARKDOWN_PARSER');

UPDATE knowledge_file_reference
SET evidence_fingerprint = 'legacy:' || kid::text
WHERE evidence_fingerprint IS NULL;

-- The discriminator draft represented generated semantic relations separately
-- from Markdown links.  Restore their intended stable locators before the
-- discriminator is removed.  Rows from a plain 026 schema use the fallback.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'reference_type'
    ) THEN
        EXECUTE '
            UPDATE knowledge_file_reference
            SET target_locator_type = ''ENTITY_SURFACE'',
                target_locator_value = original_target
            WHERE reference_type = ''SEMANTIC''
        ';

        EXECUTE '
            UPDATE knowledge_file_reference assertion
            SET target_locator_type = ''KB_PATH'',
                target_locator_value = target.virtual_path
            FROM knowledge_fs_entry target
            WHERE assertion.reference_type = ''MARKDOWN''
              AND assertion.target_fs_entry_id = target.kid
        ';

        EXECUTE '
            UPDATE knowledge_file_reference
            SET target_locator_type = ''KB_PATH'',
                target_locator_value = target_path
            WHERE reference_type = ''MARKDOWN''
              AND target_fs_entry_id IS NULL
        ';
    ELSE
        UPDATE knowledge_file_reference assertion
        SET target_locator_type = 'KB_PATH',
            target_locator_value = target.virtual_path
        FROM knowledge_fs_entry target
        WHERE assertion.target_fs_entry_id = target.kid
          AND (
              assertion.target_locator_type IS NULL
              OR assertion.target_locator_value IS NULL
          );

        UPDATE knowledge_file_reference
        SET target_locator_type = 'KB_PATH',
            target_locator_value = target_path
        WHERE target_fs_entry_id IS NULL
          AND (
              target_locator_type IS NULL
              OR target_locator_value IS NULL
          );
    END IF;
END $$;

ALTER TABLE knowledge_file_reference
    ALTER COLUMN relation_code SET DEFAULT 'MENTIONS';

ALTER TABLE knowledge_file_reference
    ALTER COLUMN relation_code SET NOT NULL;

ALTER TABLE knowledge_file_reference
    ALTER COLUMN discovered_by SET DEFAULT 'MARKDOWN_PARSER';

ALTER TABLE knowledge_file_reference
    ALTER COLUMN discovered_by SET NOT NULL;

ALTER TABLE knowledge_file_reference
    ALTER COLUMN evidence_fingerprint SET NOT NULL;

ALTER TABLE knowledge_file_reference
    ALTER COLUMN target_locator_type SET NOT NULL;

ALTER TABLE knowledge_file_reference
    ALTER COLUMN target_locator_value SET NOT NULL;

-- openGauss A compatibility treats '' as NULL.  Historical 026 declared this
-- field NOT NULL even though an empty suffix is the normal no-fragment value.
ALTER TABLE knowledge_file_reference
    ALTER COLUMN target_suffix DROP NOT NULL;

-- Remove discriminator-draft indexes before dropping their referenced column.
DROP INDEX IF EXISTS uq_kfr_semantic_relation;
DROP INDEX IF EXISTS idx_kfr_semantic_source;
DROP INDEX IF EXISTS idx_kfr_semantic_target;
DROP INDEX IF EXISTS idx_kfr_semantic_source_task;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'reference_type'
    ) THEN
        ALTER TABLE knowledge_file_reference DROP COLUMN reference_type;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_kfr_relation_code'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_kfr_relation_code
            CHECK (
                relation_code IN ('MENTIONS', 'PART_OF', 'IS_A', 'DEPENDS_ON')
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_kfr_confidence'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_kfr_confidence
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_kfr_source_lines'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_kfr_source_lines
            CHECK (
                (start_line IS NULL AND end_line IS NULL)
                OR
                (
                    start_line IS NOT NULL
                    AND end_line IS NOT NULL
                    AND start_line >= 1
                    AND end_line >= start_line
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_kfr_source_offsets'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_kfr_source_offsets
            CHECK (
                (start_offset IS NULL AND end_offset IS NULL)
                OR
                (
                    start_offset IS NOT NULL
                    AND end_offset IS NOT NULL
                    AND start_offset >= 0
                    AND end_offset >= start_offset
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_kfr_target_locator'
          AND conrelid = 'knowledge_file_reference'::regclass
    ) THEN
        ALTER TABLE knowledge_file_reference
            ADD CONSTRAINT chk_kfr_target_locator
            CHECK (
                target_locator_type IN (
                    'KB_PATH',
                    'ENTITY_SURFACE',
                    'FS_ENTRY_ID'
                )
                AND btrim(target_locator_value) <> ''
            );
    END IF;

END $$;

-- A short-lived unified draft normalized NULL producer_run_id with ''.  In
-- openGauss A compatibility that expression remains NULL and cannot enforce
-- uniqueness.  Rebuild only that stale definition; keep a healthy index stable
-- on ordinary repeated bootstrap runs.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = current_schema()
          AND indexname = 'uq_kfr_exact_assertion'
          AND indexdef NOT LIKE '%__NO_RUN__%'
    ) THEN
        DROP INDEX uq_kfr_exact_assertion;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_kfr_exact_assertion
    ON knowledge_file_reference (
        knowledge_base_id,
        source_fs_entry_id,
        relation_code,
        target_locator_type,
        target_locator_value,
        discovered_by,
        COALESCE(producer_run_id, '__NO_RUN__'),
        evidence_fingerprint
    );

CREATE INDEX IF NOT EXISTS idx_kfr_relation_source
    ON knowledge_file_reference (
        knowledge_base_id,
        source_fs_entry_id,
        relation_code,
        target_fs_entry_id,
        kid
    );

CREATE INDEX IF NOT EXISTS idx_kfr_relation_target
    ON knowledge_file_reference (
        knowledge_base_id,
        target_fs_entry_id,
        relation_code,
        source_fs_entry_id,
        kid
    );

CREATE INDEX IF NOT EXISTS idx_kfr_producer_outgoing
    ON knowledge_file_reference (
        knowledge_base_id,
        source_fs_entry_id,
        discovered_by,
        producer_run_id,
        kid
    );

CREATE INDEX IF NOT EXISTS idx_kfr_source_task
    ON knowledge_file_reference (source_task_id, kid)
    WHERE source_task_id IS NOT NULL;
