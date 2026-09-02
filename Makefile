PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
export PIP_DISABLE_PIP_VERSION_CHECK := 1

.PHONY: setup run test test-security lint typecheck smoke benchmark verify

setup:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements.lock
	$(BIN)/pip install -e ".[dev,ocr]"

run:
	$(BIN)/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

worker:
	$(BIN)/python -m app.worker

test:
	$(BIN)/pytest -q

test-security:
	$(BIN)/pytest -q tests/security

lint:
	$(BIN)/ruff check app tests scripts/smoke_*.py scripts/benchmark_vps.py

typecheck:
	$(BIN)/mypy app

smoke:
	./scripts/smoke.sh

verify:
	./scripts/verify_vps.sh

benchmark:
	./scripts/benchmark.sh
