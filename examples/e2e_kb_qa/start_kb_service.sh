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

required_env_vars=(
  "HOST"
  "PORT"
  "EMBEDDING_BASE_URL"
  "EMBEDDING_API_KEY"
  "EMBEDDING_MODEL_NAME"
  "EMBEDDING_DIMENSION"
  "KB_OPENGAUSS_DSN"
  "KB_MINIO_ENDPOINT"
  "KB_MINIO_ACCESS_KEY"
  "KB_MINIO_SECRET_KEY"
  "KB_MINIO_BUCKET"
  "KB_MINIO_MARKDOWN_BUCKET"
  "KB_MINIO_SECURE"
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
export AGENT_DATA_PATH
export COMPOSE_PROJECT_NAME

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required but was not found in PATH." >&2
  exit 1
fi

echo "Starting openGauss and MinIO from ${COMPOSE_FILE}..."
docker compose -f "${COMPOSE_FILE}" up -d --build opengauss minio

echo "Running stack initialization..."
docker compose -f "${COMPOSE_FILE}" --profile init up --abort-on-container-exit opengauss-init minio-init

echo "Starting by-qa service at http://${HOST}:${PORT}"
echo "Keep this process running, then use the other two example scripts in a separate terminal."

cd "${PROJECT_ROOT}"
python -m by_qa.main
