CREATE TABLE IF NOT EXISTS knowledge_item (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL UNIQUE REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    item_code varchar(255) NOT NULL,
    current_version_id bigint,
    source_code varchar(64) NOT NULL,
    type_code varchar(64) NOT NULL,
    title varchar(512) NOT NULL,
    status varchar(32) NOT NULL DEFAULT 'ACTIVE',
    is_deleted boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_item_item_code
        UNIQUE (knowledge_base_id, item_code),
    CONSTRAINT chk_knowledge_item_status
        CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
