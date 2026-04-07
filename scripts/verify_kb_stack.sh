#!/usr/bin/env bash

set -euo pipefail

compose_file="${COMPOSE_FILE:-docker-compose.kb-stack.yml}"
db="${OPENGAUSS_DB:-postgres}"
user="${OPENGAUSS_APP_USER:-gaussdb}"
password="${OPENGAUSS_APP_PASSWORD:-OpenGauss#2026}"
graph_name="${OPENGAUSS_GRAPH_NAME:-knowledge_graph}"
minio_api_port="${MINIO_API_PORT:-19000}"
bucket="${MINIO_DEFAULT_BUCKET:-knowledge-base}"

echo "Checking compose service status..."
docker compose -f "${compose_file}" ps

echo "Checking MinIO health endpoint..."
curl -fsS "http://127.0.0.1:${minio_api_port}/minio/health/live" >/dev/null

echo "Checking AGE extension..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT extname FROM pg_extension WHERE extname = 'age';\""

echo "Checking ltree extension..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT extname FROM pg_extension WHERE extname = 'ltree';\""

echo "Checking pg_trgm extension..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT extname FROM pg_extension WHERE extname = 'pg_trgm';\""

echo "Checking built-in vector capability..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT typname FROM pg_type WHERE typname = 'vector';\""

echo "Checking DataVec vector query..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT id FROM kb_bootstrap.vector_smoke ORDER BY embedding <-> '[1,2,3]' LIMIT 1;\""

echo "Checking ltree query..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT COUNT(*) FROM kb_bootstrap.ltree_smoke WHERE path <@ 'kb_root'::ltree;\""

echo "Checking pg_trgm query..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At -c \"SELECT COUNT(*) FROM kb_bootstrap.pg_trgm_smoke WHERE content % 'knowledge retrival';\""

echo "Checking AGE graph query..."
docker compose -f "${compose_file}" exec -T opengauss \
  /bin/bash -lc "source /home/omm/.bashrc >/dev/null 2>&1 || true; source /etc/profile >/dev/null 2>&1 || true; gsql -h 127.0.0.1 -p 5432 -d '${db}' -U '${user}' -W '${password}' -At" <<SQL
LOAD 'age';
SET search_path = ag_catalog, "\$user", public;
SELECT name FROM ag_catalog.ag_graph WHERE name = '${graph_name}';
SELECT * FROM cypher('${graph_name}', \$\$ RETURN 1 \$\$) AS (n agtype);
SQL

echo "Checking MinIO bucket..."
docker compose -f "${compose_file}" run --rm --entrypoint /bin/sh minio-init \
  -c "mc alias set local http://minio:9000 \"${MINIO_ROOT_USER:-minioadmin}\" \"${MINIO_ROOT_PASSWORD:-minioadmin}\" >/dev/null && mc ls local/${bucket} >/dev/null"

echo "All infrastructure checks passed."
