#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

read_env_file_value() {
  local key="$1"

  if [[ ! -f "${ENV_FILE}" ]]; then
    return 0
  fi

  awk -v key="${key}" '
    /^[[:space:]]*#/ { next }
    {
      line = $0
      sub(/\r$/, "", line)
      split(line, parts, "=")
      candidate = parts[1]
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", candidate)
      if (candidate != key) {
        next
      }

      sub(/^[^=]*=/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)

      if (line ~ /^".*"$/ || line ~ /^'\''.*'\''$/) {
        line = substr(line, 2, length(line) - 2)
      }

      print line
      exit
    }
  ' "${ENV_FILE}"
}

export_required_value() {
  local key="$1"
  local value="${!key-}"

  if [[ -z "${value}" ]]; then
    value="$(read_env_file_value "${key}")"
  fi

  if [[ -z "${value}" ]]; then
    echo "Missing required config: ${key}. Set it in the environment or define it in ${ENV_FILE}." >&2
    exit 1
  fi

  export "${key}=${value}"
}

export_optional_value() {
  local key="$1"
  local value="${!key-}"

  if [[ -z "${value}" ]]; then
    value="$(read_env_file_value "${key}")"
  fi

  export "${key}=${value}"
}

required_keys=(
  "DB_HOST"
  "DB_PORT"
  "DB_USER"
  "DB_PASS"
  "MINIO_ENDPOINT"
  "MINIO_ACCESS_KEY"
  "MINIO_SECRET_KEY"
  "KB_MINIO_BUCKET"
  "KB_MINIO_MARKDOWN_BUCKET"
  "MINIO_SECURE"
)

for key in "${required_keys[@]}"; do
  export_required_value "${key}"
done

export_optional_value "DB_SCHEMA"

PYTHONPATH=. .venv/bin/python scripts/reset_kb_data.py
