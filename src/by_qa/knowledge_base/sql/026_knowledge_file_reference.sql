CREATE TABLE IF NOT EXISTS knowledge_file_reference (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid),
    source_fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid),
    target_fs_entry_id bigint NULL REFERENCES knowledge_fs_entry(kid),
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
