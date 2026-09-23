# IoT Factory Monitor - convenience targets
COMPOSE ?= docker compose
PYTHON  ?= python3

.PHONY: help env up down clean logs ps config test smoke psql

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

.env:
	cp .env.example .env
	@echo "Created .env from .env.example - change the 'change-me' passwords before exposing the stack."

env: .env ## Create .env from .env.example if missing

up: .env ## Build and start the whole stack
	$(COMPOSE) up -d --build
	@echo "Grafana: http://localhost:$${GRAFANA_PORT:-3000}  (credentials from .env)"

down: ## Stop the stack (keeps data volumes)
	$(COMPOSE) down

clean: ## Stop the stack and DELETE all data volumes
	$(COMPOSE) down -v

logs: ## Follow logs of all services (make logs S=ingestor for one)
	$(COMPOSE) logs -f --tail=100 $(S)

ps: ## Show service status
	$(COMPOSE) ps

config: .env ## Validate docker-compose.yml
	$(COMPOSE) config -q && echo "compose config OK"

test: ## Run the Python unit tests (offline)
	$(PYTHON) -m pytest

smoke: .env ## End-to-end smoke test against a running stack
	./scripts/smoke_test.sh

psql: ## Open psql in the database container
	$(COMPOSE) exec timescaledb sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
