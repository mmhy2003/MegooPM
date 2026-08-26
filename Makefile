# MegooPM developer shortcuts. Thin wrappers over `docker compose`.
# Run `make help` for the list.

COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help up up-fg down clean build ps logs migrate shell psql redis-cli

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the full stack in the background
	$(COMPOSE) up --build -d

up-fg: ## Build and start the full stack in the foreground (streams logs)
	$(COMPOSE) up --build

down: ## Stop and remove containers (volumes are kept)
	$(COMPOSE) down

clean: ## Stop and remove containers AND named volumes (wipes DB + certs)
	$(COMPOSE) down -v

build: ## Rebuild all images
	$(COMPOSE) build

ps: ## Show service status and health
	$(COMPOSE) ps

logs: ## Follow logs from all services (make logs s=backend for one)
	$(COMPOSE) logs -f $(s)

migrate: ## Apply DB migrations against the running backend
	$(COMPOSE) exec backend alembic upgrade head

shell: ## Open a shell in the backend container
	$(COMPOSE) exec backend bash

psql: ## Open a psql session against the dev database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-megoopm} -d $${POSTGRES_DB:-megoopm}

redis-cli: ## Open a redis-cli session
	$(COMPOSE) exec redis redis-cli
