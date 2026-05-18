CREATE TABLE IF NOT EXISTS knowledge_file_metadata_value (
    kid bigserial PRIMARY KEY,
    fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    property_def_id bigint NOT NULL REFERENCES knowledge_metadata_property_def(kid),
    value_string text,
    value_number numeric(20, 6),
    value_boolean boolean,
    value_datetime timestamptz,
    value_string_list jsonb,
    is_deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW()
);
