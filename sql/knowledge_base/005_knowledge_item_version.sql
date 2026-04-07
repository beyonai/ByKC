CREATE TABLE IF NOT EXISTS knowledge_item_version (
    kid bigserial PRIMARY KEY,
    knowledge_item_id bigint NOT NULL REFERENCES knowledge_item(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    version varchar(64) NOT NULL,
    bucket_name varchar(128) NOT NULL,
    object_key varchar(1024) NOT NULL,
    markdown_bucket_name varchar(128),
    markdown_object_key varchar(1024),
    markdown_file_size bigint,
    markdown_checksum varchar(128),
    file_size bigint NOT NULL DEFAULT 0,
    checksum varchar(128),
    mime_type varchar(128),
    line_count integer,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_knowledge_item_version
        UNIQUE (knowledge_item_id, version)
);
