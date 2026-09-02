#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BASE="${BASE_URL:-http://127.0.0.1:8000}"
cd "${APP_DIR}"
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi
export TANGGAP60_SOAK="${TANGGAP60_SOAK:-3}"
"${PY}" scripts/benchmark_vps.py "${BASE}"
