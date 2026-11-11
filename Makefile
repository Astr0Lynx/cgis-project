# Task shortcuts; each target is a thin wrapper around `docker compose` (raw commands in README.md).
COMPOSE ?= docker compose
BASE_URL ?= http://localhost:8080

.DEFAULT_GOAL := help
.PHONY: help env config build up down restart ps logs migrate fixtures import seed smoke e2e test-modules bench reset

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create .env from .env.example with random local passwords (never overwrites)
	@sh infra/scripts/init-env.sh

config: ## Validate the resolved Compose configuration
	$(COMPOSE) config --quiet && echo "compose config OK"

build: ## Build all images, including the ingestion tool image
	@[ -f .env ] || { echo "missing .env (run: make env)"; exit 1; }
	$(COMPOSE) --profile tools build

up: build ## Build and start the stack, waiting for health
	$(COMPOSE) up -d --wait

down: ## Stop the stack (keeps the database, assets and outputs volumes)
	$(COMPOSE) down

restart: ## Recreate all containers without deleting volumes
	$(COMPOSE) down
	$(COMPOSE) up -d --wait

ps: ## Show service status and health
	$(COMPOSE) ps -a

logs: ## Follow logs (SERVICE=name to select one)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

migrate: ## Apply database migrations and service roles (idempotent)
	$(COMPOSE) run --rm migrate

fixtures: ## Generate the SYNTHETIC fixtures into ./data/fixtures
	$(COMPOSE) run --rm ingestion generate-fixtures --out-dir /data/fixtures

import: ## Import ./data/fixtures/manifest.json (MANIFEST=/data/... to override)
	$(COMPOSE) run --rm ingestion import --manifest $(or $(MANIFEST),/data/fixtures/manifest.json)

seed: ## Generate and import the SYNTHETIC fixtures (idempotent)
	$(COMPOSE) run --rm ingestion seed

smoke: ## Check every gateway route reaches a ready service
	@sh infra/scripts/smoke.sh $(BASE_URL)

e2e: ## Full end-to-end acceptance run (ARGS="--qgis --browser" optional)
	sh tests/run.sh e2e $(ARGS)

test-modules: ## Every module's own test suite on a throwaway stack (ARGS="--qgis --cite" optional)
	sh tests/run.sh modules $(ARGS)

bench: ## National-scale benchmark on a separate project cgis-bench (~15 GB disk; results in data/bench/)
	sh tests/bench/run_national_bench.sh

reset: ## DESTROY all volumes (database, assets, outputs, app staging/exports); requires CONFIRM=1
	@[ "$(CONFIRM)" = "1" ] || { echo "refusing: this deletes all data volumes; run: make reset CONFIRM=1"; exit 1; }
	$(COMPOSE) down -v
