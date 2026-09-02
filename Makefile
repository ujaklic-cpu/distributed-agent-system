.PHONY: install test test-unit test-integration lint format typecheck migrate db-history compose-up compose-down build-tools security-audit image-scan sbom-python sbom-images

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= $(PYTHON) -m ruff
COMPOSE ?= docker compose -f deploy/docker-compose.control.yml
APP_VERSION ?= 7.1
TOOL_VERSION ?= 0.7.0
TRIVY_IMAGE_SCAN_FLAGS ?= --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed

install:
	$(PIP) install -r requirements-dev.txt

test: test-unit

test-unit:
	$(PYTEST) -q -m "not integration"

test-integration:
	RUN_DOCKER_INTEGRATION_TESTS=1 $(PYTEST) -q -m integration tests/integration

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

typecheck:
	$(PYTHON) -m mypy shared orchestrator node_agent agent_server

migrate:
	alembic upgrade head

db-history:
	alembic history

compose-up:
	$(COMPOSE) --env-file .env.test up --build -d

compose-down:
	$(COMPOSE) --env-file .env.test down -v

build-tools:
	$(COMPOSE) build tool-shell-build tool-test-build tool-internet-build tool-filesystem-build tool-build-build tool-analysis-build


security-audit:
	$(PYTHON) -m pip_audit -r orchestrator/requirements.txt -r node-agent/requirements.txt -r agent-server/requirements.txt -r requirements-dev.txt

image-scan:
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/orchestrator-api:$(APP_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/node-agent:$(APP_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/smart-agent:$(APP_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-shell:$(TOOL_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-test:$(TOOL_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-internet:$(TOOL_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-filesystem:$(TOOL_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-build:$(TOOL_VERSION)
	trivy image $(TRIVY_IMAGE_SCAN_FLAGS) local-agent/tool-analysis:$(TOOL_VERSION)

sbom-python:
	cyclonedx-py requirements requirements-dev.txt -o sbom-python.cdx.json

sbom-images:
	syft local-agent/orchestrator-api:$(APP_VERSION) -o cyclonedx-json=sbom-orchestrator-api.cdx.json
	syft local-agent/node-agent:$(APP_VERSION) -o cyclonedx-json=sbom-node-agent.cdx.json
	syft local-agent/smart-agent:$(APP_VERSION) -o cyclonedx-json=sbom-smart-agent.cdx.json
	syft local-agent/tool-shell:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-shell.cdx.json
	syft local-agent/tool-test:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-test.cdx.json
	syft local-agent/tool-internet:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-internet.cdx.json
	syft local-agent/tool-filesystem:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-filesystem.cdx.json
	syft local-agent/tool-build:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-build.cdx.json
	syft local-agent/tool-analysis:$(TOOL_VERSION) -o cyclonedx-json=sbom-tool-analysis.cdx.json
