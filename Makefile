COMPOSE_DEV  = docker compose -f compose.yaml -f compose.dev.yaml
COMPOSE_PROD = docker compose -f compose.yaml -f compose.prod.yaml

.PHONY: help up down logs build test lint format check clean secrets

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Development stack
# ---------------------------------------------------------------------------

up: ## Start dev stack (requires secrets — run `make secrets` first)
	$(COMPOSE_DEV) up --build -d

down: ## Stop dev stack
	$(COMPOSE_DEV) down

logs: ## Tail dev logs
	$(COMPOSE_DEV) logs -f

build: ## Build all Docker images
	$(COMPOSE_DEV) build

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

test: ## Run backend tests
	cd backend && uv run pytest

lint: ## Lint backend + frontend
	cd backend && uv run ruff check src tests
	cd frontend && pnpm run check

format: ## Auto-format backend code
	cd backend && uv run ruff format src tests

check: lint test ## Run all quality checks

# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

secrets: ## Generate initial dev secrets (safe to re-run — won't overwrite)
	@mkdir -p secrets
	@test -f secrets/database_password.txt \
		|| openssl rand -base64 32 > secrets/database_password.txt \
		&& echo "Generated secrets/database_password.txt"
	@test -f secrets/database_migrations_password.txt \
		|| openssl rand -base64 32 > secrets/database_migrations_password.txt \
		&& echo "Generated secrets/database_migrations_password.txt"
	@test -f secrets/session_secret.txt \
		|| openssl rand -base64 64 > secrets/session_secret.txt \
		&& echo "Generated secrets/session_secret.txt"
	@test -f secrets/encryption_key.txt \
		|| python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' \
			> secrets/encryption_key.txt \
		&& echo "Generated secrets/encryption_key.txt"
	@chmod 644 secrets/*.txt
	@echo "Done. Secrets are in ./secrets/"
	@echo "Note: files are 644 for Docker Compose bind-mount compatibility."

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove build artifacts and caches
	cd backend && rm -rf .venv .pytest_cache .coverage .coverage-html .ruff_cache .mypy_cache
	cd frontend && rm -rf node_modules .svelte-kit build
	$(COMPOSE_DEV) down -v --remove-orphans 2>/dev/null || true
