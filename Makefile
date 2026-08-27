# MegooPM shortcuts. Thin wrappers over `docker compose`.
#   make help          this list
#   make up / down …   the DEVELOPMENT stack (docker-compose.dev.yml)
#   make prod-*        production, single node (docker-compose.yml)
#   make ha-*          production, this node of a cluster (docker-compose.ha.yml)

COMPOSE      := docker compose -f docker-compose.dev.yml
COMPOSE_PROD := docker compose -f docker-compose.yml
COMPOSE_HA   := docker compose -f docker-compose.ha.yml

.DEFAULT_GOAL := help
.PHONY: help up up-fg down clean build ps logs migrate shell psql redis-cli \
        prod-up prod-down prod-logs ha-up ha-down ha-logs

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Dev: build and start the stack with hot reload (background)
	$(COMPOSE) up --build -d --remove-orphans

up-fg: ## Dev: same, in the foreground (streams logs)
	$(COMPOSE) up --build --remove-orphans

down: ## Dev: stop and remove containers (volumes are kept)
	$(COMPOSE) down --remove-orphans

clean: ## Dev: stop and remove containers AND named volumes (wipes DB + certs)
	$(COMPOSE) down -v --remove-orphans

build: ## Dev: rebuild all images
	$(COMPOSE) build

ps: ## Dev: show service status and health
	$(COMPOSE) ps

logs: ## Dev: follow logs (make logs s=backend for one service)
	$(COMPOSE) logs -f $(s)

migrate: ## Dev: apply DB migrations against the running backend
	$(COMPOSE) exec backend alembic upgrade head

shell: ## Dev: open a shell in the backend container
	$(COMPOSE) exec backend bash

psql: ## Dev: open a psql session against the dev database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-megoopm} -d $${POSTGRES_DB:-megoopm}

redis-cli: ## Dev: open a redis-cli session
	$(COMPOSE) exec redis redis-cli

prod-up: ## Prod (single node): build and start (needs a filled-in .env)
	$(COMPOSE_PROD) up --build -d

prod-down: ## Prod (single node): stop (volumes are kept)
	$(COMPOSE_PROD) down

prod-logs: ## Prod (single node): follow logs (s=service)
	$(COMPOSE_PROD) logs -f $(s)

ha-up: ## HA (this node): build and start (needs this node's .env, see .env.ha.example)
	$(COMPOSE_HA) up --build -d

ha-down: ## HA (this node): stop
	$(COMPOSE_HA) down

ha-logs: ## HA (this node): follow logs (s=service)
	$(COMPOSE_HA) logs -f $(s)
