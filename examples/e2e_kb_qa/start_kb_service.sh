#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.kb-stack.yml"
ENV_FILE="${SCRIPT_DIR}/.env"
RUNTIME_DIR_DEFAULT="${PROJECT_ROOT}/examples/e2e_kb_qa/.runtime"
RUNTIME_DIR="${RUNTIME_DIR_DEFAULT}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-dir)
      RUNTIME_DIR="$2"
      shift 2
      ;;
    --project-name)
      COMPOSE_PROJECT_NAME="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: bash examples/e2e_kb_qa/start_kb_service.sh [--runtime-dir PATH] [--project-name NAME]" >&2
      exit 1
      ;;
  esac
done

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Create examples/e2e_kb_qa/.env first." >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

HOST="${HOST:-}"
PORT="${PORT:-}"
AGENT_DATA_PATH="${AGENT_DATA_PATH:-${RUNTIME_DIR}/agent_data}"

OPENGAUSS_PORT="${OPENGAUSS_PORT:-${DB_PORT:-15432}}"
OPENGAUSS_DB="${OPENGAUSS_DB:-${DB_DATABASE:-postgres}}"
OPENGAUSS_APP_USER="${OPENGAUSS_APP_USER:-${DB_USER:-gaussdb}}"
OPENGAUSS_APP_PASSWORD="${OPENGAUSS_APP_PASSWORD:-${DB_PASS:-OpenGauss#2026}}"
MINIO_API_PORT="${MINIO_API_PORT:-19000}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-${MINIO_ACCESS_KEY:-minioadmin}}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-${MINIO_SECRET_KEY:-minioadmin}}"
MINIO_DEFAULT_BUCKET="${MINIO_DEFAULT_BUCKET:-${KB_MINIO_BUCKET:-knowledge-base}}"

required_env_vars=(
  "HOST"
  "PORT"
  "SERVICE_NAME"
  "DB_HOST"
  "DB_PORT"
  "DB_DATABASE"
  "DB_USER"
  "DB_PASS"
  "MINIO_ENDPOINT"
  "MINIO_ACCESS_KEY"
  "MINIO_SECRET_KEY"
  "KB_MINIO_BUCKET"
  "KB_MINIO_MARKDOWN_BUCKET"
  "MINIO_SECURE"
  "EMBEDDING_BASE_URL"
  "EMBEDDING_API_KEY"
  "EMBEDDING_MODEL_NAME"
  "EMBEDDING_DIMENSION"
  "REDIS_HOST"
  "REDIS_PORT"
  "REDIS_DATABASE"
)

for key in "${required_env_vars[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    echo "Missing required environment variable: ${key}" >&2
    exit 1
  fi
done

mkdir -p "${RUNTIME_DIR}/agent_data"

export HOST
export PORT
export SERVICE_NAME
export HOST_MACHINE
export REDIS_HOST
export REDIS_PORT
export REDIS_USERNAME
export REDIS_PASSWORD
export REDIS_DATABASE
export AGENT_DATA_PATH
export DB_HOST
export DB_PORT
export DB_DATABASE
export DB_SCHEMA
export DB_USER
export DB_PASS
export MINIO_ENDPOINT
export MINIO_ACCESS_KEY
export MINIO_SECRET_KEY
export KB_MINIO_BUCKET
export KB_MINIO_MARKDOWN_BUCKET
export MINIO_SECURE
export BY_QA_MODEL_CONFIG_PROVIDER
export EMBEDDING_MODEL_NAME
export EMBEDDING_BASE_URL
export EMBEDDING_API_KEY
export EMBEDDING_DIMENSION
export EMBEDDING_DISTANCE_METRIC
export EMBEDDING_BATCH_MAX_TEXTS
export OPENGAUSS_PORT
export OPENGAUSS_DB
export OPENGAUSS_APP_USER
export OPENGAUSS_APP_PASSWORD
export MINIO_API_PORT
export MINIO_ROOT_USER
export MINIO_ROOT_PASSWORD
export MINIO_DEFAULT_BUCKET
export COMPOSE_PROJECT_NAME

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

echo "Starting openGauss and MinIO from ${COMPOSE_FILE}..."
docker compose -f "${COMPOSE_FILE}" up -d --build opengauss minio redis

echo "Running stack initialization..."
docker compose -f "${COMPOSE_FILE}" --profile init up --abort-on-container-exit opengauss-init minio-init

echo "Starting by-qa service at http://${HOST}:${PORT}"
echo "Keep this process running, then use the other two example scripts in a separate terminal."

cd "${PROJECT_ROOT}"
python -m by_qa.main
