# CyberGIS Phase 2 task commands (owner: Agent 1). Each target is a thin wrapper around
# `docker compose`; the equivalent raw commands are listed in infra/README.md for hosts without make.

COMPOSE ?= docker compose
BASE_URL ?= http://localhost:8080
MODULES := database/pyproject.toml ingestion/Dockerfile ogc-services/tiler/Dockerfile \
           ogc-services/pygeoapi/Dockerfile spatial-processing/Dockerfile

.DEFAULT_GOAL := help
.PHONY: help env check-modules config build up down ps logs migrate fixtures import seed smoke restart reset

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create .env from .env.example with random local passwords (never overwrites)
	@sh infra/scripts/init-env.sh

check-modules: ## Fail clearly if a service module has not been merged yet
	@missing=0; for f in $(MODULES); do \
	  if [ ! -f "$$f" ]; then echo "missing $$f (module branch not merged yet)"; missing=1; fi; done; \
	  if [ ! -f .env ]; then echo "missing .env (run: make env)"; missing=1; fi; \
	  [ $$missing -eq 0 ] || { echo "cannot build the full stack yet"; exit 1; }

config: ## Validate and print the resolved Compose configuration
	$(COMPOSE) config --quiet && echo "compose config OK"

build: check-modules ## Build all images, including the ingestion tool image
	$(COMPOSE) --profile tools build

up: check-modules ## Build and start the stack, waiting until every service is healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (keeps volumes: database, assets, outputs)
	$(COMPOSE) down

restart: ## Restart all services without deleting volumes
	$(COMPOSE) restart

ps: ## Show service status and health
	$(COMPOSE) ps -a

logs: ## Follow logs (SERVICE=name to select one)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

migrate: ## Apply database migrations and service roles (idempotent)
	$(COMPOSE) run --rm migrate

fixtures: ## Generate the SYNTHETIC fixtures into ./data/fixtures
	$(COMPOSE) run --rm ingestion generate-fixtures --out-dir /data/fixtures

import: ## Import ./data/fixtures/manifest.json (MANIFEST=/data/fixtures/<file> to override)
	$(COMPOSE) run --rm ingestion import --manifest $(or $(MANIFEST),/data/fixtures/manifest.json)

seed: ## Generate and import the SYNTHETIC fixtures (idempotent)
	$(COMPOSE) run --rm ingestion seed

smoke: ## Check every gateway route reaches a ready service
	@sh infra/scripts/smoke.sh $(BASE_URL)

reset: ## DESTROY all volumes (database, assets, outputs); requires CONFIRM=1
	@[ "$(CONFIRM)" = "1" ] || { echo "refusing: this deletes all data volumes; run: make reset CONFIRM=1"; exit 1; }
	$(COMPOSE) down -v
