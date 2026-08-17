CREATE TABLE IF NOT EXISTS knowledge_file_reference (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid) ON DELETE RESTRICT,
    original_target text NOT NULL,
    target_path text NULL,
    target_suffix text NOT NULL DEFAULT '',
    target_kind text NOT NULL DEFAULT 'FILE',
    status text NOT NULL,
    reference_type varchar(16) NOT NULL DEFAULT 'MARKDOWN',
    relation_code varchar(32) NULL,
    confidence numeric(5,4) NULL,
    discovered_by varchar(32) NULL,
    definition_version varchar(64) NULL,
    source_task_id bigint NULL REFERENCES knowledge_build_task(kid) ON DELETE SET NULL,
    last_resolved_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_file_reference_status
        CHECK (status IN ('resolved', 'unresolved', 'broken')),
    CONSTRAINT chk_knowledge_file_reference_state
        CHECK (
            (
                status = 'resolved'
                AND target_fs_entry_id IS NOT NULL
                AND target_path IS NULL
            )
            OR
            (
                status IN ('unresolved', 'broken')
                AND target_fs_entry_id IS NULL
                AND target_path IS NOT NULL
            )
        ),
    CONSTRAINT chk_knowledge_file_reference_target_kind
        CHECK (target_kind IN ('FILE')),
    CONSTRAINT chk_knowledge_file_reference_type
        CHECK (reference_type IN ('MARKDOWN', 'SEMANTIC')),
    CONSTRAINT chk_knowledge_file_reference_semantic_state
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
        ),
    CONSTRAINT chk_knowledge_file_reference_confidence
        CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);

CREATE INDEX IF NOT EXISTS idx_kfr_source
    ON knowledge_file_reference (source_fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_kfr_pending_path
    ON knowledge_file_reference (knowledge_base_id, target_path)
    WHERE target_fs_entry_id IS NULL
      AND status IN ('unresolved', 'broken');

CREATE INDEX IF NOT EXISTS idx_kfr_target
    ON knowledge_file_reference (target_fs_entry_id);

-- On an existing installation 026 is executed before the 030 upgrade. Guard
-- indexes that reference extension columns so bootstrap can reach that migration.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'knowledge_file_reference'
          AND column_name = 'reference_type'
    ) THEN
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
    END IF;
END $$;
