CREATE TABLE IF NOT EXISTS knowledge_file_reference (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    original_target text NOT NULL,
    target_path text NULL,
    target_suffix text NOT NULL DEFAULT '',
    target_kind text NOT NULL DEFAULT 'FILE',
    status text NOT NULL,
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
        CHECK (target_kind IN ('FILE'))
);

CREATE INDEX IF NOT EXISTS idx_kfr_source
    ON knowledge_file_reference (source_fs_entry_id);

CREATE INDEX IF NOT EXISTS idx_kfr_pending_path
    ON knowledge_file_reference (knowledge_base_id, target_path)
    WHERE target_fs_entry_id IS NULL
      AND status IN ('unresolved', 'broken');

CREATE INDEX IF NOT EXISTS idx_kfr_target
    ON knowledge_file_reference (target_fs_entry_id);
