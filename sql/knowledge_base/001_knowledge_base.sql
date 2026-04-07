CREATE TABLE IF NOT EXISTS knowledge_base (
    kid bigserial PRIMARY KEY,
    kb_code varchar(64) NOT NULL UNIQUE,
    kb_name varchar(256) NOT NULL,
    kb_description text,
    status varchar(32) NOT NULL DEFAULT 'ACTIVE',
    is_deleted boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    root_entry_id bigint,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_base_status
        CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
