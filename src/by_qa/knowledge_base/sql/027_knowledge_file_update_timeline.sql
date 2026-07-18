CREATE TABLE IF NOT EXISTS knowledge_file_update_timeline (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    event_type text NOT NULL DEFAULT 'UPDATE',
    old_checksum text NULL,
    new_checksum text NOT NULL,
    old_file_size bigint NULL,
    new_file_size bigint NOT NULL,
    summary text NOT NULL,
    summary_source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_file_update_timeline_event_type
        CHECK (event_type IN ('UPDATE')),
    CONSTRAINT chk_knowledge_file_update_timeline_summary_source
        CHECK (summary_source IN ('RULE_BASED', 'FIXED', 'LLM'))
);

CREATE INDEX IF NOT EXISTS idx_knowledge_file_update_timeline_fs_entry_created
    ON knowledge_file_update_timeline (fs_entry_id, created_at DESC, kid DESC);
