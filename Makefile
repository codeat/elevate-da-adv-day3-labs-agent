# ============================================================================
# Cymbal Retail Operations Coordinator Agent - operational entrypoints
#
# Every target is environment-driven. Load your local config first:
#     cp .env.example app/.env && $(EDITOR) app/.env
# ============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ---- Environment resolution (env wins, then app/.env, then safe defaults) ---
ifneq (,$(wildcard app/.env))
include app/.env
export
endif

PROJECT_ID        ?= $(shell gcloud config get-value project 2>/dev/null)
REGION            ?= us-central1
BIGTABLE_INSTANCE ?= operations-db
MCP_SERVICE       ?= mcp-toolbox-bigtable
MCP_SECRET        ?= bigtable-mcp-tools-secret
MCP_IMAGE         ?= us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest
AGENT_SA          ?= cymbal-sa-data@$(PROJECT_ID).iam.gserviceaccount.com
VENV              ?= .venv
PY                := $(VENV)/bin/python
PIP               := $(VENV)/bin/pip

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup ----
.PHONY: install
install: ## Create the virtualenv and install runtime + dev dependencies
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

.PHONY: env-check
env-check: ## Fail fast if mandatory environment properties are missing
	@test -n "$(PROJECT_ID)" || { echo "❌ PROJECT_ID is not set (see .env.example)"; exit 1; }
	@test -n "$(BIGTABLE_MCP_URL)" || { echo "❌ BIGTABLE_MCP_URL is not set (run 'make mcp-deploy')"; exit 1; }
	@echo "✅ Environment OK  project=$(PROJECT_ID) region=$(REGION)"

# ----------------------------------------------------------- quality -------
.PHONY: lint
lint: ## Run ruff lint + format checks
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests

.PHONY: format
format: ## Auto-format the codebase
	$(VENV)/bin/ruff format app tests
	$(VENV)/bin/ruff check --fix app tests

.PHONY: test
test: ## Run the offline unit test suite
	$(VENV)/bin/pytest tests/unit -v

.PHONY: eval
eval: env-check ## Run the ADK evaluation suite against the golden datasets
	$(VENV)/bin/agents-cli eval run \
	  --dataset tests/eval/datasets/basic-dataset.json \
	  --metrics tool_use_quality,grounding

.PHONY: check
check: lint test ## Gate: lint + unit tests (run before every commit)

# ------------------------------------------------------------- runtime -----
.PHONY: run
run: env-check ## Launch the ADK web playground on :8000
	$(VENV)/bin/adk web --host 0.0.0.0 --port 8000 .

# ---------------------------------------------------------- containers -----
.PHONY: docker-build
docker-build: ## Build the agent container image
	docker build -t cymbal-operations-agent:local .

.PHONY: docker-run
docker-run: env-check ## Run the agent container locally with mounted ADC
	docker run --rm -p 8000:8000 \
	  -e PROJECT_ID=$(PROJECT_ID) \
	  -e REGION=$(REGION) \
	  -e BIGTABLE_MCP_URL=$(BIGTABLE_MCP_URL) \
	  -v $$HOME/.config/gcloud:/home/agent/.config/gcloud:ro \
	  cymbal-operations-agent:local

# --------------------------------------------------------------- infra -----
.PHONY: tf-init
tf-init: ## Initialize Terraform
	cd deploy/terraform && terraform init

.PHONY: tf-plan
tf-plan: env-check ## Plan the infrastructure changes
	cd deploy/terraform && terraform plan \
	  -var="project_id=$(PROJECT_ID)" -var="region=$(REGION)"

.PHONY: tf-apply
tf-apply: env-check ## Apply the infrastructure changes
	cd deploy/terraform && terraform apply -auto-approve \
	  -var="project_id=$(PROJECT_ID)" -var="region=$(REGION)"

# ----------------------------------------------------------------- mcp -----
.PHONY: mcp-config
mcp-config: env-check ## Render tools.yaml with the active environment
	@mkdir -p build
	@PROJECT_ID=$(PROJECT_ID) BIGTABLE_INSTANCE=$(BIGTABLE_INSTANCE) \
	  envsubst < tools.yaml > build/tools.rendered.yaml
	@echo "✅ Rendered -> build/tools.rendered.yaml"

.PHONY: mcp-deploy
mcp-deploy: mcp-config ## Publish tools.yaml to Secret Manager and deploy Cloud Run
	-gcloud secrets create $(MCP_SECRET) --project=$(PROJECT_ID) --replication-policy=automatic
	gcloud secrets versions add $(MCP_SECRET) --project=$(PROJECT_ID) \
	  --data-file=build/tools.rendered.yaml
	gcloud run deploy $(MCP_SERVICE) --project=$(PROJECT_ID) --region=$(REGION) \
	  --image=$(MCP_IMAGE) --service-account=$(AGENT_SA) --no-allow-unauthenticated \
	  --set-secrets=/app/tools.yaml=$(MCP_SECRET):latest \
	  --args="--tools-file=/app/tools.yaml,--address=0.0.0.0,--port=8080"
	@echo "➡️  Export the printed URL as BIGTABLE_MCP_URL in app/.env"

# -------------------------------------------------------------- deploy -----
.PHONY: deploy
deploy: check env-check ## Deploy the agent to Vertex AI Agent Runtime
	$(VENV)/bin/agents-cli deploy agent_runtime \
	  --project=$(PROJECT_ID) --region=$(REGION) \
	  --service-name=cymbal_operations_agent \
	  --service-account=$(AGENT_SA)

.PHONY: clean
clean: ## Remove build artefacts and caches
	rm -rf build .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
