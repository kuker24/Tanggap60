#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tanggap60/app}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
cd "${APP_DIR}"

systemctl is-active --quiet tanggap60-web
systemctl is-active --quiet tanggap60-worker
systemctl is-active --quiet nginx

BASE_URL="${BASE_URL}" ./scripts/smoke.sh
.venv/bin/pytest -q
.venv/bin/pytest -q tests/security
.venv/bin/ruff check app tests
.venv/bin/mypy app
echo "VERIFY_OK"
