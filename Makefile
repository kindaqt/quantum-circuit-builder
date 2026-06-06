.PHONY: help bootstrap install run start dev test test-backend test-frontend clean db-up db-down db-logs migrate

# Load .env if present (HOST, PORT, QCB_MAX_*) and export to child processes.
ifneq (,$(wildcard .env))
include .env
export
endif

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
ALEMBIC := $(VENV)/bin/alembic
HOST    ?= 127.0.0.1
PORT    ?= 8533

help:                ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

bootstrap:           ## Install system dependencies via Homebrew (macOS). Run once before anything else.
	@if [ "$$(uname -s)" != "Darwin" ]; then \
		echo "bootstrap targets macOS + Homebrew. On Linux, install docker, python3, and node via your package manager."; \
		exit 1; \
	fi
	@which brew > /dev/null 2>&1 || \
		(echo "Installing Homebrew..." && \
		/bin/bash -c "$$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)")
	@echo "--- installing Docker Desktop ---"
	@brew list --cask docker > /dev/null 2>&1 && echo "Docker Desktop already installed" || brew install --cask docker
	@echo "--- installing Python 3 ---"
	@brew list python3 > /dev/null 2>&1 && echo "Python 3 already installed" || brew install python3
	@echo "--- installing Node.js (for frontend tests) ---"
	@brew list node > /dev/null 2>&1 && echo "Node.js already installed" || brew install node
	@echo ""
	@echo "All system dependencies installed."
	@echo "Next steps:"
	@echo "  1. Open Docker Desktop from Applications and wait for it to start"
	@echo "  2. make install    # Python virtualenv + packages"
	@echo "  3. make db-up      # start Postgres container"
	@echo "  4. make migrate    # apply schema"
	@echo "  5. make run        # start the app"

install: $(VENV)     ## Create the virtualenv and install dependencies
	$(PIP) install -r requirements.txt

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

run: install         ## Start the app (http://$(HOST):$(PORT))
	KMP_DUPLICATE_LIB_OK=TRUE $(UVICORN) backend.main:app --host $(HOST) --port $(PORT)

start: install db-up ## Start all dependencies (Postgres) then the app (http://$(HOST):$(PORT))
	KMP_DUPLICATE_LIB_OK=TRUE $(UVICORN) backend.main:app --host $(HOST) --port $(PORT)

dev: install         ## Start with auto-reload for development
	KMP_DUPLICATE_LIB_OK=TRUE $(UVICORN) backend.main:app --host $(HOST) --port $(PORT) --reload

test: test-backend test-frontend  ## Run all tests (backend + frontend)

test-backend: install  ## Run the backend test suite (pytest)
	$(PY) -m pytest backend

test-frontend:       ## Run the frontend test suite (node --test, no npm)
	node --test frontend/tests/*.test.mjs

db-up:               ## Start the Postgres container (optional memory features)
	docker compose up -d db

db-down:             ## Stop the Postgres container (keeps the data volume)
	docker compose down

db-logs:             ## Tail the Postgres container logs
	docker compose logs -f db

migrate: install     ## Apply database migrations (needs db-up + QCB_DATABASE_URL)
	$(ALEMBIC) upgrade head

clean:               ## Remove the virtualenv and Python caches
	rm -rf $(VENV)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
