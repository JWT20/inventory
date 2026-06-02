# Local development helpers. See `make help`.
# Production is unaffected: every target below uses docker-compose.dev.yml.

COMPOSE := docker compose -f docker-compose.dev.yml

.PHONY: help dev dev-events down reset migrate seed test logs logs-backend ps

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

dev: ## Start db + backend + frontend with hot reload
	$(COMPOSE) up -d --build --wait db backend frontend
	@echo ""
	@echo "  Frontend → http://localhost:5173"
	@echo "  Backend  → http://localhost:8000  (docs: /docs)"
	@echo "  Login    → admin / devadmin"
	@echo "  Tip: 'make seed' for sample data, 'make logs' to watch."

dev-events: ## Start dev stack + Kafka + Pinot (heavy, ~4GB)
	$(COMPOSE) --profile events up -d --build

down: ## Stop the dev stack (keeps data)
	$(COMPOSE) down

reset: ## Wipe throwaway data, rebuild fresh, and seed
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build --wait db backend frontend
	$(MAKE) seed

migrate: ## Run database migrations (also runs automatically on boot)
	$(COMPOSE) exec backend alembic upgrade head

seed: ## Load sample org / SKUs / customer for clicking around
	$(COMPOSE) exec backend python -m scripts.seed_dev

test: ## Run backend tests (SQLite in-memory, no services needed)
	$(COMPOSE) run --rm --no-deps backend pytest -v

logs: ## Tail all dev logs
	$(COMPOSE) logs -f

logs-backend: ## Tail just the backend log
	$(COMPOSE) logs -f backend

ps: ## Show dev container status
	$(COMPOSE) ps
