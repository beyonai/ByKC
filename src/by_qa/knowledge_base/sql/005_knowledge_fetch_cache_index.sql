CREATE TABLE IF NOT EXISTS knowledge_fetch_cache_index (
    kid bigserial PRIMARY KEY,
    knowledge_base_id bigint NOT NULL REFERENCES knowledge_base(kid) ON DELETE CASCADE,
    fs_entry_id bigint NOT NULL UNIQUE REFERENCES knowledge_fs_entry(kid) ON DELETE CASCADE,
    full_path text NOT NULL,
    bucket_name varchar(128) NOT NULL,
    object_key varchar(1024) NOT NULL,
    checksum varchar(128),
    cache_file_path text NOT NULL UNIQUE,
    file_size bigint,
    cache_ttl_seconds integer NOT NULL DEFAULT 86400,
    first_cached_at timestamptz NOT NULL DEFAULT NOW(),
    last_cached_at timestamptz NOT NULL DEFAULT NOW(),
    last_accessed_at timestamptz NOT NULL DEFAULT NOW(),
    expires_at timestamptz NOT NULL,
    cache_status varchar(32) NOT NULL DEFAULT 'READY',
    evict_retry_count integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT NOW(),
    updated_at timestamptz NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_knowledge_fetch_cache_index_status
        CHECK (cache_status IN ('READY', 'EVICTING', 'ERROR'))
);
