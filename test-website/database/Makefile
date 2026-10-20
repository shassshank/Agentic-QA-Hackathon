COMPOSE := docker compose
PSQL := $(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U demobank -d demobank

.NOTPARALLEL:
.PHONY: up migrate seed reset down destroy

up:
	$(COMPOSE) up -d --wait

migrate:
	$(PSQL) < migrations/001_create_schema.sql

seed:
	$(PSQL) < seed.sql

reset: migrate seed

down:
	$(COMPOSE) down

destroy:
	$(COMPOSE) down --volumes
