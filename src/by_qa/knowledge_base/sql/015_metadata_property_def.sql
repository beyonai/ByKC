CREATE TABLE IF NOT EXISTS knowledge_metadata_property_def (
    kid bigserial PRIMARY KEY,
    property_name varchar(128) NOT NULL,
    value_type varchar(32) NOT NULL,
    description text,
    ext_params jsonb,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
