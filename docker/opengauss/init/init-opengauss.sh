#!/usr/bin/env bash

set -euo pipefail

set +u
source /home/omm/.bashrc >/dev/null 2>&1 || true
source /etc/profile >/dev/null 2>&1 || true
set -u

host="${OPENGAUSS_HOST:-opengauss}"
port="${OPENGAUSS_PORT:-5432}"
db="${OPENGAUSS_DB:-postgres}"
user="${OPENGAUSS_APP_USER:-gaussdb}"
password="${OPENGAUSS_APP_PASSWORD:-OpenGauss#2026}"
graph_name="${OPENGAUSS_GRAPH_NAME:-knowledge_graph}"
gsql_bin="${GSQL_BIN:-gsql}"

run_gsql() {
  "$gsql_bin" -h "$host" -p "$port" -d "$db" -U "$user" -W "$password" "$@"
}

require_available_extension() {
  local extension_name="$1"
  local available_extension

  available_extension="$(run_gsql -At -c "SELECT name FROM pg_available_extensions WHERE name = '${extension_name}';")"
  if [ "$available_extension" != "$extension_name" ]; then
    echo "Required extension ${extension_name} is not available in this image." >&2
    echo "Expected the custom openGauss image to compile and install ${extension_name} during docker build." >&2
    exit 1
  fi
}

echo "Waiting for openGauss at ${host}:${port}/${db}..."
for _ in $(seq 1 30); do
  if run_gsql -At -c "SELECT 1;" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

run_gsql -At -c "SELECT 1;" >/dev/null

thread_pool="$(run_gsql -At -c "SHOW enable_thread_pool;")"
if [ "$thread_pool" != "off" ]; then
  echo "AGE requires enable_thread_pool=off, but current value is: ${thread_pool}" >&2
  exit 1
fi

require_available_extension "ltree"
require_available_extension "pg_trgm"
require_available_extension "age"

vector_type="$(run_gsql -At -c "SELECT typname FROM pg_type WHERE typname = 'vector';")"
if [ "$vector_type" != "vector" ]; then
  echo "Built-in vector type is unavailable; DataVec capability is not ready." >&2
  exit 1
fi

run_gsql <<SQL
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS age;

CREATE SCHEMA IF NOT EXISTS kb_bootstrap;

DROP TABLE IF EXISTS kb_bootstrap.ltree_smoke;
CREATE TABLE kb_bootstrap.ltree_smoke (
    path ltree PRIMARY KEY
);

TRUNCATE TABLE kb_bootstrap.ltree_smoke;
INSERT INTO kb_bootstrap.ltree_smoke (path)
VALUES
    ('kb_root.section_a'),
    ('kb_root.section_b');

DROP TABLE IF EXISTS kb_bootstrap.pg_trgm_smoke;
CREATE TABLE kb_bootstrap.pg_trgm_smoke (
    id integer PRIMARY KEY,
    content text NOT NULL
);

TRUNCATE TABLE kb_bootstrap.pg_trgm_smoke;
INSERT INTO kb_bootstrap.pg_trgm_smoke (id, content)
VALUES
    (1, 'knowledge base retrieval'),
    (2, 'knowledge graph extension');

CREATE INDEX IF NOT EXISTS pg_trgm_smoke_content_idx
ON kb_bootstrap.pg_trgm_smoke
USING gin (content gin_trgm_ops);

DROP TABLE IF EXISTS kb_bootstrap.vector_smoke;
CREATE TABLE kb_bootstrap.vector_smoke (
    id integer PRIMARY KEY,
    embedding vector(3) NOT NULL
);

INSERT INTO kb_bootstrap.vector_smoke (id, embedding)
VALUES
    (1, '[1,2,3]'),
    (2, '[2,3,4]'),
    (3, '[3,4,5]');

CREATE INDEX IF NOT EXISTS vector_smoke_embedding_idx
ON kb_bootstrap.vector_smoke
USING ivfflat (embedding vector_l2_ops)
WITH (lists = 4);

LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT ag_catalog.create_graph('${graph_name}')
WHERE NOT EXISTS (
    SELECT 1 FROM ag_catalog.ag_graph WHERE name = '${graph_name}'
);
SQL

ltree_smoke="$(run_gsql -At -c "SELECT COUNT(*) FROM kb_bootstrap.ltree_smoke WHERE path <@ 'kb_root'::ltree;")"
if [ "$ltree_smoke" != "2" ]; then
  echo "ltree smoke test failed: expected 2 rows, got ${ltree_smoke}" >&2
  exit 1
fi

trgm_smoke="$(run_gsql -At -c "SELECT COUNT(*) FROM kb_bootstrap.pg_trgm_smoke WHERE content % 'knowledge retrival';")"
if [ -z "$trgm_smoke" ] || [ "$trgm_smoke" = "0" ]; then
  echo "pg_trgm smoke test failed: similarity operator returned no rows" >&2
  exit 1
fi

echo "openGauss bootstrap completed: ltree + pg_trgm + vector + age are initialized."
