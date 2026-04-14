CREATE TABLE IF NOT EXISTS knowledge_base (
    kid bigserial PRIMARY KEY,
    kb_name varchar(256) NOT NULL,
    kb_description text,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
