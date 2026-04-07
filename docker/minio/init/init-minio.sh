#!/bin/sh

set -eu

endpoint="${MINIO_ENDPOINT:-http://minio:9000}"
bucket="${MINIO_DEFAULT_BUCKET:-knowledge-base}"

echo "Waiting for MinIO at ${endpoint}..."
for _ in $(seq 1 30); do
  if mc alias set local "${endpoint}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

mc alias set local "${endpoint}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null
mc mb --ignore-existing "local/${bucket}" >/dev/null
mc anonymous set private "local/${bucket}" >/dev/null

echo "MinIO bootstrap completed: bucket ${bucket} is ready."
