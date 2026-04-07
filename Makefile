.PHONY: init opengauss-build opengauss-up opengauss-down kb-stack-up kb-stack-down verify-opengauss verify-kb-stack reset-kb-data kb-test-unit kb-test-integration knowledge-build-test-unit qa-test-unit

COMPOSE_FILE ?= docker-compose.kb-stack.yml

init:
	@echo "正在安装项目依赖..."
	uv sync --extra dev
	@echo "正在安装 pre-commit 钩子..."
	uv run pre-commit install
	@echo "✅ 项目初始化完成！"

opengauss-build:
	docker compose -f $(COMPOSE_FILE) build opengauss

opengauss-up:
	docker compose -f $(COMPOSE_FILE) up -d opengauss

opengauss-down:
	docker compose -f $(COMPOSE_FILE) stop opengauss

kb-stack-up:
	docker compose -f $(COMPOSE_FILE) up -d opengauss minio
	docker compose -f $(COMPOSE_FILE) run --rm opengauss-init
	docker compose -f $(COMPOSE_FILE) run --rm minio-init

kb-stack-down:
	docker compose -f $(COMPOSE_FILE) down -v

verify-opengauss:
	PYTHONPATH=. .venv/bin/python scripts/verify_opengauss_checkpointer.py

verify-kb-stack:
	/bin/bash scripts/verify_kb_stack.sh

reset-kb-data:
	/bin/bash scripts/reset_kb_stack.sh

kb-test-unit:
	/bin/bash scripts/knowledge_base/run_unit_tests.sh

kb-test-integration:
	/bin/bash scripts/knowledge_base/run_integration_tests.sh

knowledge-build-test-unit:
	/bin/bash scripts/knowledge_build/run_unit_tests.sh

qa-test-unit:
	/bin/bash scripts/qa/run_unit_tests.sh
