#!/bin/bash

set -e
set -a

ENV_FILE="$(pwd)/.env"
echo "ENV_FILE: $ENV_FILE"

if [ -f "$ENV_FILE" ]; then
  # The .env file is project-owned configuration, so we can source it directly.
  . "$ENV_FILE"
fi

set +a

PORT=${APP_BY_QA_PORT:-${APP_BYAI_KNOWLEDGE_RESEARCH_PORT:-${PORT:-8000}}}
HOST=${HOST:-0.0.0.0}
ENTRYPOINT_MODE=${BY_QA_ENTRYPOINT_MODE:-${BYAI_ENTRYPOINT_MODE:-api}}

case "$ENTRYPOINT_MODE" in
  api)
    CMD=(uvicorn by_qa.main:create_app --factory --host "$HOST" --port "$PORT")
    ;;
  worker)
    CMD=(python -m by_qa.workers.instant_search_worker)
    ;;
  *)
    echo "Unsupported BY_QA_ENTRYPOINT_MODE: $ENTRYPOINT_MODE"
    echo "Supported values: api, worker"
    exit 1
    ;;
esac

echo "Starting mode: $ENTRYPOINT_MODE"
echo "Command: ${CMD[*]}"

if [ "${ENTRYPOINT_DRY_RUN:-0}" = "1" ]; then
  exit 0
fi

exec "${CMD[@]}"
