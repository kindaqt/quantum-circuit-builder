.PHONY: help install run dev test test-backend test-frontend clean

# Load .env if present (HOST, PORT, QCB_MAX_*) and export to child processes.
ifneq (,$(wildcard .env))
include .env
export
endif

VENV    := .venv
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
HOST    ?= 127.0.0.1
PORT    ?= 8533

help:                ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: $(VENV)     ## Create the virtualenv and install dependencies
	$(PIP) install -r requirements.txt

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

run: install         ## Start the app (http://$(HOST):$(PORT))
	$(UVICORN) backend.main:app --host $(HOST) --port $(PORT)

dev: install         ## Start with auto-reload for development
	$(UVICORN) backend.main:app --host $(HOST) --port $(PORT) --reload

test: test-backend test-frontend  ## Run all tests (backend + frontend)

test-backend: install  ## Run the backend test suite (pytest)
	$(PY) -m pytest backend

test-frontend:       ## Run the frontend test suite (node --test, no npm)
	node --test frontend/tests/*.test.mjs

clean:               ## Remove the virtualenv and Python caches
	rm -rf $(VENV)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
