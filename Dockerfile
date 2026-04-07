FROM python:3.12-slim

ARG APP_BY_QA_PORT=8000
ENV APP_BY_QA_PORT=${APP_BY_QA_PORT}

WORKDIR /app

ENV PORT=${APP_BY_QA_PORT} \
    HOST=0.0.0.0

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN set -e; \
    uv --version; \
    uv pip install --system --no-cache -e .

COPY scripts/entrypoint.sh ./

RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    mkdir ./logs && \
    chown -R appuser:appuser ./logs && \
    chmod +x ./entrypoint.sh

USER appuser

EXPOSE ${APP_BY_QA_PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${APP_BY_QA_PORT}/health')" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
