#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/tanggap60/app}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
EXPECTED_SHA="${EXPECTED_SHA:-${RELEASE_SHA:-}}"
cd "${APP_DIR}"

systemctl is-active --quiet tanggap60-web
systemctl is-active --quiet tanggap60-worker
systemctl is-active --quiet nginx

if [[ -n "${EXPECTED_SHA}" ]]; then
  actual="$(git -C "${APP_DIR}" rev-parse HEAD)"
  if [[ "${actual}" != "${EXPECTED_SHA}" ]]; then
    echo "SHA_MISMATCH expected=${EXPECTED_SHA} actual=${actual}" >&2
    exit 1
  fi
fi
if [[ -n "$(git -C "${APP_DIR}" status --porcelain)" ]]; then
  echo "DIRTY_WORKTREE" >&2
  exit 1
fi
if systemctl is-active --quiet hermes-tunnel || systemctl is-enabled --quiet hermes-tunnel; then
  echo "HERMES_TUNNEL_MUST_BE_INACTIVE" >&2
  exit 1
fi

node --check app/web/static/agent.js
node --check app/web/static/app.js
bash -n scripts/*.sh

BASE_URL="${BASE_URL}" ./scripts/smoke.sh
.venv/bin/pytest -q
.venv/bin/pytest -q tests/security
.venv/bin/ruff check app tests scripts/smoke_*.py scripts/benchmark_vps.py
.venv/bin/mypy app
echo "VERIFY_OK"
