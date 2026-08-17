-- Schema migration ledger used to coordinate multi-instance bootstrap.
-- The bootstrap service creates this table under a database advisory lock
-- before it evaluates pending migrations, then records each file atomically
-- with the DDL in that file.
CREATE TABLE IF NOT EXISTS knowledge_schema_migration (
    version varchar(255) PRIMARY KEY,
    checksum varchar(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT NOW()
);
